"""Verified identity sources: OIDC, trusted proxy header, and the guards.

The point of these tests is not that a good token is accepted — it is that a
bad one is refused, and that a configuration which *cannot* enforce identity
refuses to start rather than pretending. PRIVATE_COLLECTIONS is only as
trustworthy as this layer.
"""

import base64
import hashlib
import hmac
import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from config import settings
from services import identity as identity_service
from services.identity import (
    IdentityRefused,
    JWKSCache,
    OIDCIdentity,
    TrustedHeaderIdentity,
    _claims_satisfy,
    _parse_required_claims,
)

ISSUER = "https://sso.agency.test/realms/main"
AUDIENCE = "clio"


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def keypair():
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


def _b64(n: int, length: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()


def _jwk(private_key, kid):
    numbers = private_key.public_key().public_numbers()
    return {
        "kid": kid,
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "n": _b64(numbers.n, 256),
        "e": _b64(numbers.e, 3),
    }


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def oidc(keypair, monkeypatch):
    """An OIDC verifier wired to a fake IdP: discovery document + JWKS."""
    key, _ = keypair
    jwks = {"keys": [_jwk(key, "kid-1")]}
    discovery = {"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"}

    def fake_urlopen(url, timeout=10):
        if url.endswith("/.well-known/openid-configuration"):
            return _FakeResp(discovery)
        return _FakeResp(jwks)

    monkeypatch.setattr("services.identity.urllib.request.urlopen", fake_urlopen)
    return OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE])


def _mint(private_key, kid="kid-1", alg="RS256", **overrides):
    claims = {
        "aud": AUDIENCE,
        "iss": ISSUER,
        "exp": int(time.time()) + 300,
        "iat": int(time.time()),
        "email": "analyst@agency.test",
    }
    claims.update(overrides)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pyjwt.encode(claims, pem, algorithm=alg, headers={"kid": kid})


def _headers(token):
    return {"authorization": f"Bearer {token}"}


# ── OIDC ────────────────────────────────────────────────────────────────────


def test_valid_token_resolves_to_the_identity_claim(oidc, keypair):
    key, _ = keypair
    assert oidc.verify_request(_headers(_mint(key)), None) == "analyst@agency.test"


def test_token_signed_by_another_key_is_refused(oidc, keypair):
    _, impostor = keypair
    assert oidc.verify_request(_headers(_mint(impostor)), None) is None


def test_expired_token_is_refused(oidc, keypair):
    key, _ = keypair
    token = _mint(key, exp=int(time.time()) - 60)
    assert oidc.verify_request(_headers(token), None) is None


def test_token_without_an_expiry_is_refused(oidc, keypair):
    """PyJWT only checks `exp` when present; a token without one would be
    valid forever, so its absence is a refusal, not a pass."""
    key, _ = keypair
    claims = {"aud": AUDIENCE, "iss": ISSUER, "iat": int(time.time()),
              "email": "analyst@agency.test"}
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    token = pyjwt.encode(claims, pem, algorithm="RS256", headers={"kid": "kid-1"})
    assert oidc.verify_request(_headers(token), None) is None


def test_token_for_another_audience_is_refused(oidc, keypair):
    """The check that stops every other app's tokens from working here."""
    key, _ = keypair
    assert oidc.verify_request(_headers(_mint(key, aud="some-other-app")), None) is None


def test_token_from_another_issuer_is_refused(oidc, keypair):
    key, _ = keypair
    token = _mint(key, iss="https://evil.test/realms/main")
    assert oidc.verify_request(_headers(token), None) is None


def test_hs256_token_is_refused_even_with_a_known_kid(oidc):
    """Algorithm confusion: a symmetric token must never be accepted, or the
    published JWKS modulus becomes a signing key anyone can use."""
    token = pyjwt.encode(
        {"aud": AUDIENCE, "iss": ISSUER, "exp": int(time.time()) + 300,
         "email": "attacker@evil.test"},
        "secret",
        algorithm="HS256",
        headers={"kid": "kid-1"},
    )
    assert oidc.verify_request(_headers(token), None) is None


def test_non_jwt_bearer_is_left_for_the_other_auth_paths(oidc):
    """The shared password and personal MCP tokens ride the same header."""
    assert oidc.verify_request({"authorization": "Bearer asy_mcp_abc123"}, None) is None
    assert oidc.verify_request({"authorization": "Bearer hunter2"}, None) is None


