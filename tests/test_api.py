import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from secure_rag import api
from secure_rag.audit import AuditLog
from secure_rag.auth import IdentityStore
from secure_rag.config import Settings
from secure_rag.rag import Source


def test_api_audit_and_individual_attribution(monkeypatch, tmp_path):
    key_file = tmp_path / "audit.key"
    key_file.write_bytes(b"a" * 32)
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps({"users": [
        {"id": "reader-a", "role": "reader", "key": "r" * 32},
        {"id": "demo-editor", "role": "editor", "key": "e" * 32},
    ]}), encoding="utf-8")
    audit = AuditLog(tmp_path / "audit.sqlite3", key_file)
    monkeypatch.setattr(api, "audit_log", lambda: audit)
    monkeypatch.setattr(api, "identity_store", lambda: IdentityStore(users_file))
    monkeypatch.setattr(api, "get_settings", lambda: Settings())
    saved = []

    class FakeService:
        store = SimpleNamespace(put=lambda post: saved.append(post))

        def index(self, post):
            pass

        def search(self, query, limit, trace=None):
            if trace is not None:
                trace["retrieval"] = {"candidates": [{"verified": True}]}
            return [Source("one", "Title", "demo-editor", "Evidence text", 0.5)]

        def answer(self, query, insight=False, limit=5, trace=None):
            sources = self.search(query, limit, trace)
            if trace is not None:
                trace["generation"] = {"requested_model": "test-model", "attempts": []}
            return "“Evidence text” [1]", sources

    monkeypatch.setattr(api, "service", lambda: FakeService())
    with TestClient(api.app) as client:
        reader = {"X-API-Key": "r" * 32}
        editor = {"X-API-Key": "e" * 32}
        response = client.post("/answer", headers=reader, json={"query": "What happened?"})
        assert response.status_code == 200
        assert response.headers["x-request-id"]
        assert "rag_http_requests_total" in client.get("/metrics").text
        post = {"id": "one", "title": "T", "author": "demo-editor", "body": "B"}
        assert client.put("/posts/one", headers=editor, json=post).status_code == 200
        assert client.put("/posts/one", headers=editor,
                          json={**post, "author": "someone-else"}).status_code == 403
        assert client.put("/posts/one", headers=reader, json=post).status_code == 403
        assert client.put("/posts/one", headers={"X-API-Key": "wrong"},
                          json=post).status_code == 401
        assert client.post("/answer", headers=reader, json={"query": ""}).status_code == 422
        assert client.post("/answer", json={"query": "Anonymous"}).status_code == 401

        class BrokenService:
            def search(self, query, limit, trace=None):
                raise RuntimeError("backend unavailable")

        monkeypatch.setattr(api, "service", lambda: BrokenService())
        failed = client.post("/search", headers=reader, json={"query": "Check failure"})
        assert failed.status_code == 500
        assert failed.headers["x-request-id"]
    count, _ = audit.verify()
    assert count == 19  # Nine requests, plus the pre-mutation intent.
    with sqlite3.connect(tmp_path / "audit.sqlite3") as db:
        rows = db.execute("SELECT phase, request_id, status, actor, details FROM audit_events "
                          "ORDER BY seq").fetchall()
    completed = [row for row in rows if row[0] == "complete"]
    assert completed[0][1:4] == (response.headers["x-request-id"], 200, "reader-a")
    assert json.loads(completed[0][4])["trace"]["generation"]["requested_model"] == "test-model"
    assert [row[0] for row in rows].count("intent") == 1
    assert any(row[3] == "demo-editor" and json.loads(row[4]).get("post", {}).get("body") == "B"
               for row in rows)
    assert any(row[2] == 403 and row[3] == "reader-a" for row in completed)
    assert any(row[2] == 401 and row[3] == "anonymous" for row in completed)
    assert "wrong" not in str(rows)
    assert "r" * 32 not in str(rows)
    assert len(saved) == 1
    audit.close()


def test_audit_completion_failure_blocks_success_response(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"k" * 32)
    audit = AuditLog(tmp_path / "audit.sqlite3", key_file)
    original = audit.record

    def fail_completion(**kwargs):
        if kwargs["phase"] == "complete":
            raise OSError("disk unavailable")
        return original(**kwargs)

    monkeypatch.setattr(audit, "record", fail_completion)
    monkeypatch.setattr(api, "audit_log", lambda: audit)
    with TestClient(api.app) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert audit.verify()[0] == 1  # The durable start record remains.
    audit.close()


def test_post_intent_precedes_mutation_and_survives_completion_failure(monkeypatch, tmp_path):
    key_file = tmp_path / "key"
    key_file.write_bytes(b"k" * 32)
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps({"users": [
        {"id": "demo-editor", "role": "editor", "key": "e" * 32},
    ]}), encoding="utf-8")
    audit = AuditLog(tmp_path / "audit.sqlite3", key_file)
    original = audit.record
    mutations = []

    def record_or_fail(**kwargs):
        if kwargs["phase"] == "complete":
            raise OSError("disk unavailable")
        return original(**kwargs)

    def put_after_intent(post):
        assert audit.verify()[0] == 2
        mutations.append(post.id)

    class FakeService:
        store = SimpleNamespace(put=put_after_intent)

        def index(self, post):
            pass

    monkeypatch.setattr(audit, "record", record_or_fail)
    monkeypatch.setattr(api, "audit_log", lambda: audit)
    monkeypatch.setattr(api, "identity_store", lambda: IdentityStore(users_file))
    monkeypatch.setattr(api, "service", lambda: FakeService())
    with TestClient(api.app) as client:
        response = client.put("/posts/one", headers={"X-API-Key": "e" * 32},
                              json={"id": "one", "title": "Title", "author": "demo-editor",
                                    "body": "Body"})
    assert response.status_code == 503
    assert mutations == ["one"]
    assert audit.verify()[0] == 2
    with sqlite3.connect(tmp_path / "audit.sqlite3") as db:
        events = db.execute("SELECT phase, actor, details FROM audit_events ORDER BY seq").fetchall()
    assert events[1][0:2] == ("intent", "demo-editor")
    assert json.loads(events[1][2])["post"]["body"] == "Body"
    audit.close()
