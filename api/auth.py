"""Sign-in, first-run setup, and the Users panel's API (IDENTITY_PROVIDER=local).

The sign-in endpoints are public by necessity (``main._is_public_path``): a
person without a session has to reach them. Everything else here requires an
administrator, or the signed-in person acting on their own account.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from config import settings
from services import audit
from services.admins import is_admin
from services.identity import active_provider
from services.local_auth import (
    SESSION_ABSOLUTE_DAYS,
    SESSION_COOKIE,
    AuthError,
    get_store,
    session_token_from_headers,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])


# ── models ──────────────────────────────────────────────────────────────────


class SetupRequest(BaseModel):
    setup_code: str = Field(..., max_length=64)
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=512)
    display_name: str = Field("", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=512)


class AcceptRequest(BaseModel):
    token: str = Field(..., max_length=200)
    password: str = Field(..., max_length=512)
    display_name: str = Field("", max_length=120)


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=512)
    new_password: str = Field(..., max_length=512)


class InviteRequest(BaseModel):
    email: str = Field(..., max_length=320)
    is_admin: bool = False


class AccountUpdate(BaseModel):
    is_admin: Optional[bool] = None
    disabled: Optional[bool] = None


# ── helpers ─────────────────────────────────────────────────────────────────


def _require_local() -> None:
    if active_provider() != "local":
        raise HTTPException(status_code=404, detail="Built-in accounts are not enabled on this deployment.")


def _fail(exc: AuthError) -> HTTPException:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    return HTTPException(status_code=exc.status, detail=exc.message, headers=headers)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def _set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(
        # The browser keeps the cookie for the absolute lifetime; the server
        # enforces the shorter sliding idle window.
        SESSION_COOKIE, token, max_age=SESSION_ABSOLUTE_DAYS * 86400,
        httponly=True, samesite="lax", secure=_is_https(request), path="/",
    )


def _start_session(request: Request, response: Response, email: str) -> None:
    token = get_store().create_session(
        email, ip=_client_ip(request), user_agent=request.headers.get("user-agent", "")
    )
    _set_cookie(request, response, token)


def _session_account(request: Request):
    return get_store().lookup_session(session_token_from_headers(request.headers))


def _require_admin(request: Request):
    """The signed-in administrator. These routes are not public, so the
    middleware has already verified the session by the time we get here."""
    identity = getattr(request.state, "auth_identity", None)
    if not is_admin(identity):
        raise HTTPException(status_code=403, detail="Only an administrator can do that.")
    return identity


def _provision_user(email: str, display_name: str) -> None:
    """Record the person in the app's user table so shares and the audit
    trail can name them."""
    try:
        from services.app_database import app_db

        app_db.upsert_user(email, display_name or None)
    except Exception as e:  # noqa: BLE001 - never block a sign-in on bookkeeping
        logger.warning("Could not record user %s: %s", email, e)


# ── public: status, setup, login, logout, accept ────────────────────────────


@router.get("/api/auth/status", summary="Who am I, and does this install need setting up?")
def auth_status(request: Request):
    provider = active_provider()
    if provider != "local":
        return {"provider": provider or "none", "setup_required": False, "authenticated": True}
    store = get_store()
    account = _session_account(request)
    return {
        "provider": "local",
        "setup_required": store.count_accounts() == 0,
        "authenticated": account is not None,
        "user": (
            {"email": account.email, "display_name": account.display_name or account.email,
             "is_admin": account.is_admin}
            if account else None
        ),
    }


@router.post("/api/auth/setup", summary="Create the first administrator")
def auth_setup(body: SetupRequest, request: Request, response: Response):
    _require_local()
    store = get_store()
    ip = _client_ip(request)
    retry = store.code_throttle.retry_after(ip)
    if retry:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    if store.count_accounts() > 0:
        raise HTTPException(status_code=409, detail="Setup is already complete. Sign in instead.")
    if not store.check_setup_code(body.setup_code):
        store.code_throttle.fail(ip)
        raise HTTPException(
            status_code=403,
            detail="That setup code is not right. It is printed in the server log "
                   "(for Docker: docker logs clio).",
        )
    try:
        account = store.create_first_admin(body.email, body.password, body.display_name)
    except AuthError as exc:
        raise _fail(exc)
    store.code_throttle.clear(ip)
    _provision_user(account.email, account.display_name)
    audit.record("auth.setup", target=account.email, actor=account.email)
    _start_session(request, response, account.email)
    return {"ok": True, "user": account.public()}


@router.post("/api/auth/login", summary="Sign in with email and password")
def auth_login(body: LoginRequest, request: Request, response: Response):
    _require_local()
    store = get_store()
    ip = _client_ip(request)
    email = (body.email or "").strip().lower()
    pair_key, ip_key = f"{ip}|{email}", f"ip:{ip}"
    retry = max(store.login_throttle.retry_after(pair_key), store.address_throttle.retry_after(ip_key))
    if retry:
        raise HTTPException(
            status_code=429,
            detail=f"Too many failed sign-ins. Try again in {max(1, retry // 60)} minute(s).",
            headers={"Retry-After": str(retry)},
        )
    account = store.authenticate(email, body.password)
    if account is None:
        store.login_throttle.fail(pair_key)
        store.address_throttle.fail(ip_key)
        audit.record("auth.login_failed", target=email, actor=None, detail={"ip": ip})
        # One message for unknown address, wrong password and disabled account.
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    store.login_throttle.clear(pair_key)
    _provision_user(account.email, account.display_name)
    _start_session(request, response, account.email)
    audit.record("auth.login", target=account.email, actor=account.email)
    return {"ok": True, "user": account.public()}


@router.post("/api/auth/logout", summary="End this session")
def auth_logout(request: Request, response: Response):
    store = get_store()
    token = session_token_from_headers(request.headers)
    account = store.lookup_session(token) if token else None
    store.delete_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    if account:
        audit.record("auth.logout", target=account.email, actor=account.email)
    return {"ok": True}


@router.get("/api/auth/link", summary="What does this invite or reset link do?")
def auth_link_info(token: str, request: Request):
    _require_local()
    store = get_store()
    ip = _client_ip(request)
    retry = store.code_throttle.retry_after(f"link:{ip}")
    if retry:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    info = store.peek_link(token)
    if info is None:
        store.code_throttle.fail(f"link:{ip}")
        raise HTTPException(status_code=404, detail="This link is invalid or has expired. Ask an admin for a new one.")
    return {"purpose": info["purpose"], "email": info["email"]}


@router.post("/api/auth/accept", summary="Set a password from an invite or reset link")
def auth_accept(body: AcceptRequest, request: Request, response: Response):
    _require_local()
    store = get_store()
    ip = _client_ip(request)
    retry = store.code_throttle.retry_after(f"link:{ip}")
    if retry:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    try:
        info = store.peek_link(body.token)
        account = store.redeem_link(body.token, body.password, body.display_name)
    except AuthError as exc:
        if exc.status == 400:
            store.code_throttle.fail(f"link:{ip}")
        raise _fail(exc)
    _provision_user(account.email, account.display_name)
    audit.record(
        "auth.invite_accepted" if info and info["purpose"] == "invite" else "auth.password_reset",
        target=account.email, actor=account.email,
    )
    _start_session(request, response, account.email)
    return {"ok": True, "user": account.public()}


# ── signed-in: change my password ───────────────────────────────────────────


@router.post("/api/auth/password", summary="Change my password")
def auth_change_password(body: PasswordChange, request: Request, response: Response):
    _require_local()
    store = get_store()
    identity = getattr(request.state, "auth_identity", None)
    if not identity:
        raise HTTPException(status_code=401, detail="Not authenticated")
    key = f"pw:{identity}"
    retry = store.login_throttle.retry_after(key)
    if retry:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry)})
    if store.authenticate(identity, body.current_password) is None:
        store.login_throttle.fail(key)
        raise HTTPException(status_code=403, detail="Your current password is not right.")
    store.login_throttle.clear(key)
    try:
        # Other devices are signed out; this one stays.
        store.set_password(identity, body.new_password,
                           keep_session=session_token_from_headers(request.headers))
    except AuthError as exc:
        raise _fail(exc)
    audit.record("auth.password_changed", target=identity, actor=identity)
    return {"ok": True}


# ── admin: the Users panel ──────────────────────────────────────────────────


def _link_url(request: Request, token: str) -> str:
    # In the fragment, not the query: a fragment is never sent to the server,
    # so the token stays out of access logs and Referer headers.
    return f"{str(request.base_url).rstrip('/')}/#token={token}"


@router.get("/api/auth/users", summary="List accounts (admin)")
def list_users(request: Request):
    _require_local()
    _require_admin(request)
    return {"users": [a.public() for a in get_store().list_accounts()]}


@router.post("/api/auth/users/invite", summary="Invite someone (admin)", status_code=201)
def invite_user(body: InviteRequest, request: Request):
    _require_local()
    admin = _require_admin(request)
    try:
        email = body.email.strip().lower()
        token = get_store().issue_link("invite", email, created_by=admin, is_admin=body.is_admin)
    except AuthError as exc:
        raise _fail(exc)
    audit.record("auth.invite_created", target=email, actor=admin,
                 detail={"is_admin": body.is_admin})
    return {"email": email, "url": _link_url(request, token), "expires_in_days": 7}


@router.post("/api/auth/users/{email}/reset", summary="Create a password reset link (admin)")
def reset_user(email: str, request: Request):
    _require_local()
    admin = _require_admin(request)
    try:
        token = get_store().issue_link("reset", email, created_by=admin)
    except AuthError as exc:
        raise _fail(exc)
    audit.record("auth.reset_created", target=email.lower(), actor=admin)
    return {"email": email.lower(), "url": _link_url(request, token), "expires_in_hours": 24}


@router.patch("/api/auth/users/{email}", summary="Grant admin or disable an account (admin)")
def update_user(email: str, body: AccountUpdate, request: Request):
    _require_local()
    admin = _require_admin(request)
    try:
        account = get_store().update_account(email, is_admin=body.is_admin, disabled=body.disabled)
    except AuthError as exc:
        raise _fail(exc)
    audit.record("auth.account_updated", target=account.email, actor=admin,
                 detail={"is_admin": body.is_admin, "disabled": body.disabled})
    return account.public()


@router.delete("/api/auth/users/{email}", summary="Delete an account (admin)")
def delete_user(email: str, request: Request):
    _require_local()
    admin = _require_admin(request)
    try:
        get_store().delete_account(email)
    except AuthError as exc:
        raise _fail(exc)
    audit.record("auth.account_deleted", target=email.lower(), actor=admin)
    return {"ok": True}
