"""D1: a small notes profile, typed filters, and an agent searching its own notes.

An agent loaded 32 tools and about 66,000 characters of tool description with
no way to load fewer; a read-only token still listed the write tools; and a
misspelled filter key was dropped, so the search ran unfiltered."""

import asyncio
import json

import pytest

import services.mcp_server as mcp
from models.schemas import SearchMode
from tests.test_mcp_write import FakeEmbedder, fresh_db, indexer  # noqa: F401 - fixtures


def _list(profile=None, can_write=True):
    tools_token = mcp._request_mcp_tools.set(profile)
    write_token = mcp._request_mcp_can_write.set(can_write)
    try:
        return asyncio.run(mcp._listed_tools()).tools
    finally:
        mcp._request_mcp_tools.reset(tools_token)
        mcp._request_mcp_can_write.reset(write_token)


def _size(tools) -> int:
    return len(json.dumps([t.model_dump(exclude_none=True, mode="json") for t in tools]))


def test_the_notes_profile_lists_its_tools_in_under_4000_tokens():
    tools = _list("notes")

    assert [t.name for t in tools] == list(mcp.TOOL_PROFILES["notes"])
    # About four characters to a token for JSON and English.
    assert _size(tools) / 4 < 4000, _size(tools)
    assert _size(tools) < _size(_list()) / 3


def test_every_profile_names_real_tools():
    names = {t.name for t in asyncio.run(mcp._clio_mcp.list_tools())}
    for profile, wanted in mcp.TOOL_PROFILES.items():
        assert set(wanted) <= names, profile


def test_a_read_only_credential_never_lists_a_write_tool():
    for profile in (None, "notes", "core"):
        tools = _list(profile, can_write=False)
        assert tools and all(t.annotations.readOnlyHint for t in tools), profile
    assert "write_document" not in {t.name for t in _list("notes", can_write=False)}


def test_no_profile_lists_every_tool_as_before():
    assert len(_list()) == len(asyncio.run(mcp._clio_mcp.list_tools()))


def test_a_misspelled_filter_is_an_error_that_names_the_valid_keys():
    with pytest.raises(ValueError) as e:
        mcp._normalize_search_filters({"source_typ": "agent_note"})
    assert "source_typ" in str(e.value) and "source_type" in str(e.value) and "written_after" in str(e.value)


def test_note_filters_reach_the_index():
    out = mcp._normalize_search_filters({"source_type": "agent_note", "written_after": "2026-11-18T00:00:00"})
    assert out == {"source_types": ["agent_note"], "date_from": "2026-11-18T00:00:00"}


def test_an_agent_can_search_only_its_own_notes(indexer, tmp_path):  # noqa: F811
    source = tmp_path / "backups.md"
    source.write_text("# Backups\n\nBackups run nightly at 02:00 in the source runbook.\n", encoding="utf-8")
    indexer.index_document(source, "backups.md", collection_id="default")
    note = mcp.write_document_sync("backup-notes", "Backups run nightly at 02:00, said the agent.\n",
                                   collection_id="default")

    everything = indexer.search("when do backups run", top_k=5, mode=SearchMode.KEYWORD)["results"]
    notes = indexer.search("when do backups run", top_k=5, mode=SearchMode.KEYWORD,
                           filters=mcp._normalize_search_filters({"source_type": "agent_note"}))["results"]
    later = indexer.search("when do backups run", top_k=5, mode=SearchMode.KEYWORD,
                           filters=mcp._normalize_search_filters({"written_after": "2999-01-01"}))["results"]

    assert {r.filename for r in everything} == {"backups.md", "backup-notes.md"}
    assert {r.document_id for r in notes} == {note["document_id"]}
    assert later == []
