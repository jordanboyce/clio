"""Private-collections mode: ownership, sharing, and enforcement.

The mode is opt-in (PRIVATE_COLLECTIONS=true) and requires Cloudflare Access
so every request carries a verified identity. These tests cover the three
layers separately:

- service rules: check_collection_access / get_all_collections visibility
- startup posture: the flag refuses to start without an identity source
- HTTP enforcement: two identities exercising the real routers end-to-end
  (the Access verifier is faked; the JWT crypto itself is covered by
  test_access_jwt.py)
"""

import importlib

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import config
from config import settings as _import_time_settings
from services.app_database import SQLiteBackend, app_db
from services.collection_service import collection_service
from services.sharing_service import sharing_service
from middleware.user_context import set_request_user, reset_request_user, set_request_identity, reset_request_identity

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture()
def fresh_db(tmp_path):
    """Point the app_db singleton (shared by every service) at a temp SQLite
    file, seeded with the default team collection, and restore afterwards."""
    old_db_path = app_db.db_path
    old_base_dir = collection_service.base_dir
    app_db.db_path = tmp_path / "app.db"
    if isinstance(app_db, SQLiteBackend):
        app_db._init_db()
    collection_service.base_dir = tmp_path / "collections"
    yield app_db
    app_db.db_path = old_db_path
    collection_service.base_dir = old_base_dir


def _settings_objects():
    """Every Settings object a module in this process might hold.

    Modules bind `settings` at their import time; test fixtures that reload
    config (here and in test_auth.py) replace config.settings with a fresh
    object, so the import-time object and the current one can differ. Patching
    both keeps the flip visible to every module regardless of test order.
    """
    objs = {id(_import_time_settings): _import_time_settings}
    objs.setdefault(id(config.settings), config.settings)
    return list(objs.values())


def _flip_private_mode():
    saved = []
    for s in _settings_objects():
        saved.append((s, s.private_collections, s.cf_access_team_domain, s.cf_access_aud))
        s.private_collections = True
        s.cf_access_team_domain = "testteam.cloudflareaccess.com"
        s.cf_access_aud = "aud-test"
    return saved


def _restore(saved):
    for s, private, team, aud in saved:
        s.private_collections = private
        s.cf_access_team_domain = team
        s.cf_access_aud = aud


@pytest.fixture()
def private_mode(fresh_db):
    """Flip the shared settings object(s) into private-collections mode."""
    saved = _flip_private_mode()
    yield config.settings
    _restore(saved)


def _make_private(owner):
    return app_db.create_collection(name=f"{owner}'s notes", owner_id=owner)


# ── Service rules ───────────────────────────────────────────────────────────


def test_mode_off_everyone_is_owner(fresh_db):
    assert sharing_service.check_collection_access("anything", ALICE) == "owner"


def test_team_collection_open_to_all(private_mode):
    """The seeded 'default' collection is owned by 'default' → team tier."""
    assert sharing_service.check_collection_access("default", ALICE) == "owner"
    assert sharing_service.check_collection_access("default", BOB) == "owner"
    # Anonymous password callers reach team collections too
    assert sharing_service.check_collection_access("default", None) == "owner"


def test_private_collection_hidden_from_others(private_mode):
    cid = _make_private(ALICE)
    assert sharing_service.check_collection_access(cid, ALICE) == "owner"
    assert sharing_service.check_collection_access(cid, BOB) is None
    assert sharing_service.check_collection_access(cid, None) is None


def test_unknown_collection_is_none(private_mode):
    assert sharing_service.check_collection_access("nope1234", ALICE) is None


def test_share_grants_declared_permission(private_mode):
    cid = _make_private(ALICE)
    share = sharing_service.create_share(cid, ALICE, permission="read")
    sharing_service.accept_share(share["share_id"], BOB)
    assert sharing_service.check_collection_access(cid, BOB) == "read"

    cid2 = _make_private(ALICE)
    share2 = sharing_service.create_share(cid2, ALICE, permission="readwrite")
    sharing_service.accept_share(share2["share_id"], BOB)
    assert sharing_service.check_collection_access(cid2, BOB) == "readwrite"


