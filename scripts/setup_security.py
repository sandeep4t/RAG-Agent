"""One-time generation of local reader/editor credentials and audit signing key."""

import json
import os
import secrets
from pathlib import Path


def write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        file.write(content)
    os.chmod(path, 0o600)


def main() -> None:
    users_path = Path("data/api_keys.json")
    key_path = Path("data/audit.key")
    if users_path.exists() or key_path.exists():
        raise SystemExit("Security files already exist; refusing to overwrite them")
    users = {"users": [
        {"id": "demo-reader", "role": "reader", "key": secrets.token_urlsafe(32)},
        {"id": "demo-editor", "role": "editor", "key": secrets.token_urlsafe(32)},
    ]}
    write_private(users_path, json.dumps(users, indent=2) + "\n")
    write_private(key_path, secrets.token_hex(32) + "\n")
    print(f"Created {users_path} and {key_path}. Keep both private and back them up securely.")


if __name__ == "__main__":
    main()