def test_missing_and_non_bearer_headers_yield_no_identity(oidc):
    assert oidc.verify_request({}, None) is None
    assert oidc.verify_request({"authorization": "Basic dXNlcjpwdw=="}, None) is None


def test_has_candidate_matches_what_verify_would_consider(oidc, keypair):
    """The middleware skips the worker thread when has_candidate is false, so
    it must never be false for a request verify_request would have accepted."""
    key, _ = keypair
    assert oidc.has_candidate(_headers(_mint(key))) is True
    for headers in ({}, {"authorization": "Basic dXNlcjpwdw=="},
                    {"authorization": "Bearer hunter2"},
                    {"authorization": "Bearer asy_mcp_abc"}):
        assert oidc.has_candidate(headers) is False
        assert oidc.verify_request(headers, None) is None


def test_trusted_header_candidate_check(keypair):
    v = TrustedHeaderIdentity("X-Forwarded-User", trusted_proxies="10.0.0.0/8")
    assert v.has_candidate({"x-forwarded-user": "someone@x.test"}) is True
    assert v.has_candidate({"x-forwarded-user": "  "}) is False
    assert v.has_candidate({}) is False


def test_required_claims_gate_admission(keypair, monkeypatch):
    key, _ = keypair
    jwks = {"keys": [_jwk(key, "kid-1")]}
    monkeypatch.setattr(
        "services.identity.urllib.request.urlopen",
        lambda url, timeout=10: _FakeResp(jwks),
    )
    verifier = OIDCIdentity(
        issuer=ISSUER,
        audiences=[AUDIENCE],
        jwks_url=f"{ISSUER}/certs",
        required_claims="groups=clio-users",
    )
    # List claim: membership counts.
    ok = _mint(key, groups=["other-team", "clio-users"])
    assert verifier.verify_request(_headers(ok), None) == "analyst@agency.test"
    # Present but wrong, and absent entirely, are both refused.
    assert verifier.verify_request(_headers(_mint(key, groups=["other-team"])), None) is None
    assert verifier.verify_request(_headers(_mint(key)), None) is None


def test_identity_claim_is_configurable(keypair, monkeypatch):
    key, _ = keypair
    jwks = {"keys": [_jwk(key, "kid-1")]}
    monkeypatch.setattr(
        "services.identity.urllib.request.urlopen",
        lambda url, timeout=10: _FakeResp(jwks),
    )
    verifier = OIDCIdentity(
        issuer=ISSUER, audiences=[AUDIENCE], jwks_url=f"{ISSUER}/certs",
        identity_claim="preferred_username",
    )
    token = _mint(key, preferred_username="a.analyst")
    assert verifier.verify_request(_headers(token), None) == "a.analyst"


def test_explicit_jwks_url_skips_discovery(keypair, monkeypatch):
    """The air-gapped path: the issuer's .well-known may be unreachable."""
    key, _ = keypair
    seen = []

    def fake_urlopen(url, timeout=10):
        seen.append(url)
        return _FakeResp({"keys": [_jwk(key, "kid-1")]})

    monkeypatch.setattr("services.identity.urllib.request.urlopen", fake_urlopen)
    verifier = OIDCIdentity(
        issuer=ISSUER, audiences=[AUDIENCE], jwks_url="https://internal.test/certs"
    )
    assert verifier.verify_request(_headers(_mint(key)), None) == "analyst@agency.test"
    assert seen == ["https://internal.test/certs"]


def test_discovery_without_a_key_set_refuses_rather_than_admits(keypair, monkeypatch):
    """A discovery document that names no jwks_uri leaves nothing to verify
    against — that must refuse, not wave the request through."""
    key, _ = keypair
    monkeypatch.setattr(
        "services.identity.urllib.request.urlopen",
        lambda url, timeout=10: _FakeResp({"issuer": ISSUER}),
    )
    verifier = OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE])
    assert verifier.verify_request(_headers(_mint(key)), None) is None


def test_unreachable_idp_refuses_rather_than_admits(keypair, monkeypatch):
    key, _ = keypair

    def boom(url, timeout=10):
        raise OSError("network unreachable")

    monkeypatch.setattr("services.identity.urllib.request.urlopen", boom)
    verifier = OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE])
    assert verifier.verify_request(_headers(_mint(key)), None) is None