def test_only_owner_can_share(private_mode):
    cid = _make_private(ALICE)
    with pytest.raises(PermissionError):
        sharing_service.create_share(cid, BOB)
    # Team collections have no person as owner — nobody can share them
    with pytest.raises(PermissionError):
        sharing_service.create_share("default", ALICE)


def test_anonymous_cannot_accept_shares(private_mode):
    cid = _make_private(ALICE)
    share = sharing_service.create_share(cid, ALICE)
    with pytest.raises(ValueError, match="identity"):
        sharing_service.accept_share(share["share_id"], None)


def test_get_all_collections_scoping(private_mode):
    mine = _make_private(ALICE)
    theirs = _make_private(BOB)

    alice_ids = {c["id"] for c in collection_service.get_all_collections(user_id=ALICE)}
    assert mine in alice_ids
    assert theirs not in alice_ids
    assert "default" in alice_ids  # team tier stays visible

    anon_ids = {c["id"] for c in collection_service.get_all_collections(user_id=None)}
    assert anon_ids == {"default"}

    share = sharing_service.create_share(theirs, BOB, permission="read")
    sharing_service.accept_share(share["share_id"], ALICE)
    alice_cols = collection_service.get_all_collections(user_id=ALICE)
    shared = next(c for c in alice_cols if c["id"] == theirs)
    assert shared["permission"] == "read"


def test_require_collection_access_status_codes(private_mode):
    from api.deps import require_collection_access

    cid = _make_private(ALICE)
    # Invisible == missing: 404, never 403, so ids can't be probed
    with pytest.raises(HTTPException) as exc:
        require_collection_access(cid, BOB)
    assert exc.value.status_code == 404

    share = sharing_service.create_share(cid, ALICE, permission="read")
    sharing_service.accept_share(share["share_id"], BOB)
    assert require_collection_access(cid, BOB) == "read"
    with pytest.raises(HTTPException) as exc:
        require_collection_access(cid, BOB, write=True)
    assert exc.value.status_code == 403


def test_mcp_resolver_masks_invisible_collections(private_mode):
    from services.mcp_server import _resolve_collection_id

    cid = _make_private(ALICE)
    token = set_request_user(BOB)
    try:
        with pytest.raises(ValueError, match="not found"):
            _resolve_collection_id(cid)
    finally:
        reset_request_user(token)

    token = set_request_user(ALICE)
    try:
        assert _resolve_collection_id(cid) == cid
    finally:
        reset_request_user(token)


# ── Startup posture ─────────────────────────────────────────────────────────


@pytest.fixture()
def reloaded_main(monkeypatch):
    """Reload config + main under caller-supplied env, then restore both."""
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
    # Restore object identity: modules imported before the reload hold the
    # original Settings instance, so leaving a fresh one in config.settings
    # would split the app between two configuration objects.
    config.settings = _import_time_settings
    importlib.reload(main)


def test_private_collections_refuse_without_identity_source(reloaded_main):
    """Ownership enforced against a forgeable identity is the half-boundary
    this app refuses to ship — no Access config, no private mode."""
    with pytest.raises(RuntimeError, match="identity"):
        # CF_ACCESS_* blanked explicitly: a developer .env may configure them
        reloaded_main(
            PRIVATE_COLLECTIONS="true",
            CF_ACCESS_TEAM_DOMAIN="",
            CF_ACCESS_AUD="",
        )


def test_private_collections_start_with_access_configured(reloaded_main):
    main = reloaded_main(
        PRIVATE_COLLECTIONS="true",
        CF_ACCESS_TEAM_DOMAIN="testteam.cloudflareaccess.com",
        CF_ACCESS_AUD="aud-test",
    )
    assert main.app is not None


# ── HTTP enforcement with two identities ────────────────────────────────────


class _FakeVerifier:
    """Stands in for AccessJWTVerifier: the 'assertion' is just the email.

    JWT crypto (signature/issuer/audience/expiry) is covered by
    test_access_jwt.py; here we only need the middleware to map an assertion
    to an identity.
    """

    def verify(self, token):
        return {"email": token} if token and "@" in token else None

    @staticmethod
    def identity_from_claims(claims):
        return claims.get("email")


