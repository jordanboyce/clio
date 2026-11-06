"""What a corporate network and its security scan expect of a hosted service:
response headers, probes, nothing disclosed to an anonymous caller, API docs that
load with no internet, logs a SIEM can parse, and a posture check that says what
is actually protecting the deployment.
"""

import json
import logging

import pytest
from fastapi.testclient import TestClient

import api.system
import config
import main
import services.api_docs
import services.readiness as readiness
from middleware import request_meta, security_headers
from services import log_format

CDN_HOSTS = ("cdn.jsdelivr.net", "unpkg.com", "fastapi.tiangolo.com", "cdnjs.cloudflare.com")


def _set(monkeypatch, **values):
    holders = [config, main, api.system, readiness, security_headers]
    for name, value in values.items():
        for holder in holders:
            monkeypatch.setattr(holder.settings, name, value)


@pytest.fixture()
def client(monkeypatch):
    _set(monkeypatch, auth_password="", private_collections=False, auth_require_identity=False, identity_provider="")
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture()
def https_client(monkeypatch):
    _set(monkeypatch, auth_password="", private_collections=False, auth_require_identity=False, identity_provider="")
    return TestClient(main.app, raise_server_exceptions=False, base_url="https://clio.example.test")


# ── Response headers ───────────────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/health", "/livez", "/api/capabilities", "/does-not-exist"])
def test_every_response_carries_the_headers_a_scan_looks_for(client, path):
    headers = client.get(path).headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert headers["Referrer-Policy"] == "same-origin"
    assert "default-src 'self'" in headers["Content-Security-Policy"]


def test_a_refusal_carries_them_too(client, monkeypatch):
    _set(monkeypatch, auth_password="deploy-secret")
    response = client.get("/api/collections")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_hsts_only_over_https(client, https_client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    assert https_client.get("/health").headers["Strict-Transport-Security"].startswith("max-age=")


def test_hsts_and_csp_can_be_turned_off(https_client, monkeypatch):
    _set(monkeypatch, hsts_max_age_seconds=0, content_security_policy="")
    headers = https_client.get("/health").headers
    assert "Strict-Transport-Security" not in headers
    assert "Content-Security-Policy" not in headers


def test_a_pdf_has_no_csp_so_the_browsers_viewer_can_open_it():
    from starlette.responses import Response
    from types import SimpleNamespace

    response = Response(b"%PDF", media_type="application/pdf")
    request = SimpleNamespace(url=SimpleNamespace(scheme="http", path="/documents/d1/pdf"))
    security_headers.apply(request, response)
    assert "Content-Security-Policy" not in response.headers
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ── Probes ─────────────────────────────────────────────────────────────────


def test_probes_need_no_credential_and_say_nothing(client, monkeypatch):
    from api import deps

    _set(monkeypatch, auth_password="deploy-secret", auth_require_identity=False)
    monkeypatch.setattr(deps, "_initialized", True)
    assert client.get("/livez").json() == {"status": "alive"}
    assert client.get("/readyz").json() == {"status": "ready"}


def test_not_ready_until_started(client, monkeypatch):
    from api import deps

    monkeypatch.setattr(deps, "_initialized", False)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "initializing"}
    assert client.get("/livez").status_code == 200


def test_not_ready_when_the_database_fails_and_the_error_is_not_disclosed(client, monkeypatch):
    from api import deps
    from services.app_database import app_db

    monkeypatch.setattr(deps, "_initialized", True)

    def broken(*args):
        raise RuntimeError("connection to postgres://admin:hunter2@db failed")

    monkeypatch.setattr(app_db, "get_collection", broken)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "reason": "database"}


def test_probes_never_touch_a_collection(client, monkeypatch):
    from api import deps
    from services.indexer_manager import indexer_manager

    monkeypatch.setattr(deps, "_initialized", True)

    def boom(*args, **kwargs):
        raise AssertionError("a probe opened a collection")

    monkeypatch.setattr(indexer_manager, "get_collection_stats", boom)
    monkeypatch.setattr(indexer_manager, "get_indexer", boom)
    assert client.get("/livez").status_code == 200
    assert client.get("/readyz").status_code == 200