# ── Trusted header ──────────────────────────────────────────────────────────


def _sign(secret, value):
    return hmac.new(secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def test_header_accepted_from_a_trusted_proxy_address():
    v = TrustedHeaderIdentity("X-Forwarded-User", trusted_proxies="10.0.0.0/24")
    headers = {"x-forwarded-user": "analyst@agency.test"}
    assert v.verify_request(headers, "10.0.0.5") == "analyst@agency.test"


def test_header_refused_from_an_untrusted_address():
    v = TrustedHeaderIdentity("X-Forwarded-User", trusted_proxies="10.0.0.0/24")
    headers = {"x-forwarded-user": "analyst@agency.test"}
    assert v.verify_request(headers, "192.0.2.9") is None
    assert v.verify_request(headers, None) is None


def test_hmac_guard_accepts_only_a_correctly_signed_identity():
    secret = "s3cret"
    v = TrustedHeaderIdentity(
        "X-Forwarded-User", shared_secret=secret,
        signature_header="X-Forwarded-User-Signature",
    )
    who = "analyst@agency.test"
    good = {"x-forwarded-user": who, "x-forwarded-user-signature": _sign(secret, who)}
    assert v.verify_request(good, None) == who

    # Right signature, different identity: the forged name is not covered.
    forged = {"x-forwarded-user": "admin@agency.test",
              "x-forwarded-user-signature": _sign(secret, who)}
    assert v.verify_request(forged, None) is None

    # No signature at all.
    assert v.verify_request({"x-forwarded-user": who}, None) is None


def test_empty_header_is_not_an_identity():
    v = TrustedHeaderIdentity("X-Forwarded-User", trusted_proxies="10.0.0.0/8")
    assert v.verify_request({"x-forwarded-user": "   "}, "10.1.2.3") is None
    assert v.verify_request({}, "10.1.2.3") is None


# ── Startup validation ──────────────────────────────────────────────────────


def _settings_objects():
    """Every live Settings object in this process.

    Modules bind `settings` at import time, and fixtures elsewhere (test_auth,
    test_private_collections) reload `config` and replace `config.settings`
    with a fresh object — so the object this module imported and the one
    `services.identity` reads at call time can differ. Patch both, or these
    tests pass alone and fail in a full run.
    """
    import config

    objs = {id(settings): settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


@pytest.fixture()
def identity_settings(monkeypatch):
    """Isolate the identity settings; the repo's own .env sets some of them."""
    for field, value in (
        ("identity_provider", ""), ("private_collections", False), ("auth_require_identity", False),
        ("oidc_issuer", ""), ("oidc_audience", ""), ("oidc_jwks_url", ""),
        ("oidc_identity_claim", "email"), ("oidc_required_claims", ""),
        ("trusted_header_name", "X-Forwarded-User"), ("trusted_header_proxies", ""),
        ("trusted_header_secret", ""),
        ("trusted_header_signature_name", "X-Forwarded-User-Signature"),
        ("oidc_identity_fallback_claims", ""), ("oidc_require_email_verified", False),
        ("trusted_header_timestamp_name", "X-Forwarded-User-Timestamp"),
        ("trusted_header_max_skew_seconds", 300),
        ("trusted_header_allow_legacy_signature", True),
        ("host", "0.0.0.0"),
    ):
        for obj in _settings_objects():
            monkeypatch.setattr(obj, field, value)
    # The module caches one OIDC verifier (for its JWKS); a cached instance
    # from a previous test would carry that test's fake key set.
    monkeypatch.setattr(identity_service, "_oidc_verifier", None)
    monkeypatch.setattr(identity_service, "_oidc_signature", None)
    return _IdentitySettings(monkeypatch)


class _IdentitySettings:
    """Assigns a setting on every live Settings object at once.

    Tests read like `identity_settings.oidc_issuer = ...`; the write lands
    wherever `services.identity` will actually look for it.
    """

    def __init__(self, monkeypatch):
        object.__setattr__(self, "_monkeypatch", monkeypatch)

    def __setattr__(self, name, value):
        for obj in _settings_objects():
            self._monkeypatch.setattr(obj, name, value)

    def __getattr__(self, name):
        import config

        return getattr(config.settings, name)


def test_unknown_provider_refuses_to_start(identity_settings):
    identity_settings.identity_provider = "saml"
    with pytest.raises(RuntimeError, match="not a provider"):
        identity_service.validate_config()


def test_oidc_without_audience_refuses_to_start(identity_settings):
    """Without an audience, any token the IdP ever minted would be accepted."""
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    with pytest.raises(RuntimeError, match="OIDC_AUDIENCE"):
        identity_service.validate_config()


def test_oidc_without_issuer_refuses_to_start(identity_settings):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_audience = AUDIENCE
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        identity_service.validate_config()


def test_oidc_over_plain_http_refuses_to_start(identity_settings):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = "http://sso.agency.test/realms/main"
    identity_settings.oidc_audience = AUDIENCE
    with pytest.raises(RuntimeError, match="must be https"):
        identity_service.validate_config()


def test_oidc_config_that_can_be_enforced_starts(identity_settings):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    identity_service.validate_config()  # must not raise
    assert identity_service.active_provider() == "oidc"


def test_trusted_header_with_no_real_guard_refuses_to_start(identity_settings):
    """A CIDR allowlist alone is not a guard: uvicorn can rewrite the peer
    address from X-Forwarded-For, which the caller controls."""
    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_proxies = "10.0.0.0/24"
    with pytest.raises(RuntimeError, match="guard that holds"):
        identity_service.validate_config()


def test_trusted_header_guarded_by_loopback_bind_starts(identity_settings):
    identity_settings.identity_provider = "trusted_header"
    identity_settings.host = "127.0.0.1"
    identity_service.validate_config()  # must not raise


def test_trusted_header_guarded_by_hmac_starts(identity_settings):
    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_secret = "s3cret"
    identity_service.validate_config()  # must not raise


def test_hmac_secret_without_a_signature_header_refuses_to_start(identity_settings):
    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_secret = "s3cret"
    identity_settings.trusted_header_signature_name = ""
    with pytest.raises(RuntimeError, match="SIGNATURE_NAME"):
        identity_service.validate_config()


def test_private_collections_do_not_invent_an_identity_source(identity_settings):
    """Clio has no accounts: turning private collections on does not select a
    provider, so the startup check (main._check_security_posture) refuses it."""
    identity_settings.private_collections = True
    assert identity_service.active_provider() == ""
    assert identity_service.requires_identity() is False


def test_explicit_provider_is_used_with_private_collections(identity_settings):
    identity_settings.private_collections = True
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    assert identity_service.active_provider() == "oidc"
    identity_service.validate_config()  # must not raise


def test_requires_identity_follows_provider_and_flag(identity_settings):
    assert identity_service.requires_identity() is False
    identity_settings.auth_require_identity = True
    assert identity_service.requires_identity() is True
    identity_settings.auth_require_identity = False
    assert identity_service.requires_identity() is False


def test_no_identity_source_is_a_valid_appliance_config(identity_settings):
    assert identity_service.active_provider() == ""
    identity_service.validate_config()  # must not raise
    assert identity_service.get_identity_verifier() is None


def test_cloudflare_access_is_no_longer_a_provider(identity_settings):
    """A deployment still naming the removed provider must fail loudly at
    startup, not silently fall back to "everyone is anonymous"."""
    identity_settings.identity_provider = "cloudflare_access"
    with pytest.raises(RuntimeError, match="oidc, trusted_header"):
        identity_service.validate_config()


# ── Helpers ─────────────────────────────────────────────────────────────────


def test_no_verifier_is_built_when_nothing_is_configured(identity_settings):
    assert identity_service.get_identity_verifier() is None


def test_each_provider_builds_its_own_verifier(identity_settings):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    assert isinstance(identity_service.get_identity_verifier(), OIDCIdentity)

    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_secret = "s3cret"
    assert isinstance(identity_service.get_identity_verifier(), TrustedHeaderIdentity)


# ── Through the real auth middleware ────────────────────────────────────────


@pytest.fixture()
def oidc_app(identity_settings, keypair, monkeypatch, tmp_path):
    """The real app, reloaded with OIDC as its identity source."""
    import importlib

    import config
    import main
    from services.app_database import SQLiteBackend, app_db
    from services.collection_service import collection_service

    key, _ = keypair
    jwks = {"keys": [_jwk(key, "kid-1")]}
    discovery = {"jwks_uri": f"{ISSUER}/protocol/openid-connect/certs"}

    def fake_urlopen(url, timeout=10):
        if url.endswith("/.well-known/openid-configuration"):
            return _FakeResp(discovery)
        return _FakeResp(jwks)

    monkeypatch.setattr("services.identity.urllib.request.urlopen", fake_urlopen)

    old_db_path, old_base_dir = app_db.db_path, collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"

    # identity_settings already patched the identity fields on every live
    # Settings object; these two decide the middleware's shape.
    identity_settings.private_collections = True
    identity_settings.auth_password = ""
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE

    from fastapi.testclient import TestClient

    reloaded = importlib.reload(main)
    yield TestClient(reloaded.app)

    # monkeypatch restores the settings; main is reloaded afterwards so the
    # module-level middleware registration matches the restored config again.
    app_db.db_path, collection_service.base_dir = old_db_path, old_base_dir
    monkeypatch.undo()
    importlib.reload(main)


def test_a_valid_idp_token_authenticates_a_real_request(oidc_app, keypair):
    key, _ = keypair
    r = oidc_app.get("/api/collections", headers=_headers(_mint(key)))
    assert r.status_code == 200


def test_requests_without_a_token_are_rejected(oidc_app):
    assert oidc_app.get("/api/collections").status_code == 401
    assert oidc_app.get("/health").status_code == 200  # probes stay open


def test_a_token_from_an_impostor_is_rejected_end_to_end(oidc_app, keypair):
    _, impostor = keypair
    r = oidc_app.get("/api/collections", headers=_headers(_mint(impostor)))
    assert r.status_code == 401


def test_a_token_for_another_audience_is_rejected_end_to_end(oidc_app, keypair):
    key, _ = keypair
    r = oidc_app.get("/api/collections", headers=_headers(_mint(key, aud="other-app")))
    assert r.status_code == 401


# ── Helpers ─────────────────────────────────────────────────────────────────


def test_required_claims_parsing_tolerates_whitespace_and_junk():
    assert _parse_required_claims(" groups=a , dept=b ") == [("groups", "a"), ("dept", "b")]
    assert _parse_required_claims("no-equals-sign") == []
    assert _parse_required_claims("") == []


def test_claims_satisfy_matches_scalars_and_lists():
    assert _claims_satisfy({"dept": "research"}, [("dept", "research")])
    assert _claims_satisfy({"groups": ["a", "b"]}, [("groups", "b")])
    assert not _claims_satisfy({"groups": ["a"]}, [("groups", "b")])
    assert not _claims_satisfy({}, [("groups", "b")])


def test_loopback_detection(identity_settings):
    for host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        identity_settings.host = host
        identity_settings.identity_provider = "trusted_header"
        identity_service.validate_config()  # loopback is a guard: must not raise
    for host in ("0.0.0.0", "10.0.0.5", ""):
        identity_settings.host = host
        with pytest.raises(RuntimeError, match="guard that holds"):
            identity_service.validate_config()


# ── OIDC: which claim is the identity ───────────────────────────────────────


@pytest.fixture()
def jwks_verifier(keypair, monkeypatch):
    """Builds an OIDC verifier against a fixed key set, with any options."""
    key, _ = keypair
    monkeypatch.setattr(
        "services.identity.urllib.request.urlopen",
        lambda url, timeout=10: _FakeResp({"keys": [_jwk(key, "kid-1")]}),
    )

    def build(**options):
        return OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE], jwks_url=f"{ISSUER}/certs", **options)

    return build


