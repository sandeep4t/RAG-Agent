import sqlite3
import sys
from types import SimpleNamespace

import pytest

from scripts import audit_report
from secure_rag.audit import AuditIntegrityError, AuditLog


def record(log, request_id, answer):
    log.record(request_id=request_id, phase="complete", method="POST", path="/answer",
               status=200, duration_ms=1.5, client_ip="127.0.0.1", actor="reader-a",
               details={"answer": answer})


def test_hmac_chain_detects_direct_answer_edit_and_tail_deletion(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"k" * 32)
    path = tmp_path / "audit.sqlite3"
    log = AuditLog(path, key_file)
    record(log, "one", "Original [1]")
    record(log, "two", "Second [1]")
    assert log.verify()[0] == 2
    with sqlite3.connect(path) as db:
        db.execute("UPDATE audit_events SET details = ? WHERE seq = 1",
                   ('{"answer":"Changed [1]"}',))
    with pytest.raises(AuditIntegrityError):
        log.verify()
    with pytest.raises(AuditIntegrityError):
        record(log, "three", "Must fail")
    log.close()

    # A separate fresh chain also detects removal of the most recent row via its anchor.
    second = tmp_path / "second.sqlite3"
    log = AuditLog(second, key_file)
    record(log, "one", "Original [1]")
    record(log, "two", "Second [1]")
    with sqlite3.connect(second) as db:
        db.execute("DELETE FROM audit_events WHERE seq = 2")
    with pytest.raises(AuditIntegrityError):
        log.verify()
    log.close()


def test_audit_report_verifies_before_printing(monkeypatch, tmp_path, capsys):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"k" * 32)
    path = tmp_path / "audit.sqlite3"
    log = AuditLog(path, key_file)
    record(log, "one", "Verified answer [1]")
    log.close()
    monkeypatch.setattr(audit_report, "get_settings", lambda: SimpleNamespace(
        audit_db=path, audit_key_file=key_file))
    monkeypatch.setattr(sys, "argv", ["audit_report.py", "--limit", "1"])
    audit_report.main()
    output = capsys.readouterr()
    assert "Verified 1 chained events" in output.err
    assert "Verified answer [1]" in output.out
    with sqlite3.connect(path) as db:
        db.execute("UPDATE audit_events SET details = ? WHERE seq = 1", ('{"answer":"fake"}',))
    with pytest.raises(AuditIntegrityError):
        audit_report.main()
