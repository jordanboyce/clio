"""The Connect tab's live activity view: what is recorded, and what is not."""

from __future__ import annotations

import asyncio
import json

import pytest

from services import mcp_activity


@pytest.fixture(autouse=True)
def _fresh():
    mcp_activity.clear()
    yield
    mcp_activity.clear()


def _rpc(method, params=None, id_=1):
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


# ── Parsing ─────────────────────────────────────────────────────────────

def test_parse_keeps_tool_and_collection_but_never_the_query():
    body = json.dumps(_rpc("tools/call", {
        "name": "search_collection",
        "arguments": {"query": "salary of the director", "collection_id": "ops"},
    })).encode()
    [call] = mcp_activity.parse_calls(body)
    assert call == {"kind": "tool", "name": "search_collection", "collection": "ops", "client": None}
    assert "salary" not in json.dumps(call)


def test_parse_names_the_client_on_initialize_and_skips_discovery_noise():
    batch = [
        _rpc("initialize", {"clientInfo": {"name": "claude-code", "version": "2.1.0"}}),
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        _rpc("tools/list", id_=2),
        _rpc("prompts/get", {"name": "try_clio"}, id_=3),
        _rpc("tools/call", {"name": "research_documents",
                            "arguments": {"query": "x", "collection_ids": ["a", "b"]}}, id_=4),
    ]
    calls = mcp_activity.parse_calls(json.dumps(batch).encode())
    assert [c["kind"] for c in calls] == ["connect", "prompt", "tool"]
    assert calls[0]["client"] == "claude-code 2.1.0"
    assert calls[2]["collection"] == "a, b"


@pytest.mark.parametrize("body", [b"", b"not json", b"[1, 2]", b'{"method": 5}', b"\xff\xfe"])
def test_parse_never_raises(body):
    assert mcp_activity.parse_calls(body) == []


# ── Recording and reading ───────────────────────────────────────────────

def test_tool_calls_inherit_the_client_name_from_initialize():
    ua = "claude-code/2.1.0 (cli)"
    mcp_activity.record(mcp_activity.parse_calls(json.dumps(
        _rpc("initialize", {"clientInfo": {"name": "claude-code", "version": "2.1.0"}})).encode()),
        user_agent=ua)
    mcp_activity.record([{"kind": "tool", "name": "list_collections", "collection": None, "client": None}],
                        user_agent=ua)
    mcp_activity.record([{"kind": "tool", "name": "health_check", "collection": None, "client": None}],
                        user_agent="python-httpx/0.28")
    entries = mcp_activity.recent()["entries"]
    assert [e["client"] for e in entries] == ["claude-code 2.1.0", "claude-code 2.1.0", "python-httpx"]


def test_recent_is_incremental_and_bounded():
    for i in range(mcp_activity.MAX_ENTRIES + 10):
        mcp_activity.record([{"kind": "tool", "name": f"t{i}", "collection": None, "client": None}])
    feed = mcp_activity.recent()
    assert len(feed["entries"]) == mcp_activity.MAX_ENTRIES
    assert feed["latest"] == mcp_activity.MAX_ENTRIES + 10
    assert mcp_activity.recent(after=feed["latest"] - 2)["entries"][0]["name"] == f"t{mcp_activity.MAX_ENTRIES + 8}"


def test_private_mode_shows_only_your_own_calls():
    call = [{"kind": "tool", "name": "search_collection", "collection": None, "client": None}]
    mcp_activity.record(call, identity="alice")
    mcp_activity.record(call, identity="bob")
    mcp_activity.record(call, identity=None)
    assert len(mcp_activity.recent(identity="alice", only_own=True)["entries"]) == 1
    assert mcp_activity.recent(identity=None, only_own=True)["entries"] == []
    shared = mcp_activity.recent()["entries"]
    assert len(shared) == 3 and all("_identity" not in e for e in shared)


# ── The /mcp wrapper ────────────────────────────────────────────────────

