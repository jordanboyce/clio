"""R9 renamed what "research" meant: the Research tab is the Library, Ask's
Research depth is Deep search ("deep"), and two settings pairs took names
that say what they limit. Every old spelling still works for one release.

The MCP tool rename is covered in test_research_search.py, ask_documents in
test_mcp_ask.py, the Library's REST paths in test_research.py.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import config
from models.schemas import ChatMessage, ChatRequest, normalize_depth


# ── depth ───────────────────────────────────────────────────────────────

def _request(**kw):
    return ChatRequest(messages=[ChatMessage(role="user", content="Q")], provider="ollama", **kw)


def test_research_is_read_as_deep_and_deep_is_the_default():
    assert normalize_depth("research") == "deep"
    assert _request().depth == "deep"
    assert _request(depth="research").depth == "deep"
    assert _request(depth="deep").depth == "deep"
    assert _request(depth="quick").depth == "quick"
    with pytest.raises(ValueError):
        _request(depth="thorough")


def test_both_spellings_of_deep_share_one_cache_entry_and_quick_never_does(monkeypatch):
    from api import chat

    monkeypatch.setattr(chat, "_corpus_version", lambda cid: "v1")
    monkeypatch.setattr(chat, "_collection_guidance_version", lambda cid: "g1")
    key = lambda depth: chat._request_fingerprint(_request(depth=depth), ["c1"], "m")  # noqa: E731
    assert key("deep") == key("research")
    assert key("quick") != key("deep")
    # Deep search is keyed as it was before the rename, so answers cached by
    # the previous release are still found.
    assert chat._cache_depth_token(_request(depth="deep")) == "research"
    assert chat._cache_depth_token(_request(depth="quick")) == "quick"


def test_api_v1_accepts_deep_and_still_accepts_research():
    from api.v1 import AskRequest

    assert AskRequest(question="q", depth="deep").depth == "deep"
    assert AskRequest(question="q", depth="research").depth == "research"
    assert AskRequest(question="q").depth == "quick"


# ── the in-app agent ────────────────────────────────────────────────────

def test_the_agent_advertises_deep_search_and_still_runs_the_old_name(monkeypatch):
    from services import mcp_server, structured_chat
    from services.agent_tools import tool_names

    assert "deep_search" in tool_names() and "research_documents" not in tool_names()
    calls = []
    monkeypatch.setattr(mcp_server, "deep_search_sync",
                        lambda **kw: calls.append(kw) or {"collection_id": "ops", "results": []})
    out = structured_chat.execute_tool_calls(
        [{"tool": "research_documents", "query": "EQ-17"}, {"tool": "deep_search", "query": "EQ-17"}],
        {"collection_id": "ops"})
    assert [r["tool"] for r in out] == ["deep_search", "deep_search"]
    assert all("error" not in r for r in out) and len(calls) == 2


# ── settings ────────────────────────────────────────────────────────────

RENAMED = [
    ("deep_search_max_concurrent", "DEEP_SEARCH_MAX_CONCURRENT", "RESEARCH_MAX_CONCURRENT", 5),
    ("deep_search_queue_timeout_seconds", "DEEP_SEARCH_QUEUE_TIMEOUT_SECONDS", "RESEARCH_QUEUE_TIMEOUT_SECONDS", 9.0),
    ("library_max_concurrent_jobs", "LIBRARY_MAX_CONCURRENT_JOBS", "RESEARCH_MAX_CONCURRENT_JOBS", 4),
    ("library_max_job_documents", "LIBRARY_MAX_JOB_DOCUMENTS", "RESEARCH_MAX_JOB_DOCUMENTS", 30),
]


@pytest.fixture()
def clean_env(monkeypatch, tmp_path):
    for _, new, old, _ in RENAMED:
        monkeypatch.delenv(new, raising=False)
        monkeypatch.delenv(old, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.mark.parametrize("field,new,old,value", RENAMED)
def test_old_setting_name_is_read_and_the_new_one_wins(clean_env, monkeypatch, field, new, old, value):
    default = config.Settings.model_fields[field].default
    assert getattr(config.Settings(), field) == default
    monkeypatch.setenv(old, str(value))
    assert getattr(config.Settings(), field) == value
    assert (old, new) in config.legacy_setting_names(env_file=clean_env / "none.env")
    monkeypatch.setenv(new, str(value * 2))
    assert getattr(config.Settings(), field) == value * 2
    assert (old, new) not in config.legacy_setting_names(env_file=clean_env / "none.env")


def test_old_names_in_the_env_file_are_found_too(clean_env):
    env_file = clean_env / ".env"
    env_file.write_text("RESEARCH_MAX_CONCURRENT_JOBS=2\nLIBRARY_MAX_JOB_DOCUMENTS=10\n", encoding="utf-8")
    assert config.legacy_setting_names(env_file=env_file) == [
        ("RESEARCH_MAX_CONCURRENT_JOBS", "LIBRARY_MAX_CONCURRENT_JOBS")]


def test_startup_names_each_old_setting_in_use(clean_env, monkeypatch, caplog):
    import main

    monkeypatch.setenv("RESEARCH_MAX_CONCURRENT", "2")
    monkeypatch.setattr(config, "legacy_setting_names",
                        lambda env_file=None, _real=config.legacy_setting_names: _real(clean_env / "none.env"))
    with caplog.at_level(logging.WARNING, logger=main.logger.name):
        main._log_renamed_settings()
    assert any("RESEARCH_MAX_CONCURRENT is the old name of DEEP_SEARCH_MAX_CONCURRENT" in r.getMessage()
               for r in caplog.records)


# ── the Features toggle ─────────────────────────────────────────────────

def test_the_lookups_feature_is_called_catalogue_lookups(monkeypatch):
    from services import feature_status as fs

    report = fs._research()
    assert report["id"] == "research"          # saved settings and the form key on it
    assert report["label"] == "Catalogue lookups"
