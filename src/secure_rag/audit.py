"""HMAC-chained local audit events. The signing key must live outside the database."""

import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path


class AuditIntegrityError(RuntimeError):
    pass


class AuditLog:
    def __init__(self, path: Path, key_file: Path):
        key = key_file.read_bytes()
        if len(key) < 32:
            raise ValueError("audit signing key must have at least 32 bytes")
        self._key = key
        self._anchor = path.with_suffix(".anchor.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._broken = False
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                seq INTEGER PRIMARY KEY,
                request_id TEXT NOT NULL,
                phase TEXT NOT NULL,
                at_utc TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER,
                duration_ms REAL,
                client_ip TEXT,
                actor TEXT NOT NULL,
                details TEXT NOT NULL,
                prev_mac TEXT NOT NULL,
                mac TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS audit_request_id ON audit_events(request_id)")
        self._db.commit()
        self._verify_unlocked()
        self._data_version = self._db.execute("PRAGMA data_version").fetchone()[0]

    def _mac(self, fields: tuple) -> str:
        encoded = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, encoded, hashlib.sha256).hexdigest()

    def _verify_unlocked(self) -> tuple[int, str]:
        previous = "0" * 64
        last_seq = 0
        for row in self._db.execute("SELECT * FROM audit_events ORDER BY seq"):
            fields, stored_mac = row[:-1], row[-1]
            if fields[0] != last_seq + 1 or fields[-1] != previous:
                raise AuditIntegrityError("audit sequence or chain is broken")
            if not hmac.compare_digest(self._mac(fields), stored_mac):
                raise AuditIntegrityError(f"audit event {fields[0]} failed verification")
            last_seq, previous = fields[0], stored_mac
        if self._anchor.exists():
            anchor = json.loads(self._anchor.read_text(encoding="utf-8"))
            if anchor != {"seq": last_seq, "mac": previous}:
                raise AuditIntegrityError("audit anchor differs from database head")
        elif last_seq:
            raise AuditIntegrityError("audit anchor is missing")
        return last_seq, previous

    def verify(self) -> tuple[int, str]:
        with self._lock:
            return self._verify_unlocked()

    def _write_anchor(self, seq: int, mac: str) -> None:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._anchor.parent,
                                         delete=False) as file:
            json.dump({"seq": seq, "mac": mac}, file)
            file.flush()
            os.fsync(file.fileno())
            temporary = Path(file.name)
        os.replace(temporary, self._anchor)

    def record(self, *, request_id: str, phase: str, method: str, path: str,
               status: int | None, duration_ms: float | None, client_ip: str | None,
               actor: str, details: dict) -> None:
        with self._lock:
            if self._broken:
                raise AuditIntegrityError("audit anchor update previously failed")
            self._db.execute("BEGIN IMMEDIATE")
            try:
                current_version = self._db.execute("PRAGMA data_version").fetchone()[0]
                if current_version != self._data_version:
                    self._verify_unlocked()
                    self._data_version = current_version
                row = self._db.execute(
                    "SELECT seq, mac FROM audit_events ORDER BY seq DESC LIMIT 1"
                ).fetchone()
                seq, previous = (row[0] + 1, row[1]) if row else (1, "0" * 64)
                fields = (seq, request_id, phase, datetime.now(UTC).isoformat(), method,
                          path, status, round(duration_ms, 2) if duration_ms is not None else None,
                          client_ip, actor, json.dumps(details, ensure_ascii=False), previous)
                mac = self._mac(fields)
                self._db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                                 "?, ?, ?, ?, ?)", (*fields, mac))
                self._db.commit()
            except Exception:
                self._db.rollback()
                raise
            try:
                self._write_anchor(seq, mac)
            except Exception:
                self._broken = True
                raise

    def close(self) -> None:
        with self._lock:
            self._db.close()
