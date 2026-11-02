"""Recent MCP activity, kept in memory, for the Connect tab's connection check.

Someone who has just pasted a server entry into their agent needs to know two
things: did it connect, and what is the agent doing with it. This module keeps
the last few dozen MCP calls (which client, which tool, which collection, when,
whether it failed) so the Connect tab can answer both while they watch.

It is deliberately small and forgetful:

- Metadata only, like the audit trail: never a query, an argument other than
  the collection a call targets, or any text a tool returned.
- In memory and bounded. A restart empties it, and nothing is written to
  disk. This is a live view, not a record. The audit trail
  (MCP_AUDIT_TOOL_CALLS) is the record.
- Always on, because it is how a new user checks their setup, and it holds
  nothing a person allowed to open the Connect tab could not already see.

Calls are captured at the ``/mcp`` ASGI boundary (see
``mcp_server.ToggleableMCPApp``) from the JSON-RPC request body, so every tool,
prompt and resource is covered on both transports without touching any of
them, and in-process calls (the built-in chat) never show up as an agent.

The transport is stateless: ``initialize`` names the client (``clientInfo``),
but the tool calls that follow arrive as separate requests that do not repeat
it. They are linked back to the name by their User-Agent and credential.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict, deque
from typing import Any, Optional

# Calls kept for the Connect tab. Enough to show one agent task end to end.
MAX_ENTRIES = 50

# Client names remembered from `initialize`, keyed by user agent and
# credential. Bounded so a stream of distinct user agents cannot grow it.
MAX_CLIENTS = 200

# Request bodies larger than this are passed through without being read for
# the activity view. A JSON-RPC call is a few hundred bytes; only a large
# write_document upload comes near it, and it is still recorded, unnamed.
MAX_PEEK_BYTES = 256 * 1024

# Longest collection id or name kept per entry.
_MAX_FIELD = 120

# JSON-RPC methods worth showing. Discovery (tools/list and friends) and
# notifications are noise: every client sends them on connect.
_KINDS = {
    "initialize": "connect",
    "tools/call": "tool",
    "prompts/get": "prompt",
    "resources/read": "resource",
}

_lock = threading.Lock()
_entries: deque[dict[str, Any]] = deque(maxlen=MAX_ENTRIES)
_clients: OrderedDict[tuple, str] = OrderedDict()
_seq = 0


def _clip(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    text = str(value)
    return text if len(text) <= _MAX_FIELD else text[: _MAX_FIELD - 1] + "…"


def _agent_from_user_agent(user_agent: str) -> Optional[str]:
    """The product token of a User-Agent ("claude-code/2.1 (…)" → "claude-code")."""
    first = (user_agent or "").strip().split(" ", 1)[0]
    name = first.split("/", 1)[0].strip()
    return _clip(name) if name else None


def parse_calls(body: bytes) -> list[dict[str, Any]]:
    """The calls worth showing in a JSON-RPC request body (one message or a batch).

    Returns ``[{kind, name, collection, client}]``; ``client`` is set only for
    ``initialize``. Anything unparsable yields nothing: the activity view must
    never be the reason a request fails.
    """
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return []
    messages = payload if isinstance(payload, list) else [payload]
    calls = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        kind = _KINDS.get(message.get("method"))
        if not kind:
            continue
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        call: dict[str, Any] = {"kind": kind, "name": None, "collection": None, "client": None}
        if kind == "connect":
            info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
            name = _clip(info.get("name"))
            version = _clip(info.get("version"))
            call["client"] = f"{name} {version}" if name and version else name
        elif kind == "resource":
            call["name"] = _clip(params.get("uri"))
        else:
            call["name"] = _clip(params.get("name"))
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            collection = args.get("collection_id") or args.get("collection")
            if not collection and isinstance(args.get("collection_ids"), list):
                collection = ", ".join(str(c) for c in args["collection_ids"][:3])
            call["collection"] = _clip(collection)
        calls.append(call)
    return calls


def record(
    calls: list[dict[str, Any]],
    *,
    user_agent: str = "",
    identity: Optional[str] = None,
    token_id: Optional[str] = None,
    ok: bool = True,
) -> None:
    """Add the calls from one request. Never raises."""
    global _seq
    if not calls:
        return
    try:
        key = (user_agent or "", identity or "", token_id or "")
        with _lock:
            for call in calls:
                if call["kind"] == "connect" and call.get("client"):
                    _clients[key] = call["client"]
                    _clients.move_to_end(key)
                    while len(_clients) > MAX_CLIENTS:
                        _clients.popitem(last=False)
                client = call.get("client") or _clients.get(key) or _agent_from_user_agent(user_agent)
                _seq += 1
                _entries.append({
                    "seq": _seq,
                    "at": time.time(),
                    "kind": call["kind"],
                    "name": call.get("name"),
                    "collection": call.get("collection"),
                    "client": client,
                    "ok": bool(ok),
                    "via_token": bool(token_id),
                    "_identity": identity,
                })
    except Exception:  # noqa: BLE001 - a live view must never break a call
        pass


def recent(*, after: int = 0, identity: Optional[str] = None, only_own: bool = False) -> dict[str, Any]:
    """Entries newer than ``after``, oldest first, plus the newest sequence number.

    ``only_own`` (private-collections deployments) keeps a person to the calls
    made with their own credentials; on a shared deployment everyone who can
    open the Connect tab sees the whole feed, as they see the whole corpus.
    """
    with _lock:
        latest = _seq
        rows = [dict(e) for e in _entries if e["seq"] > after]
    if only_own:
        rows = [r for r in rows if identity and r["_identity"] == identity]
    for r in rows:
        r.pop("_identity", None)
    return {"entries": rows, "latest": latest}


def clear() -> None:
    """Forget everything (tests)."""
    global _seq
    with _lock:
        _entries.clear()
        _clients.clear()
        _seq = 0
