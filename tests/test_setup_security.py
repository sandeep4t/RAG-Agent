import json

import pytest

from scripts.setup_security import main
from secure_rag.audit import AuditLog
from secure_rag.auth import IdentityStore


def test_setup_creates_usable_private_material_without_overwrite(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    main()
    users = json.loads((tmp_path / "data/api_keys.json").read_text(encoding="utf-8"))["users"]
    store = IdentityStore(tmp_path / "data/api_keys.json")
    assert store.authenticate(users[0]["key"]).name == "demo-reader"
    assert store.authenticate(users[1]["key"]).role == "editor"
    audit = AuditLog(tmp_path / "data/audit/events-v2.sqlite3", tmp_path / "data/audit.key")
    assert audit.verify()[0] == 0
    audit.close()
    with pytest.raises(SystemExit):
        main()