# ── /health ────────────────────────────────────────────────────────────────


def _no_collection_access(monkeypatch):
    from services.indexer_manager import indexer_manager

    def boom(*args, **kwargs):
        raise AssertionError("an anonymous caller opened a collection")

    monkeypatch.setattr(indexer_manager, "get_collection_stats", boom)


def test_anonymous_health_on_a_gated_deployment_is_just_the_status(client, monkeypatch):
    _set(monkeypatch, auth_password="deploy-secret")
    _no_collection_access(monkeypatch)
    response = client.get("/health", params={"collection_id": "someones-private-collection"})
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_a_wrong_password_gets_no_more_than_no_password(client, monkeypatch):
    _set(monkeypatch, auth_password="deploy-secret")
    _no_collection_access(monkeypatch)
    response = client.get("/health", headers={"Authorization": "Bearer wrong"})
    assert response.json() == {"status": "healthy"}


def test_the_right_password_gets_the_counts_the_ui_shows(client, monkeypatch):
    from services.indexer_manager import indexer_manager

    _set(monkeypatch, auth_password="deploy-secret")
    monkeypatch.setattr(indexer_manager, "get_collection_stats",
                        lambda cid: {"total_chunks": 7, "total_documents": 2, "total_pages": 3})
    body = client.get("/health", headers={"Authorization": "Bearer deploy-secret"}).json()
    assert body["total_documents"] == 2 and body["indexed_chunks"] == 7 and "embedding" in body


def test_an_open_appliance_keeps_its_health_details(client, monkeypatch):
    from services.indexer_manager import indexer_manager

    monkeypatch.setattr(indexer_manager, "get_collection_stats",
                        lambda cid: {"total_chunks": 1, "total_documents": 1, "total_pages": 1})
    assert client.get("/health").json()["total_documents"] == 1


def test_signed_in_is_not_enough_for_someone_elses_collection(client, monkeypatch):
    from services.sharing_service import sharing_service

    _set(monkeypatch, identity_provider="trusted_header", trusted_header_name="X-Forwarded-User", private_collections=True)
    monkeypatch.setattr(sharing_service, "check_collection_access",
                        lambda cid, user: "owner" if user == "owner@example.test" else None)
    _no_collection_access(monkeypatch)
    body = client.get("/health", params={"collection_id": "c1"}, headers={"X-Forwarded-User": "other@example.test"}).json()
    assert body["total_documents"] == 0 and "collection_id" not in body


def test_a_failing_collection_reads_as_degraded(client, monkeypatch):
    from services.indexer_manager import indexer_manager

    def broken(cid):
        raise RuntimeError("index unreadable")

    monkeypatch.setattr(indexer_manager, "get_collection_stats", broken)
    assert client.get("/health").json()["status"] == "degraded"


# ── A bad credential is a 401, whatever it holds ───────────────────────────


@pytest.mark.parametrize("value", [b"Bearer p\xe4ssword", b"Basic \xff\xff", b"Bearer \xc3\xa9"])
def test_non_ascii_credentials_are_a_401_not_a_500(client, monkeypatch, value):
    _set(monkeypatch, auth_password="deploy-secret")
    assert client.get("/api/collections", headers={"Authorization": value}).status_code == 401


def test_a_non_ascii_password_still_works(client, monkeypatch):
    import base64

    _set(monkeypatch, auth_password="pässword")
    encoded = base64.b64encode("any:pässword".encode()).decode()
    assert client.get("/api/capabilities", headers={"Authorization": f"Basic {encoded}"}).status_code == 200


# ── API docs with no internet ──────────────────────────────────────────────


