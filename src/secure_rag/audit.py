"""Persistent, local request audit trail. Never record credentials or HTTP headers."""

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                at_utc TEXT NOT NULL,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                duration_ms REAL NOT NULL,
                client_ip TEXT,
                actor TEXT NOT NULL,
                details TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS events_at_utc ON events(at_utc)")
        self._db.commit()

    def record(self, *, request_id: str, method: str, path: str, status: int,
               duration_ms: float, client_ip: str | None, actor: str,
               details: dict) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (request_id, datetime.now(UTC).isoformat(), method, path, status,
                 round(duration_ms, 2), client_ip, actor, json.dumps(details, ensure_ascii=False)),
            )

    def close(self) -> None:
        with self._lock:
            self._db.close()