@pytest.mark.parametrize("flag", [False, "false", "no", 0])
def test_an_email_the_idp_says_is_unverified_is_refused(jwks_verifier, keypair, flag):
    key, _ = keypair
    token = _mint(key, email="admin@agency.test", email_verified=flag)
    with pytest.raises(IdentityRefused, match="not verified"):
        jwks_verifier().verify_request(_headers(token), None)


@pytest.mark.parametrize("flag", [True, "true", "True"])
def test_a_verified_email_is_the_identity(jwks_verifier, keypair, flag):
    key, _ = keypair
    token = _mint(key, email_verified=flag)
    verifier = jwks_verifier(require_email_verified=True)
    assert verifier.verify_request(_headers(token), None) == "analyst@agency.test"


def test_absent_email_verified_is_accepted_unless_required(jwks_verifier, keypair):
    """Entra ID and ADFS never send the claim, so its absence is accepted by
    default; OIDC_REQUIRE_EMAIL_VERIFIED makes it a refusal."""
    key, _ = keypair
    token = _mint(key)  # no email_verified claim
    assert jwks_verifier().verify_request(_headers(token), None) == "analyst@agency.test"
    with pytest.raises(IdentityRefused, match="not verified"):
        jwks_verifier(require_email_verified=True).verify_request(_headers(token), None)


