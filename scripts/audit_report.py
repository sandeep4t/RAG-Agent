"""Print recent local audit events as JSON lines. Treat this output as sensitive."""

import argparse
import json
import sqlite3
import sys

from secure_rag.audit import AuditLog
from secure_rag.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Read recent Secure RAG audit events")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    settings = get_settings()
    path = settings.audit_db
    if not path.is_file():
        parser.error(f"audit database does not exist: {path}")
    log = AuditLog(path, settings.audit_key_file)
    try:
        count, head = log.verify()
    finally:
        log.close()
    print(f"Verified {count} chained events; head {head}", file=sys.stderr)
    if args.verify_only:
        return
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT seq, request_id, phase, at_utc, method, path, status, duration_ms, "
            "client_ip, actor, details, mac FROM audit_events ORDER BY seq DESC LIMIT ?", (args.limit,),
        )
        for row in rows:
            event = dict(zip(("seq", "request_id", "phase", "at_utc", "method", "path",
                              "status", "duration_ms", "client_ip", "actor", "details", "mac"),
                             row))
            event["details"] = json.loads(event["details"])
            print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
