"""Tests for the AUTH_PASSWORD shared-secret middleware (public-deploy gate)
and the startup checks that keep an unauthenticated app off the network.

The middleware is registered at import time only when AUTH_PASSWORD is set,
so these tests reload config + main with the variable present and restore
the unauthenticated app afterwards.
"""

import base64
import importlib

import pytest
from fastapi.testclient import TestClient

SECRET = "test-deploy-secret"


@pytest.fixture()
def reloaded_main(monkeypatch):
    """Reload config + main under caller-supplied env, then restore both.

    Yields a callable taking env overrides and returning the reloaded `main`.
    """
    import config
    import main

    touched = []

    def _reload(**env):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
            touched.append(key)
        importlib.reload(config)
        return importlib.reload(main)

    yield _reload

    for key in touched:
        monkeypatch.delenv(key, raising=False)
    importlib.reload(config)
    importlib.reload(main)


@pytest.fixture()
def auth_client(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", SECRET)
    import config
    import main

    importlib.reload(config)
    main = importlib.reload(main)
    # Bare client (no context manager): lifespan is skipped, middleware still runs.
    yield TestClient(main.app)

    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    importlib.reload(config)
    importlib.reload(main)


def test_health_stays_open(auth_client):
    """Platform health probes (Docker, Railway) must never require auth."""
    response = auth_client.get("/health")
    assert response.status_code == 200


def test_unauthenticated_request_rejected(auth_client):
    response = auth_client.get("/documents/upload/active")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"].startswith("Basic")


def test_wrong_password_rejected(auth_client):
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": "Bearer wrong"}
    )
    assert response.status_code == 401


def test_bearer_password_accepted(auth_client):
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code != 401


def test_required_identity_rejects_anonymous_request(reloaded_main):
    main = reloaded_main(
        AUTH_REQUIRE_IDENTITY="true",
        IDENTITY_PROVIDER="oidc",
        OIDC_ISSUER="https://sso.example.test/realms/main",
        OIDC_AUDIENCE="clio",
        PRIVATE_COLLECTIONS="false",
    )
    response = TestClient(main.app).get("/documents/upload/active")
    assert response.status_code == 401


def test_required_identity_does_not_accept_shared_password(reloaded_main):
    main = reloaded_main(
        AUTH_REQUIRE_IDENTITY="true",
        AUTH_PASSWORD=SECRET,
        IDENTITY_PROVIDER="oidc",
        OIDC_ISSUER="https://sso.example.test/realms/main",
        OIDC_AUDIENCE="clio",
        PRIVATE_COLLECTIONS="false",
    )
    token = base64.b64encode(f"anyone:{SECRET}".encode()).decode()
    response = TestClient(main.app).get(
        "/documents/upload/active", headers={"Authorization": f"Basic {token}"}
    )
    assert response.status_code == 401


def test_basic_password_accepted_any_username(auth_client):
    token = base64.b64encode(f"anyone:{SECRET}".encode()).decode()
    response = auth_client.get(
        "/documents/upload/active", headers={"Authorization": f"Basic {token}"}
    )
    assert response.status_code != 401


# ── Startup security posture ────────────────────────────────────────────────


def test_admin_allowlist_is_enforced_in_shared_collection_mode(monkeypatch):
    from fastapi import HTTPException

    import config
    from api.deps import require_admin
    from middleware.user_context import reset_request_identity, set_request_identity

    from services import identity

    monkeypatch.setattr(config.settings, "private_collections", False)
    monkeypatch.setattr(config.settings, "admin_emails", "owner@example.com")
    # The list only binds once there is an identity source to match it against.
    monkeypatch.setattr(identity, "active_provider", lambda: "oidc")
    token = set_request_identity("student@example.edu")
    try:
        with pytest.raises(HTTPException) as exc:
            require_admin("manage access")
        assert exc.value.status_code == 403
        reset_request_identity(token)
        token = set_request_identity("owner@example.com")
        assert require_admin("manage access") == "owner@example.com"
    finally:
        reset_request_identity(token)


