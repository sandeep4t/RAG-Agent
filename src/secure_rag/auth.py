"""Per-person local API keys; never log or return the key itself."""

import hmac
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Identity:
    name: str
    role: str


class IdentityStore:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        users = data["users"]
        if not isinstance(users, list) or not users:
            raise ValueError("at least one API user is required")
        self._users = []
        names = set()
        keys = set()
        for user in users:
            name, role, key = user["id"], user["role"], user["key"]
            if (not isinstance(name, str) or not name or role not in ("reader", "editor")
                    or not isinstance(key, str) or len(key) < 24
                    or name in names or key in keys):
                raise ValueError("invalid or duplicate API user")
            names.add(name)
            keys.add(key)
            self._users.append((Identity(name, role), key))

    def authenticate(self, key: str | None) -> Identity | None:
        if not key:
            return None
        for identity, expected in self._users:
            if hmac.compare_digest(key, expected):
                return identity
        return None
