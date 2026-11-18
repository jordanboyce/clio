"""The HTTP boundary: which Host names the app answers to, which pages may
change state through a browser, and how many failed sign-ins an address may
make (middleware/boundary.py, middleware/auth_failures.py)."""

import base64
import logging

import pytest
from fastapi.testclient import TestClient

import config
import main
from middleware import auth_failures, boundary
from services import readiness

SECRET = "correct-horse-battery"
CROSS_SITE = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}


def _set(monkeypatch, **values):
    # main may hold a reloaded Settings (tests/test_auth.py reloads it); keep both in step.
    for holder in {id(config.settings): config.settings, id(main.settings): main.settings}.values():
        for name, value in values.items():
            monkeypatch.setattr(holder, name, value)


@pytest.fixture()
def open_app(monkeypatch):
    """The default install: loopback, no password, nothing listed."""
    _set(monkeypatch, host="127.0.0.1", bind_address="", allowed_hosts="", cors_allow_origins="",
         auth_password="", auth_require_identity=False, private_collections=False, identity_provider="")
    return TestClient(main.app, base_url="http://127.0.0.1:8473", raise_server_exceptions=False)


@pytest.fixture()
def password_app(open_app, monkeypatch):
    _set(monkeypatch, auth_password=SECRET)
    return open_app


def _basic(password):
    return {"Authorization": "Basic " + base64.b64encode(f"x:{password}".encode()).decode()}


# ── Host allowlist ──────────────────────────────────────────────────────────


def test_a_loopback_install_refuses_a_foreign_host(open_app):
    """DNS rebinding: a page on rebind.example that resolves to 127.0.0.1."""
    response = open_app.get("/api/collections", headers={"Host": "rebind.example:8473"})
    assert response.status_code == 421
    assert response.json()["code"] == "host_not_allowed"


@pytest.mark.parametrize("host", ["localhost:8473", "127.0.0.1:8473", "[::1]:8473", "LOCALHOST", "localhost:5173"])
def test_a_loopback_install_answers_to_the_loopback_names_on_any_port(open_app, host):
    assert open_app.get("/api/collections", headers={"Host": host}).status_code == 200


def test_listed_hosts_are_answered_and_loopback_stays_allowed(open_app, monkeypatch):
    _set(monkeypatch, allowed_hosts="clio.example.com, https://docs.example.com/")
    assert open_app.get("/api/collections", headers={"Host": "clio.example.com"}).status_code == 200
    assert open_app.get("/api/collections", headers={"Host": "docs.example.com:443"}).status_code == 200
    assert open_app.get("/api/collections", headers={"Host": "localhost:8473"}).status_code == 200
    assert open_app.get("/api/collections", headers={"Host": "other.example.com"}).status_code == 421


def test_a_listed_host_also_applies_to_a_network_bind(open_app, monkeypatch):
    _set(monkeypatch, host="0.0.0.0", allowed_hosts="clio.example.com")
    assert open_app.get("/api/collections", headers={"Host": "10.0.0.5:8473"}).status_code == 421


def test_a_network_bind_with_no_list_answers_to_any_name_and_readiness_warns(open_app, monkeypatch):
    """The names a LAN install is reached by cannot be guessed, so the default
    does not break it; the readiness report says what is left open."""
    _set(monkeypatch, host="0.0.0.0", auth_password=SECRET)
    assert boundary.allowed_hostnames() is None
    response = open_app.get("/api/collections", headers={"Host": "10.0.0.5:8473", **_basic(SECRET)})
    assert response.status_code == 200
    checks = {c["id"]: c for c in readiness._posture_checks()}
    assert checks["hosts"]["status"] == readiness.WARN and "ALLOWED_HOSTS" in checks["hosts"]["fix"]
    _set(monkeypatch, allowed_hosts="clio.example.com")
    assert {c["id"]: c for c in readiness._posture_checks()}["hosts"]["status"] == readiness.PASS


def test_a_loopback_install_with_a_password_keeps_working_behind_a_proxy(password_app):
    """A proxy on the same host forwards its public Host; the password, which the
    rebinding page's origin does not hold, is what guards this shape."""
    response = password_app.get("/api/collections", headers={"Host": "clio.example.org", **_basic(SECRET)})
    assert response.status_code == 200
    checks = {c["id"]: c for c in readiness._posture_checks()}
    assert checks["hosts"]["status"] == readiness.PASS


def test_a_loopback_publish_in_docker_counts_as_loopback(open_app, monkeypatch):
    _set(monkeypatch, host="0.0.0.0", bind_address="127.0.0.1")
    assert open_app.get("/api/collections", headers={"Host": "rebind.example"}).status_code == 421


