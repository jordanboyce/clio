"""Regression tests for cached-source access and MCP credential boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from api import chat, mcp
from services import mcp_server


@pytest.mark.parametrize("unavailable", ["quarantined", "deleted", "revoked"])
def test_cached_document_must_still_be_readable(monkeypatch, unavailable):
    info = {"filename": "private.md", "num_chunks": 2, "policy_status": "clear"}
    if unavailable == "quarantined":
        info["policy_status"] = "quarantined"
    if unavailable == "deleted":
        info = None
    getter = Mock(return_value=SimpleNamespace(vector_store=SimpleNamespace(
        metadata_store=SimpleNamespace(get_document_info=lambda did: info))))
    if unavailable == "revoked":
        getter.side_effect = HTTPException(404, "Not found")
    monkeypatch.setattr(chat, "get_indexer", getter)
    assert chat._document_fingerprint("private", "doc") is None
    getter.assert_called_once_with("private")


def test_cached_fingerprint_tracks_content_and_effective_label(monkeypatch):
    info = {"filename": "policy.md", "content_hash": "revision-1", "sensitivity": None}
    monkeypatch.setattr(chat, "get_indexer", lambda cid: SimpleNamespace(
        vector_store=SimpleNamespace(metadata_store=SimpleNamespace(get_document_info=lambda did: info))))
    monkeypatch.setattr(chat.collection_service, "get_collection", lambda cid: {"sensitivity": "confidential"})
    first = chat._document_fingerprint("private", "doc")
    assert first["sensitivity"] == "confidential"
    info["content_hash"] = "revision-2"
    assert chat._document_fingerprint("private", "doc") != first


def test_mcp_tools_advertise_read_and_write_semantics():
    tools = asyncio.run(mcp_server._clio_mcp.list_tools())
    assert tools
    writers = {"write_document", "reindex_document", "update_document_metadata"}
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is (tool.name not in writers)
    write = next(tool for tool in tools if tool.name == "write_document")
    assert write.annotations.destructiveHint is True
    assert write.annotations.idempotentHint is False  # append is not idempotent


@pytest.mark.parametrize("value", ["false", "true", 1, None])
def test_token_api_rejects_ambiguous_write_grants(value):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(mcp.router)
    response = TestClient(app).post('/api/mcp/tokens', json={"can_write": value})
    assert response.status_code == 422


def test_mcp_configuration_requires_operator(monkeypatch):
    gate = Mock(side_effect=HTTPException(403, "Only an admin"))
    update = Mock()
    monkeypatch.setattr(mcp, "require_admin", gate)
    monkeypatch.setattr(mcp.config_manager, "update_config", update)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(mcp.update_mcp_config({"enable_mcp": False}))
    assert exc.value.status_code == 403
    update.assert_not_called()


@pytest.mark.parametrize("path,valid,expected", [
    ("/mcp/", True, 200), ("/mcp/", False, 401), ("/mcp-else", True, 401),
    ("/api/mcp/config", True, 401),
])
def test_open_deployment_still_honors_explicit_mcp_tokens(monkeypatch, path, valid, expected):
    import main
    from services import mcp_tokens
    monkeypatch.setattr(main.settings, "auth_password", "")
    monkeypatch.setattr(main.settings, "private_collections", False)
    monkeypatch.setattr(mcp_tokens, "verify_token", lambda token:
        {"user_id": "alice", "can_write": False} if valid else None)
    request = Request({"type": "http", "method": "POST", "path": path,
                       "headers": [(b"authorization", b"Bearer asy_mcp_test")], "query_string": b""})

    async def downstream(req):
        assert req.state.mcp_token_can_write is False
        return SimpleNamespace(status_code=200)

    response = asyncio.run(main.require_auth(request, downstream))
    assert response.status_code == expected