def test_required_identity_refuses_start_without_provider(reloaded_main):
    with pytest.raises(RuntimeError, match="AUTH_REQUIRE_IDENTITY"):
        reloaded_main(AUTH_REQUIRE_IDENTITY="true", PRIVATE_COLLECTIONS="false")


def test_multi_user_refuses_to_start(reloaded_main):
    """Ownership is checked on collection metadata only, never on content.

    Search, document retrieval, chat and the /mcp tools all take a
    collection_id and act on it unchecked, so the flag advertised an isolation
    boundary that does not exist. Refusing beats half-enforcing.
    """
    with pytest.raises(RuntimeError, match="not supported"):
        reloaded_main(ENABLE_MULTI_USER="true")


def test_multi_user_refuses_even_with_a_password(reloaded_main):
    """A shared secret authenticates the caller but does not separate callers."""
    with pytest.raises(RuntimeError, match="not supported"):
        reloaded_main(ENABLE_MULTI_USER="true", AUTH_PASSWORD=SECRET)


def test_network_bind_without_password_warns(reloaded_main, caplog):
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="0.0.0.0", AUTH_PASSWORD="")
    assert any("SECURITY" in r.message for r in caplog.records)


def test_network_bind_with_password_is_quiet(reloaded_main, caplog):
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="0.0.0.0", AUTH_PASSWORD=SECRET)
    assert not any("SECURITY" in r.message for r in caplog.records)


def test_loopback_without_password_is_quiet(reloaded_main, caplog):
    """The default posture must not cry wolf — loopback needs no password."""
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="127.0.0.1", AUTH_PASSWORD="")
    assert not any("SECURITY" in r.message for r in caplog.records)


def test_loopback_publish_in_docker_is_quiet(reloaded_main, caplog):
    """Inside a container HOST is 0.0.0.0, but compose publishes the port on
    127.0.0.1 and says so with BIND_ADDRESS; that is not network exposure."""
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="0.0.0.0", BIND_ADDRESS="127.0.0.1", AUTH_PASSWORD="")
    assert not any("SECURITY" in r.message for r in caplog.records)


def test_network_publish_in_docker_still_warns(reloaded_main, caplog):
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="0.0.0.0", BIND_ADDRESS="0.0.0.0", AUTH_PASSWORD="")
    assert any("SECURITY" in r.message for r in caplog.records)


def test_wildcard_cors_without_password_warns(reloaded_main, caplog):
    with caplog.at_level("WARNING"):
        reloaded_main(HOST="127.0.0.1", AUTH_PASSWORD="", CORS_ALLOW_ORIGINS="*")
    assert any("CORS" in r.message for r in caplog.records)


def test_cors_middleware_absent_by_default(reloaded_main):
    """No CORS headers means the same-origin policy protects an open install."""
    main = reloaded_main(CORS_ALLOW_ORIGINS="")
    assert not any("CORSMiddleware" in str(m.cls) for m in main.app.user_middleware)


def test_cors_middleware_added_when_origins_listed(reloaded_main):
    main = reloaded_main(CORS_ALLOW_ORIGINS="https://example.com")
    assert any("CORSMiddleware" in str(m.cls) for m in main.app.user_middleware)


def test_admin_list_without_an_identity_source_does_not_lock_out_the_owner(monkeypatch):
    """ADMIN_EMAILS can only be matched against a verified identity. On the
    default open appliance there is none, so enforcing the list would lock
    the owner out of their own settings; the gate applies once a provider
    exists."""
    import config
    from api.deps import require_admin
    from services import admins, identity

    monkeypatch.setattr(config.settings, "private_collections", False)
    monkeypatch.setattr(config.settings, "identity_provider", "")
    monkeypatch.setattr(config.settings, "admin_emails", "owner@example.com")
    assert admins.admin_gate_active() is False
    assert require_admin("change settings") is None

    monkeypatch.setattr(config.settings, "identity_provider", "oidc")
    assert admins.admin_gate_active() is True
    monkeypatch.setattr(identity, "active_provider", lambda: "oidc")
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        require_admin("change settings")
    assert exc.value.status_code == 403