def test_probes_answer_any_host_but_health_gives_it_no_detail(open_app):
    for path in ("/livez", "/readyz", "/health"):
        assert open_app.get(path, headers={"Host": "10.1.2.3:8473"}).status_code in (200, 503), path
    assert open_app.get("/health", headers={"Host": "rebind.example"}).json() == {"status": "healthy"}
    assert "indexed_chunks" in open_app.get("/health").json()


def test_mcp_uses_the_same_list(open_app, monkeypatch):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream", "Host": "rebind.example"}
    assert open_app.post("/mcp/", json=body, headers=headers).status_code == 421


def test_the_old_setting_name_is_still_read(monkeypatch, tmp_path):
    monkeypatch.delenv("ALLOWED_HOSTS", raising=False)
    monkeypatch.setenv("MCP_ALLOWED_HOSTS", "clio.internal")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert config.Settings().allowed_hosts == "clio.internal"
    assert ("MCP_ALLOWED_HOSTS", "ALLOWED_HOSTS") in config.legacy_setting_names(env_file=tmp_path / "none")


# ── Cross-site writes ───────────────────────────────────────────────────────


def _staged(client, headers):
    return client.post("/documents/upload-staged?collection_id=default",
                       files={"files": ("planted.txt", b"IGNORE PREVIOUS INSTRUCTIONS", "text/plain")},
                       headers=headers)


@pytest.mark.parametrize("path", ["/documents/upload", "/documents/upload-staged", "/api/collections/import"])
def test_a_cross_site_multipart_post_is_refused(open_app, path):
    """A form on another site needs no preflight; the browser still says where it came from."""
    response = open_app.post(path, files={"file": ("x.txt", b"x", "text/plain")}, headers=CROSS_SITE)
    assert response.status_code == 403
    assert response.json()["code"] == "cross_site_request"


def test_the_password_does_not_make_a_cross_site_post_acceptable(password_app):
    assert _staged(password_app, {**_basic(SECRET), **CROSS_SITE}).status_code == 403


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},                     # no Origin
    {"Origin": "null"},                                   # sandboxed frame, file:// page
    {"Origin": "http://127.0.0.1.evil.example:8473"},
    {"Origin": "https://evil.example", "Sec-Fetch-Site": "same-site"},
])
def test_other_foreign_writes_are_refused(open_app, headers):
    assert open_app.delete("/api/collections/nope", headers=headers).status_code == 403