@pytest.fixture()
def private_client(fresh_db, monkeypatch):
    """A TestClient for the app reloaded in private-collections mode.

    Settings are flipped on the shared singleton (visible to every module),
    the Access verifier is faked, and main is reloaded so the module-level
    auth-middleware registration runs for this mode. Teardown restores the
    settings first and only then reloads main, so the restored app matches
    the restored configuration.
    """
    import services.access_jwt as access_jwt
    import main

    saved = _flip_private_mode()
    monkeypatch.setattr(access_jwt, "get_verifier", lambda: _FakeVerifier())

    reloaded = importlib.reload(main)
    # Bare client (no context manager): lifespan skipped, middleware still runs.
    yield TestClient(reloaded.app)

    _restore(saved)
    importlib.reload(main)


def _as(email):
    return {"cf-access-jwt-assertion": email}


def test_requests_without_identity_are_rejected(private_client):
    assert private_client.get("/api/collections").status_code == 401
    assert private_client.get("/health").status_code == 200  # probes stay open


def test_identity_reported(private_client):
    r = private_client.get("/api/user/me", headers=_as(ALICE))
    assert r.status_code == 200
    assert r.json()["user_id"] == ALICE
    assert r.json()["private_collections"] is True


def test_create_with_team_visibility(private_client):
    """The creator can opt a new collection into the team tier."""
    r = private_client.post(
        "/api/collections",
        json={"name": "Shared docs", "visibility": "team"},
        headers=_as(ALICE),
    )
    assert r.status_code == 201
    assert r.json()["owner_id"] == "default"
    cid = r.json()["id"]
    # Team tier: visible to everyone, including a user who owns nothing
    bob_ids = {
        c["id"]
        for c in private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]
    }
    assert cid in bob_ids


def test_collections_are_isolated_between_users(private_client):
    r = private_client.post(
        "/api/collections", json={"name": "Alice research"}, headers=_as(ALICE)
    )
    assert r.status_code == 201
    cid = r.json()["id"]
    assert r.json()["owner_id"] == ALICE

    bob_ids = {
        c["id"]
        for c in private_client.get("/api/collections", headers=_as(BOB)).json()["collections"]
    }
    assert cid not in bob_ids
    assert "default" in bob_ids  # team collection stays shared

    # Direct id access answers like a missing collection
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    assert private_client.get(f"/api/collections/{cid}", headers=_as(ALICE)).status_code == 200


def test_share_flow_end_to_end(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Shared notes"}, headers=_as(ALICE)
    ).json()["id"]

    share = private_client.post(
        f"/api/collections/{cid}/share", json={"permission": "read"}, headers=_as(ALICE)
    )
    assert share.status_code == 201
    token = share.json()["share_id"]

    # Bob can't see it until he accepts the share
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 404
    accepted = private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB))
    assert accepted.status_code == 200

    got = private_client.get(f"/api/collections/{cid}", headers=_as(BOB))
    assert got.status_code == 200
    assert got.json()["permission"] == "read"

    # read-only share: writes are rejected
    upd = private_client.put(
        f"/api/collections/{cid}", json={"name": "hijacked"}, headers=_as(BOB)
    )
    assert upd.status_code == 403

    # deletion stays owner-only even for share holders
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403


