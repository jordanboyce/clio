"""Both MCP transports are mounted under /mcp and share the app's gates."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import services.mcp_server as mcp


def _paths(app):
    return [getattr(r, "path", None) for r in app.router.routes]


def test_legacy_sse_routes_sit_before_the_streamable_catch_all():
    paths = _paths(mcp.embedded_mcp_app.app)
    assert paths[:2] == ["/sse", "/sse/messages"]
    assert "/" in paths[2:]


@pytest.fixture(scope="module")
def client():
    import main

    # The context manager runs the lifespan (the streamable session manager
    # only answers inside it, and may only be started once per process, hence
    # one client for the module); localhost as the Host keeps the transport's
    # DNS-rebinding protection happy, as it would be for a real client.
    with TestClient(main.app, base_url="http://localhost") as c:
        yield c


def test_bare_mcp_path_is_served_without_a_redirect(client):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "t", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream"}
    for path in ("/mcp", "/mcp/", "/mcp?collection_id=default"):
        r = client.post(path, json=body, headers=headers)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
        assert r.json()["result"]["serverInfo"]["name"] == "Clio"
        assert not r.history  # no 307 on the way


def test_sse_message_endpoint_rejects_unknown_sessions(client):
    # The SSE transport's POST side answers without a stream: no session id
    # is a 400, an unknown one a 404. Either proves the route is live and
    # reached through the same /mcp prefix the auth middleware guards.
    r = client.post("/mcp/sse/messages/", content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 400
    r = client.post("/mcp/sse/messages/?session_id=0123456789abcdef0123456789abcdef",
                    content=b"{}", headers={"Content-Type": "application/json"})
    assert r.status_code == 404


def test_sse_routes_keep_dns_rebinding_protection(client):
    # The same allowlist as streamable HTTP: an unexpected Host is refused
    # with 421 before any session logic runs.
    r = client.post("/mcp/sse/messages/?session_id=0123456789abcdef0123456789abcdef",
                    content=b"{}", headers={"Content-Type": "application/json", "Host": "evil.example"})
    assert r.status_code == 421