def test_email_verified_only_matters_when_email_is_the_identity(jwks_verifier, keypair):
    key, _ = keypair
    token = _mint(key, email_verified=False, upn="a.analyst@agency.test")
    verifier = jwks_verifier(identity_claim="upn", require_email_verified=True)
    assert verifier.verify_request(_headers(token), None) == "a.analyst@agency.test"


def test_a_missing_identity_claim_is_refused_not_replaced(jwks_verifier, keypair):
    """Without email the token used to become its preferred_username or sub,
    in the same namespace as emails, so a username that reads like an
    admin's address was that admin."""
    key, _ = keypair
    token = _mint(key, email=None, preferred_username="admin@agency.test", sub="admin@agency.test")
    with pytest.raises(IdentityRefused, match="no 'email' claim"):
        jwks_verifier().verify_request(_headers(token), None)


def test_explicit_fallback_claims_are_namespaced(jwks_verifier, keypair, monkeypatch):
    import config
    from services import admins

    monkeypatch.setattr(config.settings, "admin_emails", "admin@agency.test")
    key, _ = keypair
    verifier = jwks_verifier(fallback_claims="preferred_username, sub")
    token = _mint(key, email=None, preferred_username="admin@agency.test", sub="9f1c")
    who = verifier.verify_request(_headers(token), None)
    assert who == "preferred_username:admin@agency.test"
    assert not admins.is_admin(who)
    # Next in order when the first fallback is absent too.
    only_sub = _mint(key, email=None, sub="9f1c")
    assert verifier.verify_request(_headers(only_sub), None) == "sub:9f1c"
    # The identity claim itself, when present, is never prefixed.
    assert verifier.verify_request(_headers(_mint(key)), None) == "analyst@agency.test"