def test_contextvar_identity_reaches_nested_enforcement(private_client):
    """Endpoints with no user dependency read the contextvar the middleware
    binds (the same path documents and /mcp enforcement uses)."""
    cid = private_client.post(
        "/api/collections", json={"name": "ctx check"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.get(f"/api/collections/{cid}/expertise", headers=_as(BOB))
    assert r.status_code == 404
    r = private_client.get(f"/api/collections/{cid}/expertise", headers=_as(ALICE))
    assert r.status_code == 200


def test_share_with_email_invitation(private_client, monkeypatch):
    """notify_email sends the token via the share-email service, attributed
    to the sharer's verified identity."""
    import services.share_email as se

    sent = {}

    def fake_send(to, **kwargs):
        sent["to"] = to
        sent.update(kwargs)

    monkeypatch.setattr(se, "send_share_email", fake_send)

    cid = private_client.post(
        "/api/collections", json={"name": "Mailed notes"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/share",
        json={"permission": "read", "notify_email": "bob@example.com"},
        headers=_as(ALICE),
    )
    assert r.status_code == 201
    assert r.json()["email_sent"] is True
    assert sent["to"] == "bob@example.com"
    assert sent["shared_by"] == ALICE
    assert sent["share_token"] == r.json()["share_id"]
    assert sent["collection_name"] == "Mailed notes"


def test_share_email_unconfigured_reports_error_but_creates_share(private_client, monkeypatch):
    # Pin the key empty: a real RESEND_API_KEY in the developer's .env would
    # otherwise send this test down the live-API path (and actually call out).
    monkeypatch.setattr(config.settings, "resend_api_key", "")
    cid = private_client.post(
        "/api/collections", json={"name": "No mail"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "bob@example.com"},
        headers=_as(ALICE),
    )
    assert r.status_code == 201
    assert r.json()["email_sent"] is False
    assert "RESEND_API_KEY" in r.json()["email_error"]
    # The share itself still exists and is acceptable
    token = r.json()["share_id"]
    assert private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB)).status_code == 200


def test_readwrite_share_contributes_but_cannot_reconfigure(private_client):
    """A read-write share is a contributor, not a co-owner.

    Bob may add and remove sources — that is what the share is for — but the
    collection's settings and its index stay Alice's. Re-indexing on someone
    else's behalf rewrites their index under them, so it is owner-only.
    """
    cid = private_client.post(
        "/api/collections", json={"name": "Team drafts"}, headers=_as(ALICE)
    ).json()["id"]
    token = private_client.post(
        f"/api/collections/{cid}/share", json={"permission": "readwrite"}, headers=_as(ALICE)
    ).json()["share_id"]
    private_client.post(f"/api/shares/{token}/accept", headers=_as(BOB))

    # Configuration, re-indexing and deletion are the owner's alone.
    assert (
        private_client.put(
            f"/api/collections/{cid}", json={"description": "bob was here"}, headers=_as(BOB)
        ).status_code
        == 403
    )
    assert private_client.post(f"/api/collections/{cid}/reindex", headers=_as(BOB)).status_code == 403
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403

    # Alice still has all three.
    assert (
        private_client.put(
            f"/api/collections/{cid}", json={"description": "alice was here"}, headers=_as(ALICE)
        ).status_code
        == 200
    )


# ── Edge admission (Cloudflare Access) ───────────────────────────────────

def _stub_edge(monkeypatch, admin):
    """Wire the edge-admission service to an in-memory allowlist."""
    import services.access_provisioning as ap
    import services.share_email as se

    monkeypatch.setattr(se, "send_share_email", lambda to, **kw: None)
    monkeypatch.setattr(ap, "access_provisioning_enabled", lambda: True)
    monkeypatch.setattr(ap, "is_admin", lambda uid: uid == admin)

    admitted = set()

    def fake_admit(email):
        email = email.lower()
        new = email not in admitted
        admitted.add(email)
        return new

    def fake_revoke(email):
        email = email.lower()
        was = email in admitted
        admitted.discard(email)
        return was

    monkeypatch.setattr(ap, "admit_email", fake_admit)
    monkeypatch.setattr(ap, "revoke_email", fake_revoke)
    return admitted


def test_admin_invite_admits_at_the_edge(private_client, monkeypatch):
    admitted = _stub_edge(monkeypatch, admin=ALICE)

    cid = private_client.post(
        "/api/collections", json={"name": "Admin notes"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "guest@outside.org"}, headers=_as(ALICE),
    )
    assert r.status_code == 201
    assert r.json()["edge_admitted"] is True
    assert "guest@outside.org" in admitted


def test_non_admin_invite_does_not_admit(private_client, monkeypatch):
    """A non-admin owner may still share; they just cannot open the front
    door for a stranger."""
    admitted = _stub_edge(monkeypatch, admin="someone-else@example.com")

    cid = private_client.post(
        "/api/collections", json={"name": "Bob notes"}, headers=_as(BOB)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "guest@outside.org"}, headers=_as(BOB),
    )
    assert r.status_code == 201
    assert r.json()["edge_admitted"] is False
    assert "edge_note" in r.json()
    assert admitted == set()


