import pytest
from fastapi import HTTPException

from secure_rag import api
from secure_rag.config import Settings


def test_write_endpoint_requires_key(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(write_key="test-secret"))
    with pytest.raises(HTTPException) as error:
        api.require_write_key(None)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException):
        api.require_write_key("wrong")
    api.require_write_key("test-secret")


def test_empty_key_is_denied(monkeypatch):
    monkeypatch.setattr(api, "get_settings", lambda: Settings(write_key=""))
    with pytest.raises(HTTPException) as error:
        api.require_write_key("anything")
    assert error.value.status_code == 401
