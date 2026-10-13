"""OAuth 2.1 on /mcp: metadata, challenge, and - above all - the refusals.

The app is the resource server for its MCP endpoint and the site's IdP is
the authorization server. What these tests pin is the boundary that makes
that safe: a bearer JWT on /mcp is admitted only when the same OIDC code
path a browser session uses says so (signature, issuer, audience, expiry),
plus the two rules specific to /mcp - the canonical resource URL counts as
an audience, and a required scope is enforced with the 403 the spec
defines rather than a 401 the client cannot fix.
"""

import asyncio
import importlib
import time

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from services import identity as identity_service
from tests.test_identity import (
    AUDIENCE,
    ISSUER,
    _FakeResp,
    _jwk,
    _mint,
    identity_settings,  # noqa: F401 - fixture, used by name below
    keypair,  # noqa: F401 - fixture, used by name below
)

PUBLIC_URL = "http://localhost:8473/mcp"
MCP_ISSUER = "https://sso.other.test/realms/mcp"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_idp(keypair, monkeypatch):
    """Discovery + JWKS for both issuers, served by one signing key."""
    key, _ = keypair
    jwks = {"keys": [_jwk(key, "kid-1")]}

    def fake_urlopen(url, timeout=10):
        if url.endswith("/.well-known/openid-configuration"):
            issuer = url[: -len("/.well-known/openid-configuration")]
            return _FakeResp({"jwks_uri": f"{issuer}/protocol/openid-connect/certs"})
        return _FakeResp(jwks)

    monkeypatch.setattr("services.identity.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(identity_service, "_mcp_verifier", None)
    monkeypatch.setattr(identity_service, "_mcp_signature", None)
    return key


@pytest.fixture()
def oidc_mcp(identity_settings, fake_idp):
    """An oidc deployment: the IdP is reused on /mcp with no extra settings."""
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    identity_settings.mcp_public_url = PUBLIC_URL
    identity_settings.mcp_oauth_issuer = ""
    identity_settings.mcp_oauth_audience = ""
    identity_settings.mcp_oauth_jwks_url = ""
    identity_settings.mcp_oauth_scope = ""
    identity_settings.mcp_allowed_hosts = ""
    # A password keeps the gate closed for anonymous callers without needing
    # private collections (and their database) in these tests.
    identity_settings.auth_password = "hunter2"
    identity_settings.private_collections = False
    return identity_settings


@pytest.fixture()
def app(oidc_mcp):
    """The real app reloaded with the patched settings; bare client, so the
    middleware runs but the mounted MCP session manager does not start."""
    import main

    reloaded = importlib.reload(main)
    yield TestClient(reloaded.app)
    importlib.reload(main)


def _request(path, token=None, method="POST"):
    headers = [(b"host", b"localhost:8473")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return Request({"type": "http", "method": method, "path": path, "headers": headers,
                    "query_string": b"", "scheme": "http", "server": ("localhost", 8473)})


async def _downstream(_request):
    return "downstream"


def _auth(path, token, main_module=None):
    """Run the real auth middleware on one request; returns the response or
    the downstream marker."""
    import main

    module = main_module or main
    request = _request(path, token)
    result = asyncio.run(module.require_auth(request, _downstream))
    return request, result


# ── Metadata ────────────────────────────────────────────────────────────────


def test_protected_resource_metadata_names_the_idp(app):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        r = app.get(path)
        assert r.status_code == 200, path
        doc = r.json()
        assert doc["resource"] == PUBLIC_URL
        assert doc["authorization_servers"] == [ISSUER]
        assert doc["bearer_methods_supported"] == ["header"]
        assert "scopes_supported" not in doc
        assert "registration_endpoint" not in doc  # registration is the IdP's job
        assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_metadata_is_public_before_any_credential(app):
    """Read without auth even though the rest of the API answers 401."""
    assert app.get("/api/collections").status_code == 401
    assert app.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_metadata_advertises_the_required_scope(app, oidc_mcp):
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    assert app.get("/.well-known/oauth-protected-resource/mcp").json()["scopes_supported"] == ["clio:mcp"]


def test_metadata_is_absent_when_no_authorization_server_applies(identity_settings, fake_idp):
    identity_settings.identity_provider = ""
    identity_settings.mcp_oauth_issuer = ""
    identity_settings.auth_password = "hunter2"
    import main

    reloaded = importlib.reload(main)
    try:
        client = TestClient(reloaded.app)
        assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 404
        r = client.post("/mcp/")
        assert r.status_code == 401
        assert r.headers["WWW-Authenticate"] == 'Bearer realm="Clio"'
    finally:
        importlib.reload(main)


def test_resource_url_is_derived_from_the_request_only_when_unconfigured(app, oidc_mcp):
    oidc_mcp.mcp_public_url = ""
    doc = app.get("/.well-known/oauth-protected-resource/mcp",
                  headers={"host": "clio.agency.test", "x-forwarded-proto": "https"}).json()
    assert doc["resource"] == "https://clio.agency.test/mcp"
    # ...but a derived resource is never an accepted audience.
    verifier = identity_service.get_mcp_oauth_verifier()
    assert "https://clio.agency.test/mcp" not in verifier.audiences


def test_resource_url_falls_back_to_mcp_allowed_hosts(oidc_mcp):
    oidc_mcp.mcp_public_url = ""
    oidc_mcp.mcp_allowed_hosts = "clio.internal, other.internal"
    assert identity_service.mcp_public_url() == "https://clio.internal/mcp"
    oidc_mcp.mcp_allowed_hosts = "localhost:8473"
    assert identity_service.mcp_public_url() == "http://localhost:8473/mcp"


# ── Challenge ───────────────────────────────────────────────────────────────


def test_mcp_401_carries_the_bearer_challenge_and_browsers_keep_basic(app):
    r = app.post("/mcp/")
    assert r.status_code == 401
    www = r.headers["WWW-Authenticate"]
    assert www.startswith("Bearer ")
    assert 'resource_metadata="http://localhost:8473/.well-known/oauth-protected-resource/mcp"' in www
    assert "error=" not in www  # nothing was presented, so nothing was invalid

    assert app.get("/api/collections").headers["WWW-Authenticate"] == 'Basic realm="Clio"'


def test_challenge_names_the_scope_and_flags_a_refused_token(app, oidc_mcp):
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    r = app.post("/mcp/", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401
    www = r.headers["WWW-Authenticate"]
    assert 'scope="clio:mcp"' in www
    assert 'error="invalid_token"' in www


def test_challenge_parses_with_the_reference_client(app):
    """The MCP SDK's own client is what Claude Code and friends model."""
    from mcp.client.auth.utils import extract_resource_metadata_from_www_auth

    r = app.post("/mcp/")
    assert extract_resource_metadata_from_www_auth(r) == (
        "http://localhost:8473/.well-known/oauth-protected-resource/mcp"
    )


# ── Token validation on /mcp: the refusals ──────────────────────────────────


def test_a_valid_idp_token_is_admitted_on_mcp_as_the_person(app, fake_idp):
    request, result = _auth("/mcp/", _mint(fake_idp))
    assert result == "downstream"
    assert request.state.auth_identity == "analyst@agency.test"
    assert request.state.auth_via == "oauth"


def test_the_canonical_resource_url_is_an_accepted_audience(app, fake_idp):
    """RFC 8707: an IdP that honours `resource=` mints aud=<MCP URL>."""
    _, result = _auth("/mcp/", _mint(fake_idp, aud=PUBLIC_URL))
    assert result == "downstream"


@pytest.mark.parametrize("label, overrides", [
    ("wrong audience", {"aud": "other-app"}),
    ("wrong resource", {"aud": "https://other.agency.test/mcp"}),
    ("wrong issuer", {"iss": "https://sso.attacker.test/realms/main"}),
    ("expired", {"exp": int(time.time()) - 60}),
])
def test_refused_tokens_get_401_with_the_challenge(app, fake_idp, label, overrides):
    request, result = _auth("/mcp/", _mint(fake_idp, **overrides))
    assert result != "downstream", label
    assert result.status_code == 401, label
    www = result.headers["WWW-Authenticate"]
    assert 'error="invalid_token"' in www and "resource_metadata=" in www, label


def test_token_without_an_expiry_is_refused(app, fake_idp, keypair):
    from cryptography.hazmat.primitives import serialization

    key, _ = keypair
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption())
    token = pyjwt.encode({"aud": AUDIENCE, "iss": ISSUER, "email": "x@agency.test"}, pem,
                         algorithm="RS256", headers={"kid": "kid-1"})
    _, result = _auth("/mcp/", token)
    assert result.status_code == 401


def test_forged_signature_is_refused(app, keypair, fake_idp):
    _, impostor = keypair
    _, result = _auth("/mcp/", _mint(impostor))
    assert result.status_code == 401


def test_hs256_with_a_known_kid_is_refused(app, fake_idp):
    """The published RSA modulus must never become an HMAC key."""
    token = pyjwt.encode({"aud": AUDIENCE, "iss": ISSUER, "exp": int(time.time()) + 300,
                          "email": "x@agency.test"}, "shared-secret", algorithm="HS256",
                         headers={"kid": "kid-1"})
    _, result = _auth("/mcp/", token)
    assert result.status_code == 401


def test_token_valid_for_the_app_but_lacking_the_mcp_scope_gets_403(app, oidc_mcp, fake_idp):
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    _, result = _auth("/mcp/", _mint(fake_idp, scope="openid profile"))
    assert result.status_code == 403
    www = result.headers["WWW-Authenticate"]
    assert 'error="insufficient_scope"' in www
    assert 'scope="clio:mcp"' in www
    assert "resource_metadata=" in www


@pytest.mark.parametrize("claims", [
    {"scope": "openid clio:mcp"},
    {"scp": "clio:mcp"},
    {"scp": ["openid", "clio:mcp"]},
])
def test_scope_is_accepted_however_the_idp_spells_it(app, oidc_mcp, fake_idp, claims):
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    _, result = _auth("/mcp/", _mint(fake_idp, **claims))
    assert result == "downstream"


def test_scope_is_only_demanded_on_mcp(app, oidc_mcp, fake_idp):
    """The same token, without the scope, is still a fine browser session."""
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    request, result = _auth("/api/collections", _mint(fake_idp, scope="openid"))
    assert result == "downstream"
    assert request.state.auth_via == "oidc"


def test_scope_check_never_rescues_an_otherwise_invalid_token(app, oidc_mcp, keypair, fake_idp):
    oidc_mcp.mcp_oauth_scope = "clio:mcp"
    _, impostor = keypair
    _, result = _auth("/mcp/", _mint(impostor, scope="clio:mcp"))
    assert result.status_code == 401


def test_personal_tokens_and_the_password_still_work_beside_oauth(app, oidc_mcp, tmp_path, monkeypatch):
    from services import app_database, mcp_tokens

    monkeypatch.setattr(app_database.app_db, "db_path", tmp_path / "app.db")
    app_database.app_db._init_db()
    record = mcp_tokens.generate_token("alice", "CI")
    request, result = _auth("/mcp/", record["token"])
    assert result == "downstream"
    assert request.state.auth_via == "mcp_token"

    oidc_mcp.auth_password = "hunter2"
    request, result = _auth("/mcp/", "hunter2")
    assert result == "downstream"
    assert request.state.auth_via == "password"


# ── A separate authorization server for /mcp ────────────────────────────────


@pytest.fixture()
def separate_issuer(identity_settings, fake_idp):
    """A password-gated deployment (no bearer identity for browsers) that
    names an IdP for its MCP clients only."""
    identity_settings.identity_provider = ""
    identity_settings.mcp_oauth_issuer = MCP_ISSUER
    identity_settings.mcp_oauth_audience = "clio-mcp"
    identity_settings.mcp_oauth_scope = ""
    identity_settings.mcp_public_url = PUBLIC_URL
    identity_settings.auth_password = "hunter2"
    identity_settings.private_collections = False
    import main

    reloaded = importlib.reload(main)
    yield reloaded, identity_settings
    importlib.reload(main)


def test_mcp_issuer_tokens_are_admitted_on_mcp_and_confined_to_it(separate_issuer, fake_idp):
    main_module, _ = separate_issuer
    token = _mint(fake_idp, iss=MCP_ISSUER, aud="clio-mcp")

    request, result = _auth("/mcp/", token, main_module)
    assert result == "downstream"
    assert request.state.auth_identity == "analyst@agency.test"

    # The same token is not an identity anywhere else: /api is the password's.
    _, result = _auth("/api/collections", token, main_module)
    assert result.status_code == 401

    # And the site's own IdP tokens mean nothing on /mcp here.
    _, result = _auth("/mcp/", _mint(fake_idp), main_module)
    assert result.status_code == 401


def test_metadata_for_a_separate_issuer(separate_issuer):
    main_module, _ = separate_issuer
    doc = TestClient(main_module.app).get("/.well-known/oauth-protected-resource/mcp").json()
    assert doc["authorization_servers"] == [MCP_ISSUER]


def test_chained_verifier_keeps_the_deployment_identity_on_mcp(separate_issuer):
    """A proxy-vouched client on /mcp is still verified by the trusted-header
    verifier when an MCP issuer is added beside it."""
    _, settings = separate_issuer
    settings.identity_provider = "trusted_header"
    settings.trusted_header_name = "X-Forwarded-User"
    settings.host = "127.0.0.1"  # the guard that makes trusted_header valid
    verifier = identity_service.get_identity_verifier("/mcp/")
    headers = {"x-forwarded-user": "svc@agency.test"}
    assert verifier.has_candidate(headers) is True
    assert verifier.verify_request(headers, None) == "svc@agency.test"
    assert verifier.via == "trusted-header"


# ── Startup refusals ────────────────────────────────────────────────────────


def test_mcp_issuer_without_an_audience_refuses_to_start(identity_settings):
    identity_settings.mcp_oauth_issuer = MCP_ISSUER
    identity_settings.mcp_oauth_audience = ""
    with pytest.raises(RuntimeError, match="MCP_OAUTH_AUDIENCE"):
        identity_service.validate_config()


def test_plaintext_mcp_issuer_refuses_to_start(identity_settings):
    identity_settings.mcp_oauth_issuer = "http://sso.other.test/realms/mcp"
    identity_settings.mcp_oauth_audience = "clio-mcp"
    with pytest.raises(RuntimeError, match="must be https"):
        identity_service.validate_config()


def test_scope_without_an_authorization_server_refuses_to_start(identity_settings):
    identity_settings.mcp_oauth_scope = "clio:mcp"
    with pytest.raises(RuntimeError, match="MCP_OAUTH_SCOPE"):
        identity_service.validate_config()


@pytest.mark.parametrize("bad", ["clio.agency.test/mcp", "https://x.test/mcp?x=1", "ftp://x.test/mcp"])
def test_unusable_public_url_refuses_to_start(identity_settings, bad):
    identity_settings.mcp_public_url = bad
    with pytest.raises(RuntimeError, match="MCP_PUBLIC_URL"):
        identity_service.validate_config()


def test_oidc_deployment_needs_nothing_else_to_offer_oauth_on_mcp(identity_settings):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    identity_settings.mcp_public_url = "https://clio.agency.test/mcp"
    identity_service.validate_config()  # must not raise
    config = identity_service.mcp_oauth_config()
    assert config.issuer == ISSUER
    assert set(config.audiences) == {AUDIENCE, "https://clio.agency.test/mcp"}


def test_status_endpoint_tells_the_ui_what_to_paste(app):
    r = app.get("/api/mcp/oauth", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401  # it sits behind normal app auth like the rest


def test_status_payload(app, oidc_mcp, fake_idp):
    from services import mcp_oauth

    request = _request("/api/mcp/oauth", method="GET")
    assert mcp_oauth.summary(request) == {
        "enabled": True,
        "resource": PUBLIC_URL,
        "authorization_server": ISSUER,
        "scope": "",
        "metadata_url": "http://localhost:8473/.well-known/oauth-protected-resource/mcp",
    }