def test_revoking_last_share_withdraws_admission(private_client, monkeypatch):
    admitted = _stub_edge(monkeypatch, admin=ALICE)

    cid = private_client.post(
        "/api/collections", json={"name": "Temp"}, headers=_as(ALICE)
    ).json()["id"]
    share_id = private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "guest@outside.org"}, headers=_as(ALICE),
    ).json()["share_id"]
    assert "guest@outside.org" in admitted

    r = private_client.delete(f"/api/shares/{share_id}", headers=_as(ALICE))
    assert r.status_code == 200
    assert r.json()["edge_revoked"] is True
    assert admitted == set()


def test_revoking_one_of_two_shares_keeps_admission(private_client, monkeypatch):
    """Otherwise revoking any one share would lock the guest out of the
    others they still legitimately hold."""
    admitted = _stub_edge(monkeypatch, admin=ALICE)

    ids = []
    for name in ("First", "Second"):
        cid = private_client.post(
            "/api/collections", json={"name": name}, headers=_as(ALICE)
        ).json()["id"]
        ids.append(private_client.post(
            f"/api/collections/{cid}/share",
            json={"notify_email": "guest@outside.org"}, headers=_as(ALICE),
        ).json()["share_id"])

    r = private_client.delete(f"/api/shares/{ids[0]}", headers=_as(ALICE))
    assert r.status_code == 200
    assert "edge_revoked" not in r.json()
    assert "guest@outside.org" in admitted

    r = private_client.delete(f"/api/shares/{ids[1]}", headers=_as(ALICE))
    assert r.json()["edge_revoked"] is True
    assert admitted == set()


# ── Admin admission management ───────────────────────────────────────────

def test_admissions_require_admin(private_client, monkeypatch):
    _stub_edge(monkeypatch, admin=ALICE)
    import services.access_provisioning as ap
    monkeypatch.setattr(ap, "admitted_emails", lambda: [ALICE])

    assert private_client.get("/api/access/admissions", headers=_as(BOB)).status_code == 403
    assert private_client.post(
        "/api/access/admissions", json={"email": "x@y.z"}, headers=_as(BOB)
    ).status_code == 403
    assert private_client.get("/api/access/admissions", headers=_as(ALICE)).status_code == 200


def test_admissions_503_when_unconfigured(private_client, monkeypatch):
    import services.access_provisioning as ap
    monkeypatch.setattr(ap, "access_provisioning_enabled", lambda: False)
    r = private_client.get("/api/access/admissions", headers=_as(ALICE))
    assert r.status_code == 503
    assert "CF_API_TOKEN" in r.json()["detail"]


def test_admissions_reconciles_both_kinds_of_drift(private_client, monkeypatch):
    """Admitted-with-nothing-shared and shared-with-but-not-admitted are the
    two ways the edge list stops matching reality."""
    _stub_edge(monkeypatch, admin=ALICE)
    import services.access_provisioning as ap
    monkeypatch.setattr(config.settings, "admin_emails", ALICE)

    cid = private_client.post(
        "/api/collections", json={"name": "Drifty"}, headers=_as(ALICE)
    ).json()["id"]
    private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "shared-and-admitted@x.com"}, headers=_as(ALICE),
    )
    private_client.post(
        f"/api/collections/{cid}/share",
        json={"notify_email": "missing@x.com"}, headers=_as(ALICE),
    )

    # orphan: admitted, nothing shared. missing: shared with, never admitted.
    monkeypatch.setattr(ap, "admitted_emails",
                        lambda: [ALICE, "shared-and-admitted@x.com", "orphan@x.com"])

    body = private_client.get("/api/access/admissions", headers=_as(ALICE)).json()
    assert body["orphans"] == ["orphan@x.com"]
    assert "missing@x.com" in body["missing"]
    assert body["counts"]["admitted"] == 3
    assert body["counts"]["free_seat_limit"] == 50

    by_email = {p["email"]: p for p in body["people"]}
    assert by_email["shared-and-admitted@x.com"]["shares"] == 1
    assert by_email["orphan@x.com"]["shares"] == 0
    # The admin is on the list but is never reported as dead weight.
    assert by_email[ALICE]["is_admin"] is True
    assert ALICE not in body["orphans"]


