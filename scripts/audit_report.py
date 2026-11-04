"""Print recent local audit events as JSON lines. Treat this output as sensitive."""

import argparse
import json
import sqlite3

from secure_rag.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Read recent Secure RAG audit events")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    path = get_settings().audit_db
    if not path.is_file():
        parser.error(f"audit database does not exist: {path}")
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT id, at_utc, method, path, status, duration_ms, client_ip, actor, details "
            "FROM events ORDER BY rowid DESC LIMIT ?", (args.limit,),
        )
        for row in rows:
            event = dict(zip(("id", "at_utc", "method", "path", "status", "duration_ms",
                              "client_ip", "actor", "details"), row))
            event["details"] = json.loads(event["details"])
            print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    main()