@pytest.mark.parametrize("path", ["/docs", "/api/v1/docs"])
def test_docs_never_call_out_with_or_without_the_build(client, monkeypatch, tmp_path, path):
    # No frontend build: a plain page that links to the description.
    monkeypatch.setattr(services.api_docs, "_ASSETS", tmp_path / "nothing-here")
    page = client.get(path)
    assert page.status_code == 200 and "openapi.json" in page.text
    assert not any(host in page.text for host in CDN_HOSTS)

    # With it: Swagger UI, from this server.
    (tmp_path / "swagger-ui-bundle.js").write_text("// stub")
    monkeypatch.setattr(services.api_docs, "_ASSETS", tmp_path)
    page = client.get(path)
    assert "/vendor/swagger-ui/swagger-ui-bundle.js" in page.text
    assert not any(host in page.text for host in CDN_HOSTS)
    assert "connect-src 'self'" in page.headers["Content-Security-Policy"]


def test_redoc_is_gone_rather_than_blank(client):
    assert client.get("/redoc").status_code == 404


def test_vendored_assets_load_during_a_login_prompt(client, monkeypatch):
    _set(monkeypatch, auth_password="deploy-secret")
    assert client.get("/vendor/swagger-ui/swagger-ui.css").status_code != 401


# ── The access log ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("path,expected", [
    ("/search", True), ("/documents", True), ("/documents/upload/1/status", True),
    ("/mcp", True), ("/mcp/", True), ("/api/collections", True), ("/api/v1/me", True),
    ("/health", False), ("/livez", False), ("/readyz", False), ("/assets/app.js", False),
])
def test_access_log_covers_what_people_and_apps_do(path, expected):
    assert request_meta._should_log(path) is expected


def test_access_lines_carry_structured_fields_and_no_query_string(client, caplog):
    with caplog.at_level(logging.INFO, logger="clio.access"):
        client.get("/api/capabilities", params={"secret_param": "value"})
    record = next(r for r in caplog.records if r.name == "clio.access")
    assert record.http_status == 200
    assert record.http_path == "/api/capabilities"
    assert record.request_id
    assert "secret_param" not in record.getMessage()


# ── JSON logs ──────────────────────────────────────────────────────────────


def test_json_lines_parse_and_keep_the_extra_fields():
    record = logging.LogRecord("clio.access", logging.INFO, __file__, 1, "GET %s", ("/x",), None)
    record.http_status = 200
    entry = json.loads(log_format.JsonFormatter().format(record))
    assert entry["message"] == "GET /x"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "clio.access"
    assert entry["http_status"] == 200
    assert entry["timestamp"].endswith("+00:00")


def test_a_traceback_stays_on_one_line():
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        record = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    line = log_format.JsonFormatter().format(record)
    assert "\n" not in line
    assert "boom" in json.loads(line)["exception"]


def test_configure_reaches_uvicorns_own_handlers():
    handler = logging.StreamHandler()
    uvicorn_logger = logging.getLogger("uvicorn.access")
    root_handlers = list(logging.getLogger().handlers)
    formatters = [h.formatter for h in root_handlers]
    uvicorn_logger.addHandler(handler)
    try:
        log_format.configure("json")
        assert isinstance(handler.formatter, log_format.JsonFormatter)
    finally:
        uvicorn_logger.removeHandler(handler)
        for h, f in zip(root_handlers, formatters):
            h.setFormatter(f)


def test_text_is_still_the_default():
    assert config.Settings.model_fields["log_format"].default == "text"


# ── The container and the reference proxy ──────────────────────────────────
# Read as text, because each of these was found by running the real stack and
# none of them shows up without Docker.

ROOT = config.Path(__file__).parent.parent