def test_admin_cannot_withdraw_own_access(private_client, monkeypatch):
    """Locking yourself out from inside the app leaves nobody able to fix it."""
    _stub_edge(monkeypatch, admin=ALICE)
    import services.access_provisioning as ap
    monkeypatch.setattr(ap, "admitted_emails", lambda: [ALICE])

    r = private_client.delete(f"/api/access/admissions/{ALICE}", headers=_as(ALICE))
    assert r.status_code == 400
    assert "your own access" in r.json()["detail"]


def test_admit_and_withdraw_roundtrip(private_client, monkeypatch):
    admitted = _stub_edge(monkeypatch, admin=ALICE)
    import services.access_provisioning as ap
    monkeypatch.setattr(ap, "admitted_emails", lambda: sorted(admitted))

    r = private_client.post(
        "/api/access/admissions", json={"email": "Newcomer@X.com"}, headers=_as(ALICE)
    )
    assert r.status_code == 201
    assert r.json()["added"] is True
    assert "newcomer@x.com" in admitted

    r = private_client.delete("/api/access/admissions/newcomer@x.com", headers=_as(ALICE))
    assert r.status_code == 200
    assert r.json()["removed"] is True
    assert admitted == set()


def test_admit_rejects_junk(private_client, monkeypatch):
    _stub_edge(monkeypatch, admin=ALICE)
    r = private_client.post(
        "/api/access/admissions", json={"email": "not-an-email"}, headers=_as(ALICE)
    )
    assert r.status_code == 400


# ── Operator-only settings ──────────────────────────────────────────────────
# POST /api/config changes the whole deployment — the embedding model decides
# whether every existing index still matches, and provider keys are shared by
# everyone. Private-collections mode admits people who are only meant to read
# one shared collection, so "is signed in" is not a wide enough gate.


def _set_admins(value):
    """Point ADMIN_EMAILS at `value` on every live Settings object."""
    saved = [(s, s.admin_emails) for s in _settings_objects()]
    for s in _settings_objects():
        s.admin_emails = value
    return saved


def _restore_admins(saved):
    for s, old in saved:
        s.admin_emails = old


def test_require_admin_noop_when_mode_off(fresh_db):
    """Shared appliance: everyone who reaches the app is a trusted teammate."""
    from api.deps import require_admin

    token = set_request_user(BOB)
    try:
        assert require_admin() is None
    finally:
        reset_request_user(token)


def test_require_admin_fails_closed_with_no_admins(private_mode):
    """Empty ADMIN_EMAILS means nobody — never everybody."""
    from api.deps import require_admin

    saved = _set_admins("")
    token = set_request_user(ALICE)
    try:
        with pytest.raises(HTTPException) as exc:
            require_admin()
        assert exc.value.status_code == 403
        assert "No admin is configured" in exc.value.detail
    finally:
        reset_request_user(token)
        _restore_admins(saved)


def test_require_admin_rejects_non_admin(private_mode):
    from api.deps import require_admin

    saved = _set_admins(ALICE)
    token = set_request_user(BOB)
    try:
        with pytest.raises(HTTPException) as exc:
            require_admin()
        assert exc.value.status_code == 403
    finally:
        reset_request_user(token)
        _restore_admins(saved)


