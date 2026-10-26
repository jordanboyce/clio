"""The per-identity token-bucket rate limiter.

Unit tests exercise the bucket math directly (burst capacity, refill,
key/class isolation, 0 = unlimited). The HTTP tests confirm the middleware
is actually wired: enabled → burst past the limit yields the shared 429
contract; disabled (the suite-wide default from conftest) → passthrough.
"""

import pytest
from fastapi.testclient import TestClient

import config
from middleware.rate_limit import RateLimiter, _classify, rate_limiter


@pytest.fixture()
def limits():
    """Tight limits for the test, restored afterwards; buckets reset."""
    s = config.settings
    saved = (s.rate_limit_enabled, s.rate_limit_chat_per_minute,
             s.rate_limit_search_per_minute, s.rate_limit_default_per_minute)
    s.rate_limit_enabled = True
    s.rate_limit_chat_per_minute = 2
    s.rate_limit_search_per_minute = 3
    s.rate_limit_default_per_minute = 4
    rate_limiter.reset()
    yield s
    (s.rate_limit_enabled, s.rate_limit_chat_per_minute,
     s.rate_limit_search_per_minute, s.rate_limit_default_per_minute) = saved
    rate_limiter.reset()


# ── Classification ──────────────────────────────────────────────────────────


def test_mcp_class_is_separate_and_unlimited_by_default():
    assert _classify("/mcp") == "mcp"
    assert _classify("/mcp/") == "mcp"
    assert _classify("/mcp/anything") == "mcp"
    limiter = RateLimiter()
    for _ in range(500):
        assert limiter.check("mcp", "mcp-token:abc") is None


def test_pinned_rate_limit_key_wins_over_identity():
    from types import SimpleNamespace
    from middleware.rate_limit import resolve_limit_key
    request = SimpleNamespace(state=SimpleNamespace(auth_identity="jordan@example.com", rate_limit_key="mcp-token:t1"),
                              headers={}, client=None)
    assert resolve_limit_key(request) == "mcp-token:t1"


def test_classify():
    assert _classify("/api/chat") == "chat"
    assert _classify("/api/chat/stream") == "chat"
    # Page-load reads under /api/chat are ordinary traffic, not answer turns.
    assert _classify("/api/chat/cache") == "default"
    assert _classify("/api/chat/starters") == "default"
    assert _classify("/api/search") == "search"
    assert _classify("/api/collections") == "default"
    assert _classify("/health") is None
    assert _classify("/assets/app.js") is None


# ── Bucket math ─────────────────────────────────────────────────────────────


def test_burst_then_reject(limits):
    lim = RateLimiter()
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is None
    retry = lim.check("chat", "alice")
    assert retry is not None and retry >= 1
    stats = lim.stats()
    assert stats["chat"] == {"allowed": 2, "rejected": 1}


def test_keys_are_isolated(limits):
    lim = RateLimiter()
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is not None
    # Bob has his own bucket
    assert lim.check("chat", "bob") is None


def test_classes_are_isolated(limits):
    lim = RateLimiter()
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is not None
    # Same identity, different class → separate budget
    assert lim.check("search", "alice") is None


def test_zero_means_unlimited(limits):
    config.settings.rate_limit_chat_per_minute = 0
    lim = RateLimiter()
    for _ in range(50):
        assert lim.check("chat", "alice") is None


def test_refill_restores_tokens(limits, monkeypatch):
    import middleware.rate_limit as rl
    now = [1000.0]
    monkeypatch.setattr(rl.time, "monotonic", lambda: now[0])
    lim = RateLimiter()
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is None
    assert lim.check("chat", "alice") is not None
    # 2/min = one token every 30s
    now[0] += 31
    assert lim.check("chat", "alice") is None


# ── Middleware wiring ───────────────────────────────────────────────────────


def test_http_burst_hits_429_with_contract(limits):
    import main
    client = TestClient(main.app)
    codes = [client.get("/api/capabilities").status_code for _ in range(6)]
    assert 429 in codes
    r = client.get("/api/capabilities")
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    body = r.json()
    assert set(body) == {"detail", "retry_after_seconds"}


def test_http_disabled_passes_through(limits):
    config.settings.rate_limit_enabled = False
    import main
    client = TestClient(main.app)
    for _ in range(10):
        assert client.get("/api/capabilities").status_code == 200
