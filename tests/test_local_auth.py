"""Built-in accounts (IDENTITY_PROVIDER=local): setup, sign-in, sessions, invites.

These drive the real app through its auth middleware, with the account store
pointed at a temp directory. Passwords hash with a cheap scrypt cost here so
the suite stays fast; verification reads the cost out of the stored hash, so
nothing about the production parameters is bypassed.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from services import local_auth

PASSWORD = "correct horse battery"
OTHER = "another sturdy phrase"


@pytest.fixture
def accounts(tmp_path, monkeypatch):
    from config import settings
    import main

    monkeypatch.setattr(settings, "private_collections", True)
    monkeypatch.setattr(settings, "identity_provider", "")
    monkeypatch.setattr(settings, "auth_password", "")
    monkeypatch.setattr(settings, "setup_code", "")
    monkeypatch.setattr(settings, "admin_emails", "")
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(local_auth, "_SCRYPT_LOG_N", 10)
    monkeypatch.setattr(local_auth, "_store", None)
    monkeypatch.setattr(local_auth, "_store_path", None)
    yield TestClient(main.app)


def store():
    return local_auth.get_store()


def new_client():
    import main

    return TestClient(main.app)


def setup_admin(client, email="owner@example.com", password=PASSWORD, name="Owner"):
    code = store().setup_code()
    r = client.post("/api/auth/setup", json={
        "setup_code": code, "email": email, "password": password, "display_name": name,
    })
    assert r.status_code == 200, r.text
    return r


def sign_in(email, password=PASSWORD):
    c = new_client()
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return c


# ── hashing ─────────────────────────────────────────────────────────────────


def test_password_hash_roundtrip_and_salting(monkeypatch):
    monkeypatch.setattr(local_auth, "_SCRYPT_LOG_N", 10)
    a, b = local_auth.hash_password("hunter2hunter2"), local_auth.hash_password("hunter2hunter2")
    assert a != b  # per-account salt
    assert local_auth.verify_password("hunter2hunter2", a)
    assert not local_auth.verify_password("hunter2hunter3", a)
    assert not local_auth.verify_password("x", "not-a-hash")
    assert not local_auth.verify_password("x", "bcrypt$1$2$3$4$5")


@pytest.mark.parametrize("bad", ["short", "a" * 300, "aaaaaaaaaaaa"])
def test_password_policy_rejects_weak_choices(bad):
    with pytest.raises(local_auth.AuthError):
        local_auth.check_password_policy(bad, "me@example.com")


def test_password_cannot_be_the_email():
    with pytest.raises(local_auth.AuthError):
        local_auth.check_password_policy("me@example.com", "me@example.com")


# ── first-run setup ─────────────────────────────────────────────────────────


def test_status_before_setup_and_protected_routes_refuse(accounts):
    r = accounts.get("/api/auth/status")
    assert r.json() == {"provider": "local", "setup_required": True, "authenticated": False, "user": None}

    r = accounts.get("/api/user/me")
    assert r.status_code == 401
    assert r.json()["login_required"] is True
    # The app draws its own sign-in page; a Basic challenge would pop the browser dialog.
    assert "www-authenticate" not in {k.lower() for k in r.headers}


def test_the_spa_shell_and_health_are_public(accounts):
    assert accounts.get("/health").status_code == 200
    assert accounts.get("/").status_code == 200


def test_setup_needs_the_code_from_the_server_log(accounts):
    r = accounts.post("/api/auth/setup", json={
        "setup_code": "wrong-code", "email": "owner@example.com", "password": PASSWORD,
    })
    assert r.status_code == 403
    assert store().count_accounts() == 0


def test_setup_creates_an_admin_and_signs_them_in(accounts):
    r = setup_admin(accounts)
    assert r.json()["user"]["is_admin"] is True
    status = accounts.get("/api/auth/status").json()
    assert status["authenticated"] is True and status["setup_required"] is False
    assert status["user"]["email"] == "owner@example.com"
    assert accounts.get("/api/user/me").status_code == 200


def test_every_way_in_records_the_last_sign_in(accounts):
    setup_admin(accounts)
    assert store().get_account("owner@example.com").last_login_at is not None
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    assert store().get_account("m@example.com") is None
    new_client().post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert store().get_account("m@example.com").last_login_at is not None


def test_setup_is_closed_once_an_account_exists(accounts):
    setup_admin(accounts)
    r = new_client().post("/api/auth/setup", json={
        "setup_code": "anything", "email": "attacker@example.com", "password": PASSWORD,
    })
    assert r.status_code == 409
    assert store().count_accounts() == 1


def test_setup_code_attempts_are_throttled(accounts):
    for _ in range(8):
        accounts.post("/api/auth/setup", json={"setup_code": "nope", "email": "a@example.com", "password": PASSWORD})
    r = accounts.post("/api/auth/setup", json={
        "setup_code": store().setup_code(), "email": "a@example.com", "password": PASSWORD,
    })
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}


def test_configured_setup_code_wins(accounts, monkeypatch):
    from config import settings

    monkeypatch.setattr(settings, "setup_code", "my-own-code")
    assert store().setup_code() == "my-own-code"
    r = accounts.post("/api/auth/setup", json={
        "setup_code": "My-Own-Code", "email": "a@example.com", "password": PASSWORD,
    })
    assert r.status_code == 200


# ── sign-in, sessions, logout ───────────────────────────────────────────────


def test_login_sets_a_hardened_session_cookie(accounts):
    setup_admin(accounts)
    r = new_client().post("/api/auth/login", json={"email": "OWNER@example.com", "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "clio_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie


def test_unknown_address_and_wrong_password_look_identical(accounts):
    setup_admin(accounts)
    a = new_client().post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong wrong wrong"})
    b = new_client().post("/api/auth/login", json={"email": "ghost@example.com", "password": "wrong wrong wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_repeated_failures_lock_the_pair_out(accounts):
    setup_admin(accounts)
    c = new_client()
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope nope nope"}).status_code == 401
    locked = c.post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD})
    assert locked.status_code == 429
    assert int(locked.headers["retry-after"]) > 0


def test_logout_ends_the_session(accounts):
    setup_admin(accounts)
    cookie = accounts.cookies.get("clio_session")
    assert accounts.post("/api/auth/logout").status_code == 200
    stale = new_client()
    stale.cookies.set("clio_session", cookie)
    assert stale.get("/api/user/me").status_code == 401


def test_a_forged_or_garbage_cookie_gets_nothing(accounts):
    setup_admin(accounts)
    c = new_client()
    c.cookies.set("clio_session", "A" * 43)
    assert c.get("/api/user/me").status_code == 401


def test_sessions_expire(accounts):
    setup_admin(accounts)
    token = accounts.cookies.get("clio_session")
    with sqlite3.connect(store().path) as conn:
        conn.execute("UPDATE sessions SET expires_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    assert accounts.get("/api/user/me").status_code == 401
    assert store().lookup_session(token) is None


def test_sessions_have_an_absolute_lifetime(accounts):
    setup_admin(accounts)
    old = (datetime.now(timezone.utc) - timedelta(days=local_auth.SESSION_ABSOLUTE_DAYS + 1)).isoformat()
    with sqlite3.connect(store().path) as conn:
        conn.execute("UPDATE sessions SET created_at = ?", (old,))
    assert accounts.get("/api/user/me").status_code == 401


def test_only_the_hash_of_a_session_token_is_stored(accounts):
    setup_admin(accounts)
    token = accounts.cookies.get("clio_session")
    with sqlite3.connect(store().path) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]
    assert token not in stored and len(stored) == 1


# ── CSRF ────────────────────────────────────────────────────────────────────


def test_cross_site_writes_with_the_cookie_are_refused(accounts):
    setup_admin(accounts)
    r = accounts.post("/api/auth/users/invite", json={"email": "x@example.com"},
                      headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = accounts.post("/api/auth/users/invite", json={"email": "x@example.com"},
                      headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


def test_same_origin_writes_pass(accounts):
    setup_admin(accounts)
    r = accounts.post("/api/auth/users/invite", json={"email": "x@example.com"},
                      headers={"Origin": "http://testserver"})
    assert r.status_code == 201


def test_csrf_rule_unit():
    ok = local_auth.csrf_ok
    assert ok("GET", {"origin": "https://evil.example"})
    assert ok("POST", {})  # not a browser acting for an attacker
    assert ok("POST", {"origin": "http://a.test", "host": "a.test"})
    assert not ok("POST", {"origin": "http://b.test", "host": "a.test"})
    assert not ok("POST", {"origin": "null", "host": "a.test"})


# ── invites, resets, users ──────────────────────────────────────────────────


def _token(url):
    assert "/#token=" in url and "?token=" not in url  # fragment: kept out of logs
    return url.split("token=")[1]


def test_invite_flow_creates_a_member_once(accounts):
    setup_admin(accounts)
    r = accounts.post("/api/auth/users/invite", json={"email": "Guest@Example.com"})
    assert r.status_code == 201
    token = _token(r.json()["url"])

    guest = new_client()
    info = guest.get("/api/auth/link", params={"token": token}).json()
    assert info == {"purpose": "invite", "email": "guest@example.com"}

    ok = guest.post("/api/auth/accept", json={"token": token, "password": OTHER, "display_name": "Guest"})
    assert ok.status_code == 200 and ok.json()["user"]["is_admin"] is False
    assert guest.get("/api/user/me").status_code == 200

    again = new_client().post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert again.status_code == 400  # single use
    assert new_client().get("/api/auth/link", params={"token": token}).status_code == 404


def test_expired_invites_do_not_work(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "g@example.com"}).json()["url"])
    with sqlite3.connect(store().path) as conn:
        conn.execute("UPDATE link_tokens SET expires_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),))
    r = new_client().post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert r.status_code == 400


def test_a_new_invite_retires_the_old_one(accounts):
    setup_admin(accounts)
    first = _token(accounts.post("/api/auth/users/invite", json={"email": "g@example.com"}).json()["url"])
    second = _token(accounts.post("/api/auth/users/invite", json={"email": "g@example.com"}).json()["url"])
    assert new_client().get("/api/auth/link", params={"token": first}).status_code == 404
    assert new_client().get("/api/auth/link", params={"token": second}).status_code == 200


def test_only_hashes_of_link_tokens_are_stored(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "g@example.com"}).json()["url"])
    with sqlite3.connect(store().path) as conn:
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM link_tokens")]
    assert token not in stored


def test_inviting_an_existing_address_is_refused(accounts):
    setup_admin(accounts)
    r = accounts.post("/api/auth/users/invite", json={"email": "owner@example.com"})
    assert r.status_code == 409


def test_members_cannot_use_the_users_api(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    member = new_client()
    member.post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert member.get("/api/auth/users").status_code == 403
    assert member.post("/api/auth/users/invite", json={"email": "z@example.com"}).status_code == 403
    assert member.patch("/api/auth/users/owner@example.com", json={"disabled": True}).status_code == 403
    assert member.delete("/api/auth/users/owner@example.com").status_code == 403


def test_admin_lists_users_without_secrets(accounts):
    setup_admin(accounts)
    body = accounts.get("/api/auth/users").json()
    assert [u["email"] for u in body["users"]] == ["owner@example.com"]
    assert "password_hash" not in body["users"][0]


def test_the_last_active_admin_cannot_be_removed(accounts):
    setup_admin(accounts)
    assert accounts.patch("/api/auth/users/owner@example.com", json={"is_admin": False}).status_code == 409
    assert accounts.patch("/api/auth/users/owner@example.com", json={"disabled": True}).status_code == 409
    assert accounts.delete("/api/auth/users/owner@example.com").status_code == 409


def test_disabling_an_account_ends_its_sessions_and_blocks_sign_in(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    member = new_client()
    member.post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert member.get("/api/user/me").status_code == 200

    assert accounts.patch("/api/auth/users/m@example.com", json={"disabled": True}).status_code == 200
    assert member.get("/api/user/me").status_code == 401
    assert new_client().post("/api/auth/login", json={"email": "m@example.com", "password": OTHER}).status_code == 401

    assert accounts.patch("/api/auth/users/m@example.com", json={"disabled": False}).status_code == 200
    assert new_client().post("/api/auth/login", json={"email": "m@example.com", "password": OTHER}).status_code == 200


def test_promoting_a_member_makes_them_an_admin(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    member = new_client()
    member.post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert member.get("/api/auth/users").status_code == 403
    accounts.patch("/api/auth/users/m@example.com", json={"is_admin": True})
    assert member.get("/api/auth/users").status_code == 200


def test_deleting_an_account_removes_it_and_its_sessions(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    member = new_client()
    member.post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert accounts.delete("/api/auth/users/m@example.com").status_code == 200
    assert member.get("/api/user/me").status_code == 401
    assert store().get_account("m@example.com") is None


# ── passwords ───────────────────────────────────────────────────────────────


def test_change_password_needs_the_current_one_and_signs_out_other_devices(accounts):
    setup_admin(accounts)
    other_device = sign_in("owner@example.com")

    bad = accounts.post("/api/auth/password", json={"current_password": "not it not it", "new_password": OTHER})
    assert bad.status_code == 403

    ok = accounts.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": OTHER})
    assert ok.status_code == 200
    assert accounts.get("/api/user/me").status_code == 200  # this device stays
    assert other_device.get("/api/user/me").status_code == 401  # the other does not
    assert new_client().post("/api/auth/login", json={"email": "owner@example.com", "password": PASSWORD}).status_code == 401
    assert new_client().post("/api/auth/login", json={"email": "owner@example.com", "password": OTHER}).status_code == 200


def test_new_passwords_follow_the_policy(accounts):
    setup_admin(accounts)
    r = accounts.post("/api/auth/password", json={"current_password": PASSWORD, "new_password": "short"})
    assert r.status_code == 400


def test_reset_link_sets_a_new_password_and_ends_old_sessions(accounts):
    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    member = new_client()
    member.post("/api/auth/accept", json={"token": token, "password": OTHER})

    reset = accounts.post("/api/auth/users/m@example.com/reset")
    assert reset.status_code == 200
    reset_token = _token(reset.json()["url"])
    assert new_client().get("/api/auth/link", params={"token": reset_token}).json()["purpose"] == "reset"

    fresh = new_client()
    assert fresh.post("/api/auth/accept", json={"token": reset_token, "password": "brand new phrase"}).status_code == 200
    assert member.get("/api/user/me").status_code == 401  # the old session is gone
    assert new_client().post("/api/auth/login", json={"email": "m@example.com", "password": OTHER}).status_code == 401
    assert new_client().post("/api/auth/login", json={"email": "m@example.com", "password": "brand new phrase"}).status_code == 200


def test_reset_for_an_unknown_account_is_a_404(accounts):
    setup_admin(accounts)
    assert accounts.post("/api/auth/users/ghost@example.com/reset").status_code == 404


# ── admin sources ───────────────────────────────────────────────────────────


def test_admin_emails_is_a_break_glass_admin_source(accounts, monkeypatch):
    from config import settings
    from services.admins import is_admin

    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "m@example.com"}).json()["url"])
    new_client().post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert not is_admin("m@example.com")
    monkeypatch.setattr(settings, "admin_emails", "m@example.com")
    assert is_admin("m@example.com")


def test_disabled_admins_are_not_admins(accounts):
    from services.admins import is_admin

    setup_admin(accounts)
    token = _token(accounts.post("/api/auth/users/invite", json={"email": "a2@example.com", "is_admin": True}).json()["url"])
    new_client().post("/api/auth/accept", json={"token": token, "password": OTHER})
    assert is_admin("a2@example.com")
    accounts.patch("/api/auth/users/a2@example.com", json={"disabled": True})
    assert not is_admin("a2@example.com")


# ── provider selection ──────────────────────────────────────────────────────


def test_private_collections_default_to_local_accounts(monkeypatch):
    from config import settings
    from services import identity

    monkeypatch.setattr(settings, "identity_provider", "")
    monkeypatch.setattr(settings, "private_collections", True)
    assert identity.active_provider() == "local"
    monkeypatch.setattr(settings, "private_collections", False)
    assert identity.active_provider() == ""
    assert identity.requires_identity() is False


def test_explicit_local_works_without_private_collections(monkeypatch):
    from config import settings
    from services import identity

    monkeypatch.setattr(settings, "identity_provider", "local")
    monkeypatch.setattr(settings, "private_collections", False)
    assert identity.active_provider() == "local"
    assert identity.requires_identity() is True


def test_cloudflare_is_no_longer_a_provider(monkeypatch):
    from config import settings
    from services import identity

    monkeypatch.setattr(settings, "identity_provider", "cloudflare_access")
    with pytest.raises(RuntimeError, match="local"):
        identity.validate_config()


def test_auth_endpoints_are_off_for_other_providers(monkeypatch):
    from config import settings
    import main

    monkeypatch.setattr(settings, "identity_provider", "")
    monkeypatch.setattr(settings, "private_collections", False)
    client = TestClient(main.app)
    assert client.get("/api/auth/status").json()["provider"] == "none"
    assert client.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD}).status_code == 404