def _drive(app, body: bytes, *, chunks=1, headers=None, state=None):
    """Run one POST through an ASGI app; return what it sent back."""
    size = max(1, -(-len(body) // chunks))
    parts = [body[i:i + size] for i in range(0, len(body), size)] or [b""]
    incoming = [{"type": "http.request", "body": p, "more_body": i < len(parts) - 1}
                for i, p in enumerate(parts)]
    sent = []

    async def receive():
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/", "query_string": b"",
             "headers": headers or [], "state": state or {}}
    asyncio.run(app(scope, receive, send))
    return sent


def _echo_app(result: bytes, status: int = 200):
    """An inner app that reads the whole body, then answers with `result`."""
    seen = {}

    async def app(scope, receive, send):
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        seen["body"] = body
        await send({"type": "http.response.start", "status": status, "headers": []})
        await send({"type": "http.response.body", "body": result})

    return app, seen


def test_wrapper_replays_the_body_untouched_and_records_the_call():
    from services.mcp_server import ToggleableMCPApp

    body = json.dumps(_rpc("tools/call", {"name": "health_check", "arguments": {}})).encode()
    inner, seen = _echo_app(b'{"jsonrpc":"2.0","id":1,"result":{"content":[],"isError":false}}')
    _drive(ToggleableMCPApp(inner), body, chunks=3,
           headers=[(b"user-agent", b"claude-code/2.1.0")], state={"mcp_token_id": "tok1"})

    assert seen["body"] == body
    [entry] = mcp_activity.recent()["entries"]
    assert entry["name"] == "health_check" and entry["ok"] is True
    assert entry["client"] == "claude-code" and entry["via_token"] is True


@pytest.mark.parametrize("result,status,ok", [
    (b'{"result":{"content":[],"isError":true}}', 200, False),
    (b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad"}}', 200, False),
    (b'{"detail":"nope"}', 500, False),
    # The flag inside a tool's own text output is JSON-escaped and must not count.
    (b'{"result":{"content":[{"type":"text","text":"{\\"isError\\":true}"}],"isError":false}}', 200, True),
])
def test_wrapper_reads_the_outcome(result, status, ok):
    from services.mcp_server import ToggleableMCPApp

    body = json.dumps(_rpc("tools/call", {"name": "query_table", "arguments": {}})).encode()
    inner, _ = _echo_app(result, status)
    _drive(ToggleableMCPApp(inner), body)
    assert mcp_activity.recent()["entries"][0]["ok"] is ok


def test_wrapper_passes_oversized_bodies_through_unrecorded(monkeypatch):
    from services.mcp_server import ToggleableMCPApp

    monkeypatch.setattr(mcp_activity, "MAX_PEEK_BYTES", 64)
    body = json.dumps(_rpc("tools/call", {"name": "write_document",
                                          "arguments": {"content": "x" * 500}})).encode()
    inner, seen = _echo_app(b"{}")
    _drive(ToggleableMCPApp(inner), body, chunks=10)
    assert seen["body"] == body
    assert mcp_activity.recent()["entries"] == []


# ── try_clio ────────────────────────────────────────────────────────────

def test_try_clio_audits_the_sample_set_when_it_is_there(monkeypatch):
    from services import mcp_server as mcp

    monkeypatch.setattr(mcp, "_visible_collections", lambda: [
        {"id": "default", "name": "Default"},
        {"id": "c0ffee", "name": mcp.SAMPLE_COLLECTION_NAME},
    ])
    text = asyncio.run(mcp._clio_mcp.get_prompt("try_clio", {})).messages[0].content.text
    assert '"c0ffee"' in text and "query_table" in text and "research_documents" in text
    assert "cannot settle" in text


def test_try_clio_tours_any_other_server(monkeypatch):
    from services import mcp_server as mcp

    monkeypatch.setattr(mcp, "_visible_collections", lambda: [{"id": "default", "name": "Default"}])
    text = asyncio.run(mcp._clio_mcp.get_prompt("try_clio", {})).messages[0].content.text
    assert "list_collections" in text and "research_documents" in text
    assert mcp.SAMPLE_COLLECTION_NAME not in text