def test_the_image_passes_tls_settings_to_uvicorn():
    """uvicorn does not read SSL_CERTFILE itself, so the settings used to be
    ignored in a container while the readiness report said TLS was configured."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert '--ssl-certfile "$SSL_CERTFILE"' in dockerfile
    assert '--ssl-keyfile "$SSL_KEYFILE"' in dockerfile
    assert "--no-server-header" in dockerfile


def test_the_image_healthcheck_is_the_probe_and_ignores_an_outbound_proxy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    healthcheck = dockerfile[dockerfile.index("HEALTHCHECK"):]
    assert "/readyz" in healthcheck
    assert "--noproxy" in healthcheck  # HTTP_PROXY must not capture localhost


def test_the_proxy_reaches_clio_only_over_the_network_whose_address_is_trusted():
    compose = (ROOT / "docker-compose.proxy.yml").read_text(encoding="utf-8")
    template = (ROOT / "docker" / "nginx" / "clio.conf.template").read_text(encoding="utf-8")
    # The bare service name also resolves on `default`: X-Forwarded-Proto would
    # then be ignored, and HSTS and client addresses lost with it.
    assert "proxy_pass http://clio-upstream:8473;" in template
    assert "aliases: [clio-upstream]" in compose
    assert "FORWARDED_ALLOW_IPS=${PROXY_IP:-172.30.73.2}" in compose


def test_clio_cannot_take_the_address_reserved_for_the_proxy():
    """Clio starts first. Given any free address it takes .2, and nginx then
    fails to start with "Address already in use"."""
    compose = (ROOT / "docker-compose.proxy.yml").read_text(encoding="utf-8")
    assert "ipv4_address: ${CLIO_PROXY_NET_IP:-172.30.73.3}" in compose
    assert "ipv4_address: ${PROXY_IP:-172.30.73.2}" in compose


def test_the_proxy_decides_what_clients_can_claim():
    template = (ROOT / "docker" / "nginx" / "clio.conf.template").read_text(encoding="utf-8")
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in template
    assert 'proxy_set_header X-Forwarded-User "";' in template
    assert "proxy_buffering  off;" in template


def test_clio_publishes_no_port_behind_the_proxy():
    compose = (ROOT / "docker-compose.proxy.yml").read_text(encoding="utf-8")
    assert "ports: !reset []" in compose
    assert "- BIND_ADDRESS=proxy" in compose
    assert readiness.reachable_beyond_host(type("Cfg", (), {"host": "0.0.0.0", "bind_address": "proxy"})())


# ── What is protecting this deployment? ────────────────────────────────────


def _auth_check():
    return next(c for c in readiness.run_checks()["checks"] if c["id"] == "auth")


def _exposed_oidc(monkeypatch, **more):
    _set(monkeypatch, host="0.0.0.0", bind_address="0.0.0.0", auth_password="", private_collections=False,
         auth_require_identity=False, identity_provider="oidc", oidc_issuer="https://idp.example.test",
         oidc_audience="clio", **more)


def test_naming_an_identity_provider_alone_is_not_a_gate(client, monkeypatch):
    _exposed_oidc(monkeypatch)
    assert readiness.auth_gate() is None
    assert client.get("/api/capabilities").status_code == 200  # the behaviour being named
    check = _auth_check()
    assert check["status"] == readiness.WARN
    assert "nothing requires it" in check["detail"]
    assert "AUTH_REQUIRE_IDENTITY" in check["fix"]


def test_the_secure_profile_names_the_setting_that_is_actually_missing(monkeypatch):
    _exposed_oidc(monkeypatch)
    monkeypatch.setattr(readiness.settings, "clio_profile", "secure")
    problems = readiness.secure_profile_errors()
    assert any("AUTH_REQUIRE_IDENTITY" in p for p in problems)
    # The old advice told someone who had already set IDENTITY_PROVIDER to set it.
    assert not any("set IDENTITY_PROVIDER," in p for p in problems)


@pytest.mark.parametrize("setting", ["auth_require_identity", "private_collections"])
def test_either_requirement_satisfies_the_secure_profile(monkeypatch, setting):
    _exposed_oidc(monkeypatch)
    monkeypatch.setattr(readiness.settings, "clio_profile", "secure")
    monkeypatch.setattr(readiness.settings, setting, True)
    assert readiness.auth_gate() is not None
    assert not any("IDENTITY_PROVIDER" in p for p in readiness.secure_profile_errors())
    assert _auth_check()["status"] == readiness.PASS


def test_the_onprem_example_requires_identity_where_it_offers_it():
    text = (config.Path(__file__).parent.parent / ".env.onprem.example").read_text(encoding="utf-8")
    assert text.count("AUTH_REQUIRE_IDENTITY=true") >= 2