def test_non_text_claims_are_not_identities(jwks_verifier, keypair):
    key, _ = keypair
    token = _mint(key, email=["a@agency.test", "b@agency.test"])
    with pytest.raises(IdentityRefused):
        jwks_verifier().verify_request(_headers(token), None)


def test_identity_settings_reach_both_oidc_verifiers(identity_settings, monkeypatch):
    identity_settings.identity_provider = "oidc"
    identity_settings.oidc_issuer = ISSUER
    identity_settings.oidc_audience = AUDIENCE
    identity_settings.oidc_identity_fallback_claims = "sub"
    identity_settings.oidc_require_email_verified = True
    identity_settings.mcp_oauth_issuer = ""
    monkeypatch.setattr(identity_service, "_mcp_verifier", None)
    monkeypatch.setattr(identity_service, "_mcp_signature", None)
    for verifier in (identity_service.get_identity_verifier(), identity_service.get_mcp_oauth_verifier()):
        assert verifier.fallback_claims == ["sub"]
        assert verifier.require_email_verified is True


def test_a_token_without_an_identity_gets_401_with_the_reason(oidc_app, keypair):
    key, _ = keypair
    r = oidc_app.get("/api/collections", headers=_headers(_mint(key, email=None, sub="9f1c")))
    assert r.status_code == 401
    assert r.json()["code"] == "identity_refused"
    assert "'email' claim" in r.json()["detail"]
    unverified = _mint(key, email_verified=False)
    r = oidc_app.get("/api/collections", headers=_headers(unverified))
    assert r.status_code == 401 and "not verified" in r.json()["detail"]
    # /health never refuses, and never errors over it either.
    assert oidc_app.get("/health", headers=_headers(unverified)).status_code == 200


# ── OIDC: key set refresh ───────────────────────────────────────────────────


class _CountingIdP:
    """A JWKS endpoint whose key set the test can rotate, counting fetches."""

    def __init__(self, keys):
        self.keys = keys
        self.fetches = 0
        self.down = False

    def __call__(self, url, timeout=10):
        self.fetches += 1
        if self.down:
            raise OSError("network unreachable")
        return _FakeResp({"keys": self.keys})