def test_require_admin_accepts_admin(private_mode):
    """Match is case-insensitive and tolerates spacing in the list."""
    from api.deps import require_admin

    saved = _set_admins(f" {ALICE.upper()} , someone@else.test ")
    token = set_request_user(ALICE)
    # With ADMIN_EMAILS set the gate reads the verified identity, not the
    # collection-mode user id, so a signed-in admin carries both.
    identity_token = set_request_identity(ALICE)
    try:
        assert require_admin() == ALICE
    finally:
        reset_request_identity(identity_token)
        reset_request_user(token)
        _restore_admins(saved)


def test_config_write_is_admin_only_over_http(private_client, monkeypatch):
    """The gate runs before config_manager, which is stubbed so the test
    never writes to the real .env."""
    from services import config_manager as cm_module

    calls = []
    monkeypatch.setattr(
        cm_module.config_manager,
        "update_config",
        lambda updates: calls.append(updates) or {"success": True},
    )

    saved = _set_admins(ALICE)
    try:
        body = {"chunk_size": 999}
        assert private_client.post("/api/config", json=body, headers=_as(BOB)).status_code == 403
        assert calls == []  # rejected before reaching the config writer

        assert private_client.post("/api/config", json=body, headers=_as(ALICE)).status_code == 200
        assert calls == [body]
    finally:
        _restore_admins(saved)


def test_config_read_stays_open(private_client):
    """Reading is left open so the settings UI still renders for everyone;
    the two credential fields are masked by config_manager."""
    r = private_client.get("/api/config", headers=_as(BOB))
    assert r.status_code == 200
    assert r.json()["ollama_cloud_api_key"] in ("", "********")


# ── Bulk invitations ────────────────────────────────────────────────────────


