"""D2: who wrote what, and when.

Results carried no timestamp; the author recorded was the person who made
the token, so two agents were one author; list_changes reported folder and git
syncs only; and deep_search left no audit row."""

import asyncio

import services.mcp_server as mcp
from models.schemas import SearchMode
from services import audit, source_changes
from services.clock import utcnow
from tests.test_mcp_write import FakeEmbedder, fresh_db, indexer  # noqa: F401 - fixtures


def _as_token(name, fn, *args, **kwargs):
    token = mcp._request_mcp_token_name.set(name)
    try:
        return fn(*args, **kwargs)
    finally:
        mcp._request_mcp_token_name.reset(token)


def test_two_agents_are_two_authors_and_a_third_sees_what_changed(indexer):  # noqa: F811
    since = source_changes.list_events("default")["latest_id"]
    started = utcnow().isoformat()

    _as_token("planner-agent", mcp.write_document_sync, "plan", "The rollout starts Monday.\n",
                  collection_id="default")
    _as_token("review-agent", mcp.write_document_sync, "review", "The rollout plan looks safe.\n",
              collection_id="default")
    _as_token("planner-agent", mcp.write_document_sync, "plan", "Moved to Tuesday.\n",
              collection_id="default", mode="append")

    events = source_changes.list_events("default", since_id=since)["events"]
    assert [(e["by"], e["added"] or e["changed"]) for e in events] == [
        ("planner-agent", ["plan.md"]), ("review-agent", ["review.md"]), ("planner-agent", ["plan.md"])]
    assert events[2]["changed"] == ["plan.md"] and events[0]["added"] == ["plan.md"]

    hits = indexer.search("rollout", top_k=5, mode=SearchMode.KEYWORD)["results"]
    by_file = {r.filename: r for r in hits}
    assert by_file["review.md"].written_by == "review-agent"
    assert by_file["plan.md"].written_by == "planner-agent"
    assert by_file["plan.md"].written_at >= started[:19]
    serialized = mcp._serialize_result(by_file["review.md"], 1, 500)
    assert serialized["written_by"] == "review-agent" and serialized["written_at"]

    # By name: an append gives the note a new id (work order D4 is about that).
    read = mcp.read_document_sync("plan.md", collection_id="default")
    assert read["written_by"] == "planner-agent"

    row = next(e for e in audit.list_events(action="document.upload") if e["detail"].get("written_by") == "review-agent")
    assert row["detail"]["via"] == "mcp"


def test_deep_search_leaves_an_audit_row(indexer, monkeypatch):  # noqa: F811
    monkeypatch.setattr(mcp.settings, "mcp_audit_tool_calls", True)
    mcp.write_document_sync("notes", "Backups run nightly.\n", collection_id="default")

    asyncio.run(mcp.deep_search("when do backups run", collection_id="default"))

    assert any(e["target"] == "deep_search" for e in audit.list_events(action="mcp.tool_call"))