@pytest.fixture()
def clock(monkeypatch):
    """The monotonic clock the JWKS cooldown reads, under test control."""
    now = [1000.0]
    monkeypatch.setattr("services.identity.time.monotonic", lambda: now[0])
    return now


def test_forged_key_ids_do_not_each_force_a_refetch(keypair, monkeypatch, clock):
    key, _ = keypair
    idp = _CountingIdP([_jwk(key, "kid-1")])
    monkeypatch.setattr("services.identity.urllib.request.urlopen", idp)
    verifier = OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE], jwks_url=f"{ISSUER}/certs")
    assert verifier.verify_request(_headers(_mint(key)), None) == "analyst@agency.test"
    assert idp.fetches == 1

    for n in range(50):
        forged = _mint(key, kid=f"forged-{n}")
        assert verifier.verify_request(_headers(forged), None) is None
    assert idp.fetches == 2  # one forced refetch, then the cooldown holds

    clock[0] += identity_service._JWKS_REFRESH_COOLDOWN_SECONDS
    assert verifier.verify_request(_headers(_mint(key, kid="forged-late")), None) is None
    assert idp.fetches == 3  # at most one per cooldown


def test_key_rotation_is_still_picked_up(keypair, monkeypatch, clock):
    old, new = keypair
    idp = _CountingIdP([_jwk(old, "kid-1")])
    monkeypatch.setattr("services.identity.urllib.request.urlopen", idp)
    verifier = OIDCIdentity(issuer=ISSUER, audiences=[AUDIENCE], jwks_url=f"{ISSUER}/certs")
    assert verifier.verify_request(_headers(_mint(old)), None) == "analyst@agency.test"

    # The IdP rotates: the first token signed with the new key refreshes.
    idp.keys = [_jwk(new, "kid-2")]
    assert verifier.verify_request(_headers(_mint(new, kid="kid-2")), None) == "analyst@agency.test"

    # A rotation that lands while a forged token holds the cooldown is
    # picked up once the cooldown has passed.
    clock[0] += identity_service._JWKS_REFRESH_COOLDOWN_SECONDS
    assert verifier.verify_request(_headers(_mint(new, kid="forged")), None) is None
    idp.keys = [_jwk(old, "kid-3")]
    clock[0] += 1
    assert verifier.verify_request(_headers(_mint(old, kid="kid-3")), None) is None
    clock[0] += identity_service._JWKS_REFRESH_COOLDOWN_SECONDS
    assert verifier.verify_request(_headers(_mint(old, kid="kid-3")), None) == "analyst@agency.test"


def test_an_unreachable_idp_is_not_retried_on_every_request(keypair, monkeypatch, clock):
    key, _ = keypair
    idp = _CountingIdP([_jwk(key, "kid-1")])
    idp.down = True
    monkeypatch.setattr("services.identity.urllib.request.urlopen", idp)
    cache = JWKSCache(f"{ISSUER}/certs")
    for _ in range(20):
        assert cache.get("kid-1") is None
    assert idp.fetches == 1

    idp.down = False
    clock[0] += identity_service._JWKS_REFRESH_COOLDOWN_SECONDS
    assert cache.get("kid-1") is not None
    assert idp.fetches == 2


# ── Trusted header: timestamped signatures ──────────────────────────────────

TS_SECRET = "s3cret"


def _stamped(who, stamp, secret=TS_SECRET):
    return {
        "x-forwarded-user": who,
        "x-forwarded-user-timestamp": str(stamp),
        "x-forwarded-user-signature": _sign(secret, f"{who}|{stamp}"),
    }


def _ts_verifier(**options):
    return TrustedHeaderIdentity(
        "X-Forwarded-User", shared_secret=TS_SECRET,
        signature_header="X-Forwarded-User-Signature",
        timestamp_header="X-Forwarded-User-Timestamp", **options,
    )


def test_a_fresh_timestamped_signature_is_accepted():
    who = "analyst@agency.test"
    assert _ts_verifier().verify_request(_stamped(who, int(time.time())), None) == who
    # Clock skew within the window either way is fine.
    assert _ts_verifier().verify_request(_stamped(who, int(time.time()) + 200), None) == who


def test_a_captured_header_set_stops_working_after_the_skew_window(monkeypatch):
    who = "analyst@agency.test"
    now = int(time.time())
    captured = _stamped(who, now)
    verifier = _ts_verifier(max_skew_seconds=300)
    assert verifier.verify_request(captured, None) == who
    monkeypatch.setattr("services.identity.time.time", lambda: now + 301)
    assert verifier.verify_request(captured, None) is None