def test_bulk_invite_per_address_results(private_client, monkeypatch):
    """One share per address, everyone emailed, results reported per address."""
    import services.share_email as se

    sent = []
    monkeypatch.setattr(se, "send_share_email", lambda to, **kw: sent.append(to))

    cid = private_client.post(
        "/api/collections", json={"name": "Bulk notes"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/shares/bulk",
        json={"emails": ["bob@example.com", " CAROL@example.com ", "bob@example.com", "junk"]},
        headers=_as(ALICE),
    )
    assert r.status_code == 201
    results = r.json()["results"]
    # Deduplicated + normalized + junk dropped → two invitations
    assert [x["email"] for x in results] == ["bob@example.com", "carol@example.com"]
    assert all(x["email_sent"] for x in results)
    assert sorted(sent) == ["bob@example.com", "carol@example.com"]

    # Each address got its own share, revocable independently
    shares = private_client.get(
        f"/api/collections/{cid}/shares", headers=_as(ALICE)
    ).json()["shares"]
    assert sorted(s["invited_email"] for s in shares) == ["bob@example.com", "carol@example.com"]

    # And each recipient can accept their own token
    bob_share = next(x for x in results if x["email"] == "bob@example.com")
    accept = private_client.post(f"/api/shares/{bob_share['share_id']}/accept", headers=_as(BOB))
    assert accept.status_code == 200


def test_bulk_invite_owner_only(private_client, monkeypatch):
    import services.share_email as se
    monkeypatch.setattr(se, "send_share_email", lambda to, **kw: None)

    cid = private_client.post(
        "/api/collections", json={"name": "Not yours"}, headers=_as(ALICE)
    ).json()["id"]
    r = private_client.post(
        f"/api/collections/{cid}/shares/bulk",
        json={"emails": ["x@example.com"]},
        headers=_as(BOB),
    )
    assert r.status_code == 403


def test_bulk_invite_rejects_empty_and_oversized(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Limits"}, headers=_as(ALICE)
    ).json()["id"]
    assert private_client.post(
        f"/api/collections/{cid}/shares/bulk", json={"emails": []}, headers=_as(ALICE)
    ).status_code == 400
    too_many = [f"u{i}@example.com" for i in range(101)]
    assert private_client.post(
        f"/api/collections/{cid}/shares/bulk", json={"emails": too_many}, headers=_as(ALICE)
    ).status_code == 400


# ── Published collections ───────────────────────────────────────────────────
#
# Publishing is how a reviewed collection reaches the whole deployment while
# staying one person's to maintain: everyone else resolves to 'read', in the
# app and over MCP, and the way to work with it on your own terms is a clone.


def test_published_collection_is_readable_by_everyone(private_mode):
    cid = _make_private(ALICE)
    assert sharing_service.check_collection_access(cid, BOB) is None

    app_db.update_collection(cid, published=True)

    assert sharing_service.check_collection_access(cid, ALICE) == "owner"
    assert sharing_service.check_collection_access(cid, BOB) == "read"
    # Released means released: a password-auth caller with no identity too.
    assert sharing_service.check_collection_access(cid, None) == "read"


def test_published_collection_appears_in_listings_as_read(private_mode):
    cid = _make_private(ALICE)
    app_db.update_collection(cid, published=True)

    bob_cols = collection_service.get_all_collections(user_id=BOB)
    entry = next(c for c in bob_cols if c["id"] == cid)
    assert entry["permission"] == "read"
    assert entry["published"] is True

    # And the owner still sees it as theirs.
    alice_entry = next(
        c for c in collection_service.get_all_collections(user_id=ALICE) if c["id"] == cid
    )
    assert alice_entry["permission"] == "owner"


def test_share_beats_publication(private_mode):
    """An explicit read-write share must not be downgraded by publishing."""
    cid = _make_private(ALICE)
    share = sharing_service.create_share(cid, ALICE, permission="readwrite")
    sharing_service.accept_share(share["share_id"], BOB)
    app_db.update_collection(cid, published=True)

    assert sharing_service.check_collection_access(cid, BOB) == "readwrite"
    entry = next(
        c for c in collection_service.get_all_collections(user_id=BOB) if c["id"] == cid
    )
    assert entry["permission"] == "readwrite"


def test_published_collection_is_visible_over_mcp(private_mode):
    from services.mcp_server import _resolve_collection_id

    cid = _make_private(ALICE)
    app_db.update_collection(cid, published=True)

    token = set_request_user(BOB)
    try:
        assert _resolve_collection_id(cid) == cid
    finally:
        reset_request_user(token)


def test_reader_cannot_reconfigure_published_collection(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Handbook"}, headers=_as(ALICE)
    ).json()["id"]
    assert (
        private_client.put(
            f"/api/collections/{cid}", json={"published": True}, headers=_as(ALICE)
        ).status_code
        == 200
    )

    # Bob can read it...
    assert private_client.get(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 200
    # ...and nothing more.
    assert (
        private_client.put(
            f"/api/collections/{cid}", json={"chunk_size": 1200}, headers=_as(BOB)
        ).status_code
        == 403
    )
    assert private_client.post(f"/api/collections/{cid}/reindex", headers=_as(BOB)).status_code == 403
    assert private_client.delete(f"/api/collections/{cid}", headers=_as(BOB)).status_code == 403


def test_team_collections_cannot_be_published(private_client):
    """A team collection has no single owner, so publishing would lock it
    with nobody able to unlock it. Refused rather than allowed and regretted."""
    resp = private_client.put(
        "/api/collections/default", json={"published": True}, headers=_as(ALICE)
    )
    assert resp.status_code == 400
    assert "team collection" in resp.json()["detail"].lower()


def test_restricted_collections_cannot_be_published(private_client):
    cid = private_client.post(
        "/api/collections", json={"name": "Secrets"}, headers=_as(ALICE)
    ).json()["id"]
    private_client.put(
        f"/api/collections/{cid}", json={"sensitivity": "restricted"}, headers=_as(ALICE)
    )
    resp = private_client.put(
        f"/api/collections/{cid}", json={"published": True}, headers=_as(ALICE)
    )
    assert resp.status_code == 403
    assert "restricted" in resp.json()["detail"].lower()


def test_global_reindex_is_admin_only(private_client, monkeypatch):
    """POST /api/reindex rebuilds every collection's index, including ones
    the caller does not own — it was reachable by anyone."""
    import services.access_provisioning as ap

    monkeypatch.setattr(ap, "is_admin", lambda uid: uid == ALICE)
    assert private_client.post("/api/reindex", headers=_as(BOB)).status_code == 403