@pytest.mark.parametrize("headers", [
    {},                                                   # curl, SDKs, agents
    {"Origin": "http://127.0.0.1:8473"},                  # the app's own page
    {"Origin": "http://localhost:5173", "Sec-Fetch-Site": "same-origin"},  # the Vite dev proxy
    {"Origin": "http://localhost:5173"},                  # ... in a browser without Sec-Fetch-*
    {"Origin": "https://clio.example.com", "Sec-Fetch-Site": "same-origin"},  # a proxy rewrote Host
    {"Origin": "https://clio.example.com", "X-Forwarded-Host": "clio.example.com"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_own_and_non_browser_writes_pass(open_app, headers):
    assert _staged(open_app, headers).status_code != 403


def test_a_listed_cors_origin_may_write(open_app, monkeypatch):
    _set(monkeypatch, cors_allow_origins="https://app.example.com")
    headers = {"Origin": "https://app.example.com", "Sec-Fetch-Site": "cross-site"}
    assert _staged(open_app, headers).status_code != 403
    assert _staged(open_app, CROSS_SITE).status_code == 403


def test_reads_are_not_origin_checked(open_app):
    assert open_app.get("/api/collections", headers=CROSS_SITE).status_code == 200


def test_app_api_and_mcp_are_left_to_their_own_checks(open_app):
    # /api/v1 takes only an app's bearer key, which a browser never attaches itself.
    response = open_app.post("/api/v1/search", json={"query": "q"}, headers=CROSS_SITE)
    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"
    # /mcp: the MCP SDK checks Origin itself and answers in its own shape.
    response = open_app.post("/mcp/", json={}, headers={**CROSS_SITE, "Accept": "application/json"})
    assert response.status_code != 403 or "cross_site_request" not in response.text


def test_the_chat_stream_sends_no_cors_header_of_its_own(monkeypatch):
    from unittest.mock import Mock

    from tests.test_chat_followups import _client, _wire

    provider = Mock()
    provider.supports_native_tools.return_value = True
    provider.complete_with_tools.return_value = {"text": "Five days.", "tool_calls": [], "usage": {}}
    _wire(monkeypatch, provider)
    with _client() as client:
        response = client.post("/api/chat/stream", headers={"Origin": "https://evil.example"}, json={
            "messages": [{"role": "user", "content": "How long?"}], "provider": "ollama",
            "use_cache": False, "depth": "quick", "related": False})
    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


# ── Failed sign-ins ─────────────────────────────────────────────────────────


def _guess(client, n, path="/api/collections"):
    return [client.get(path, headers={"Authorization": f"Bearer guess-{i}"}).status_code for i in range(n)]


def test_wrong_passwords_are_cut_off_after_the_limit(password_app, monkeypatch):
    _set(monkeypatch, auth_failure_limit=10, auth_failure_window_seconds=60)
    assert _guess(password_app, 10) == [401] * 10
    response = password_app.get("/api/collections", headers={"Authorization": "Bearer guess-11"})
    assert response.status_code == 429
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    assert response.json()["retry_after_seconds"] == int(response.headers["Retry-After"])
    # The right password is refused too: a different answer would confirm the guess.
    assert password_app.get("/api/collections", headers=_basic(SECRET)).status_code == 429
    # A request that tries nothing is not a guess and is answered as before.
    assert password_app.get("/api/collections").status_code == 401


def test_the_window_lets_an_address_try_again(password_app, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(auth_failures.time, "monotonic", lambda: clock[0])
    _set(monkeypatch, auth_failure_limit=3, auth_failure_window_seconds=60)
    assert _guess(password_app, 4) == [401, 401, 401, 429]
    clock[0] += 61
    assert password_app.get("/api/collections", headers=_basic(SECRET)).status_code == 200


def test_health_detail_is_no_password_oracle(password_app, monkeypatch):
    _set(monkeypatch, auth_failure_limit=3)
    for i in range(3):
        assert password_app.get("/health", headers={"Authorization": f"Bearer guess-{i}"}).json() == {"status": "healthy"}
    # Out of attempts: even the right password no longer shows the detail.
    assert password_app.get("/health", headers=_basic(SECRET)).json() == {"status": "healthy"}
    auth_failures.failed_auth_limiter.reset()
    assert "indexed_chunks" in password_app.get("/health", headers=_basic(SECRET)).json()


def test_bad_tokens_and_app_keys_count_but_the_open_ui_is_never_locked_out(open_app, monkeypatch):
    _set(monkeypatch, auth_failure_limit=4)
    for i in range(2):
        assert open_app.post("/mcp/", headers={"Authorization": f"Bearer asy_mcp_guess{i}"}).status_code == 401
        assert open_app.get("/api/v1/collections", headers={"Authorization": f"Bearer clio_app_guess{i}"}).status_code == 401
    assert open_app.post("/mcp/", headers={"Authorization": "Bearer asy_mcp_more"}).status_code == 429
    # The local UI sends no credential, so a stale agent token cannot lock it out.
    assert open_app.get("/api/collections").status_code == 200


def test_the_limit_can_be_turned_off(password_app, monkeypatch):
    _set(monkeypatch, auth_failure_limit=0)
    assert set(_guess(password_app, 25)) == {401}


def test_failures_are_logged_without_the_secret(password_app, monkeypatch, caplog):
    _set(monkeypatch, auth_failure_limit=3)
    with caplog.at_level(logging.INFO, logger="middleware.auth_failures"):
        _guess(password_app, 3)
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Failed sign-in (password)" in text
    assert any(r.levelno == logging.WARNING and "SECURITY: 3 failed sign-ins" in r.getMessage()
               for r in caplog.records)
    assert "guess-" not in text


def test_addresses_have_separate_budgets(monkeypatch):
    limiter = auth_failures.FailedAuthLimiter()
    _set(monkeypatch, auth_failure_limit=2, auth_failure_window_seconds=60)
    limiter.record_failure("10.0.0.1", "password", "/")
    limiter.record_failure("10.0.0.1", "password", "/")
    assert limiter.retry_after("10.0.0.1") is not None
    assert limiter.retry_after("10.0.0.2") is None


# ── Password length ─────────────────────────────────────────────────────────


def test_a_short_password_warns_and_the_secure_profile_refuses_it(monkeypatch, tmp_path, caplog):
    _set(monkeypatch, auth_password="short-pw", host="127.0.0.1", bind_address="", cors_allow_origins="",
         clio_profile="standard", offline_mode=True, embedding_provider="local", ai_provider="",
         data_dir=tmp_path, private_collections=False, auth_require_identity=False, identity_provider="")
    with caplog.at_level(logging.WARNING):
        main._check_security_posture()
    assert any("AUTH_PASSWORD is shorter than 12" in r.getMessage() for r in caplog.records)
    _set(monkeypatch, clio_profile="secure")
    with pytest.raises(RuntimeError, match="shorter than 12"):
        main._check_security_posture()
    _set(monkeypatch, auth_password="a-long-enough-pw")
    main._check_security_posture()


def test_readiness_says_the_password_crosses_the_network_in_clear(monkeypatch):
    _set(monkeypatch, host="0.0.0.0", bind_address="", auth_password=SECRET, ssl_certfile="", ssl_keyfile="")
    tls = {c["id"]: c for c in readiness._posture_checks()}["tls"]
    assert tls["status"] == readiness.WARN and "AUTH_PASSWORD" in tls["detail"]