def test_the_timestamp_is_covered_by_the_signature():
    who = "analyst@agency.test"
    replayed = _stamped(who, int(time.time()) - 3600)
    replayed["x-forwarded-user-timestamp"] = str(int(time.time()))  # freshened, not re-signed
    assert _ts_verifier().verify_request(replayed, None) is None
    # Nor does the signature carry over to another identity.
    forged = _stamped(who, int(time.time()))
    forged["x-forwarded-user"] = "admin@agency.test"
    assert _ts_verifier().verify_request(forged, None) is None


@pytest.mark.parametrize("stamp", ["12a", "-5", "1.5", "１２３", "9" * 20])
def test_a_malformed_timestamp_is_refused(stamp):
    who = "analyst@agency.test"
    headers = {
        "x-forwarded-user": who,
        "x-forwarded-user-timestamp": stamp,
        "x-forwarded-user-signature": _sign(TS_SECRET, f"{who}|{stamp}"),
    }
    assert _ts_verifier().verify_request(headers, None) is None


def test_legacy_signatures_are_accepted_only_while_allowed(caplog, monkeypatch):
    monkeypatch.setattr(identity_service, "_legacy_signature_noted", False)
    who = "analyst@agency.test"
    legacy = {"x-forwarded-user": who, "x-forwarded-user-signature": _sign(TS_SECRET, who)}
    with caplog.at_level("WARNING", logger="services.identity"):
        assert _ts_verifier().verify_request(legacy, None) == who
        assert _ts_verifier().verify_request(legacy, None) == who
    deprecations = [r for r in caplog.records if "deprecated" in r.getMessage()]
    assert len(deprecations) == 1  # once per process, not per request
    assert _ts_verifier(allow_legacy_signature=False).verify_request(legacy, None) is None


def test_a_timestamped_signature_is_not_a_legacy_one_for_another_identity():
    """HMAC("x|T") is the new-format signature for "x" at T. Presented with no
    timestamp header it must not pass as a legacy signature for "x|T"."""
    stamp = int(time.time())
    headers = {
        "x-forwarded-user": f"analyst@agency.test|{stamp}",
        "x-forwarded-user-signature": _sign(TS_SECRET, f"analyst@agency.test|{stamp}"),
    }
    assert _ts_verifier().verify_request(headers, None) is None


def test_trusted_header_settings_reach_the_verifier(identity_settings):
    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_secret = TS_SECRET
    identity_settings.trusted_header_max_skew_seconds = 30
    identity_settings.trusted_header_allow_legacy_signature = False
    verifier = identity_service.get_identity_verifier()
    assert verifier.timestamp_header == "x-forwarded-user-timestamp"
    assert verifier.max_skew_seconds == 30 and verifier.allow_legacy_signature is False


def test_signature_settings_that_admit_nothing_refuse_to_start(identity_settings):
    identity_settings.identity_provider = "trusted_header"
    identity_settings.trusted_header_secret = TS_SECRET
    identity_settings.trusted_header_timestamp_name = ""
    identity_settings.trusted_header_allow_legacy_signature = False
    with pytest.raises(RuntimeError, match="TRUSTED_HEADER_TIMESTAMP_NAME"):
        identity_service.validate_config()
    identity_settings.trusted_header_timestamp_name = "X-Forwarded-User-Timestamp"
    identity_settings.trusted_header_max_skew_seconds = 0
    with pytest.raises(RuntimeError, match="TRUSTED_HEADER_MAX_SKEW_SECONDS"):
        identity_service.validate_config()


def test_the_secure_profile_refuses_legacy_signatures(monkeypatch):
    import config

    monkeypatch.delenv("TRUSTED_HEADER_ALLOW_LEGACY_SIGNATURE", raising=False)
    # Offline mode would set HF_HUB_OFFLINE for the rest of the run.
    monkeypatch.setenv("OFFLINE_MODE", "false")
    monkeypatch.setenv("CLIO_PROFILE", "secure")
    assert config.Settings().trusted_header_allow_legacy_signature is False
    monkeypatch.setenv("CLIO_PROFILE", "standard")
    assert config.Settings().trusted_header_allow_legacy_signature is True
