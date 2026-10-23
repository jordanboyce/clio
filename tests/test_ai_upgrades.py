"""Tests for the reranker, new AI providers, and regex find_in_documents."""

import re

import pytest

from services.reranker import CrossEncoderReranker, _default_text_getter, get_reranker
from services import ai_service
from services.mcp_server import _find_regex_excerpt, _find_literal_excerpt


# ---------------------------------------------------------------------------
# Cross-encoder reranker
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, text):
        self.text_snippet = text


class _FakeModel:
    """Stand-in for sentence_transformers.CrossEncoder.

    Scores each (query, text) pair by counting query-token occurrences, so the
    "most relevant" text deterministically sorts first.
    """

    def predict(self, pairs, batch_size=None):
        scores = []
        for query, text in pairs:
            q_tokens = query.lower().split()
            scores.append(sum(text.lower().count(t) for t in q_tokens))
        return scores


def test_default_text_getter_handles_objects_and_dicts():
    assert _default_text_getter(_Result("hello")) == "hello"
    assert _default_text_getter({"text_snippet": "a"}) == "a"
    assert _default_text_getter({"excerpt": "b"}) == "b"
    assert _default_text_getter({}) == ""


def test_reranker_reorders_by_cross_encoder_score():
    rr = CrossEncoderReranker("fake-model")
    rr._model = _FakeModel()  # inject, skip the real load

    results = [
        _Result("nothing relevant here"),
        _Result("apple apple apple"),
        _Result("one apple"),
    ]
    ranked = rr.rerank("apple", results, top_k=2)

    assert len(ranked) == 2
    assert ranked[0].text_snippet == "apple apple apple"
    assert ranked[1].text_snippet == "one apple"


def test_reranker_degrades_when_model_unavailable():
    rr = CrossEncoderReranker("does-not-exist")
    rr._load_failed = True  # simulate a failed/absent model load

    results = [_Result("a"), _Result("b"), _Result("c")]
    ranked = rr.rerank("query", results, top_k=2)

    # No model -> original order, capped to top_k, never raises.
    assert [r.text_snippet for r in ranked] == ["a", "b"]


def test_reranker_handles_empty_and_single():
    rr = CrossEncoderReranker("fake-model")
    rr._model = _FakeModel()
    assert rr.rerank("q", [], top_k=5) == []
    single = [_Result("only")]
    assert rr.rerank("q", single, top_k=5) == single


def test_get_reranker_disabled_returns_none(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "enable_reranker", False, raising=False)
    assert get_reranker() is None


def test_get_reranker_enabled_returns_instance(monkeypatch):
    from config import settings
    monkeypatch.setattr(settings, "enable_reranker", True, raising=False)
    monkeypatch.setattr(settings, "reranker_model", "cross-encoder/test-model", raising=False)
    rr = get_reranker()
    assert rr is not None
    assert rr.model_name == "cross-encoder/test-model"


# ---------------------------------------------------------------------------
# Provider factory: OpenRouter + Bedrock
# ---------------------------------------------------------------------------


def test_openrouter_provider_config():
    # Construct directly so we don't depend on the OpenAI client validating a key.
    p = ai_service.OpenRouterProvider("sk-or-test")
    assert p._base_url == "https://openrouter.ai/api/v1"
    assert "claude" in p.QUALITY_MODEL


def test_openrouter_via_factory_requires_key():
    with pytest.raises(ValueError):
        ai_service.create_provider("openrouter", api_key="")


def test_openrouter_factory_builds_provider():
    p = ai_service.create_provider("openrouter", api_key="sk-or-test", model="openai/gpt-4o")
    assert isinstance(p, ai_service.OpenRouterProvider)
    assert p.FAST_MODEL == "openai/gpt-4o"


def test_bedrock_requires_boto3_or_builds():
    """Bedrock either builds (boto3 present) or raises a clear install error."""
    try:
        import anthropic
        has_bedrock = hasattr(anthropic, "AnthropicBedrock")
    except ImportError:
        has_bedrock = False

    if not has_bedrock:
        with pytest.raises((RuntimeError, Exception)):
            ai_service.create_provider("bedrock", region="us-east-1")
    else:
        # boto3 may still be missing even if the symbol exists; tolerate both.
        try:
            p = ai_service.create_provider("bedrock", region="us-east-1")
            assert isinstance(p, ai_service.BedrockProvider)
        except RuntimeError as e:
            assert "boto3" in str(e)


def test_unknown_provider_raises():
    with pytest.raises(ValueError):
        ai_service.create_provider("not-a-provider", api_key="x")


# ---------------------------------------------------------------------------
# Regex excerpt helper (backs find_in_documents literal=False)
# ---------------------------------------------------------------------------


def test_regex_excerpt_wraps_match():
    text = "def handle_login(request):\n    pass"
    rx = re.compile(r"def\s+handle_\w+")
    hit = _find_regex_excerpt(text, rx)
    assert hit is not None
    offset, excerpt, matched = hit
    assert matched == "def handle_login"
    assert "«def handle_login»" in excerpt
    assert offset == 0


def test_regex_excerpt_no_match_returns_none():
    assert _find_regex_excerpt("nothing here", re.compile(r"\bxyz\b")) is None
    assert _find_regex_excerpt("", re.compile(r"a")) is None


def test_regex_case_insensitive_flag():
    rx = re.compile(r"todo", re.IGNORECASE)
    hit = _find_regex_excerpt("# TODO: fix this", rx)
    assert hit is not None
    assert hit[2] == "TODO"


def test_literal_excerpt_still_works():
    hit = _find_literal_excerpt("the AAPL ticker", "AAPL", case_insensitive=False)
    assert hit is not None
    offset, excerpt = hit
    assert "«AAPL»" in excerpt
