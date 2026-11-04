import json
import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from secure_rag import api
from secure_rag.audit import AuditLog
from secure_rag.config import Settings
from secure_rag.rag import Source


def test_write_endpoint_requires_key(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(write_key="test-secret"))
    class Request:
        def __init__(self):
            self.state = SimpleNamespace()

    request = Request()
    with pytest.raises(HTTPException) as error:
        api.require_write_key(request, None)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException):
        api.require_write_key(request, "wrong")
    api.require_write_key(request, "test-secret")
    assert request.state.audit_actor == "shared_write_key"


def test_empty_key_is_denied(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(write_key=""))
    with pytest.raises(HTTPException) as error:
        api.require_write_key(type("Request", (), {})(), "anything")
    assert error.value.status_code == 401


def test_api_audit_captures_interactions_without_credentials(monkeypatch, tmp_path):
    audit = AuditLog(tmp_path / "audit.sqlite3")
    monkeypatch.setattr(api, "audit_log", lambda: audit)
    monkeypatch.setattr(api, "get_settings", lambda: Settings(write_key="test-secret"))

    class FakeService:
        store = SimpleNamespace(put=lambda post: None)

        def index(self, post):
            pass

        def search(self, query, limit):
            return [Source("one", "Title", "demo-editor", "Evidence", 0.5)]

        def answer(self, query, insight=False, limit=5):
            return "Supported answer [1]", self.search(query, limit)

    monkeypatch.setattr(api, "service", lambda: FakeService())
    with TestClient(api.app) as client:
        response = client.post("/answer", json={"query": "What happened?"})
        assert response.status_code == 200
        assert response.headers["x-request-id"]
        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert "rag_http_requests_total" in metrics.text
        assert client.put("/posts/one", headers={"X-API-Key": "test-secret"},
                          json={"id": "one", "title": "T", "author": "demo-editor",
                                "body": "B"}).status_code == 200
        assert client.put("/posts/one", headers={"X-API-Key": "wrong"},
                          json={"id": "one", "title": "T", "author": "demo-editor",
                                "body": "B"}).status_code == 401
        assert client.post("/answer", json={"query": ""}).status_code == 422
        class BrokenService:
            def search(self, query, limit):
                raise RuntimeError("backend unavailable")

        monkeypatch.setattr(api, "service", lambda: BrokenService())
        failed = client.post("/search", json={"query": "Check failure"})
        assert failed.status_code == 500
        assert failed.headers["x-request-id"]
    with sqlite3.connect(tmp_path / "audit.sqlite3") as db:
        events = db.execute("SELECT id, status, actor, details FROM events ORDER BY rowid").fetchall()
    assert len(events) == 6
    assert events[0][0] == response.headers["x-request-id"]
    assert json.loads(events[0][3])["answer"] == "Supported answer [1]"
    assert events[2][1:3] == (200, "shared_write_key")
    assert json.loads(events[2][3])["post"]["body"] == "B"
    assert events[3][1:3] == (401, "anonymous")
    assert events[4][1] == 422
    assert events[5][1] == 500
    assert json.loads(events[5][3])["query"] == "Check failure"
    assert "wrong" not in str(events)
    assert "test-secret" not in str(events)
    audit.close()
