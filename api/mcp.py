"""Embedded MCP server configuration endpoints."""

import logging
from typing import Optional
from pydantic import BaseModel, Field, StrictBool

from fastapi import Depends, HTTPException, status, Request

from middleware.user_context import get_current_user_id
from services.config_manager import config_manager
from services import mcp_tokens
from services.mcp_server import (
    MCP_CONFIG_FIELDS,
    get_mcp_settings_payload,
)

from fastapi import APIRouter
from api.deps import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


class MCPTokenRequest(BaseModel):
    name: str = Field("", max_length=200)
    collection_scope: list[str] = Field(default_factory=list, max_length=100)
    can_write: StrictBool = False
    # Days until the token stops working; omit for a non-expiring token.
    expires_in_days: Optional[int] = Field(None, ge=1, le=mcp_tokens.MAX_EXPIRY_DAYS)
    # When true, collection_scope is the ONLY set of collections the token
    # may see over MCP (any sensitivity), not merely a restricted grant.
    allowlist: StrictBool = False


@router.get(
    "/api/mcp/config",
    summary="Get MCP configuration",
    tags=["mcp"],
)
async def get_mcp_config():
    """Get the embedded MCP server configuration."""
    return get_mcp_settings_payload()


@router.get(
    "/api/mcp/catalog",
    summary="Tools, resources and prompts the MCP server exposes",
    tags=["mcp"],
)
async def get_mcp_catalog(user_id: Optional[str] = Depends(get_current_user_id)):
    """What an MCP client will see: every tool with its title, opening
    description, read-only/destructive hints and parameter names; every
    resource URI template; every prompt with its arguments. Built from the
    live server registry so it cannot drift from what /mcp/ advertises."""
    from services.mcp_server import mcp_catalog

    return await mcp_catalog()


@router.post(
    "/api/mcp/config",
    summary="Update MCP configuration",
    tags=["mcp"],
)
async def update_mcp_config(updates: dict):
    """Update embedded MCP settings without restarting the app."""
    require_admin("change MCP server settings")
    filtered_updates = {
        key: value
        for key, value in updates.items()
        if key in MCP_CONFIG_FIELDS
    }
    if not filtered_updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No MCP configuration fields were provided.",
        )

    return config_manager.update_config(filtered_updates)


# ── Personal MCP access tokens ──────────────────────────────────────────────
# Self-serve alternative to a Cloudflare Access service token: anyone who can
# already reach the app mints their own bearer credential for headless MCP
# clients here, instead of provisioning Access + hand-editing .env/.mcp.json.
# These endpoints sit behind the normal app auth (password / SSO), same as
# /api/shares; the token they hand out is separately scoped to /mcp only by
# the auth middleware in main.py.

@router.post(
    "/api/mcp/tokens",
    summary="Create a personal MCP access token",
    tags=["mcp"],
)
async def create_mcp_token(body: MCPTokenRequest, user_id: Optional[str] = Depends(get_current_user_id)):
    """Mint a token. The plaintext is returned once and cannot be recovered.

    Body:
        name: label for the device/client
        collection_scope: optional list of *restricted* collection ids this
            token may reach over MCP. Restricted collections are otherwise
            invisible to every MCP client; the scope is the explicit grant.
            Only collections the caller can already access are accepted.
        can_write: optional bool (default false). Lets agents holding this
            token add and update sources via the write_document tool.
        expires_in_days: optional lifetime; the token stops verifying after
            it. Omitted means the token never expires.
        allowlist: optional bool (default false). Makes collection_scope the
            only collections the token may see over MCP, whatever their
            sensitivity. Requires a non-empty scope.
    """
    from services import audit
    from services.collection_service import collection_service

    name = body.name
    can_write = body.can_write
    raw_scope = body.collection_scope
    visible = {c["id"] for c in collection_service.get_all_collections(user_id) if c.get("id")}
    scope = []
    for cid in raw_scope:
        cid = str(cid).strip()
        if cid and cid not in visible:
            raise HTTPException(status_code=404, detail=f"Collection '{cid}' not found")
        if cid and cid not in scope:
            scope.append(cid)

    try:
        record = mcp_tokens.generate_token(
            user_id, name, collection_scope=scope or None, can_write=can_write,
            expires_in_days=body.expires_in_days, allowlist=body.allowlist,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    audit.record("mcp_token.create", actor=user_id, target=record.get("id"),
                 detail={"name": record.get("name"), "collection_scope": scope or None,
                         "can_write": can_write, "expires_at": record.get("expires_at"),
                         "allowlist": bool(body.allowlist)})
    return record


@router.get(
    "/api/mcp/tokens",
    summary="List my personal MCP access tokens",
    tags=["mcp"],
)
async def list_mcp_tokens(user_id: Optional[str] = Depends(get_current_user_id)):
    return {"tokens": mcp_tokens.list_tokens(user_id)}


@router.delete(
    "/api/mcp/tokens/{token_id}",
    summary="Revoke a personal MCP access token",
    tags=["mcp"],
)
async def revoke_mcp_token(token_id: str, user_id: Optional[str] = Depends(get_current_user_id)):
    if not mcp_tokens.revoke_token(token_id, user_id):
        raise HTTPException(status_code=404, detail="Token not found")
    from services import audit
    audit.record("mcp_token.revoke", actor=user_id, target=token_id)
    return {"revoked": True}


# ── OAuth 2.1 for MCP clients ───────────────────────────────────────────────
# RFC 9728 protected-resource metadata: the document an MCP client reads
# (from the /mcp 401 challenge, or by probing these well-known paths) to
# learn which authorization server signs people in. Public by design -
# main.py exempts the prefix from auth - and it names the IdP and nothing
# else. 404 when no authorization server applies to /mcp, which is the
# signal the spec gives a client to fall back or ask the user.

@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
@router.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
async def oauth_protected_resource_metadata(request: Request):
    from fastapi.responses import JSONResponse
    from services import mcp_oauth

    document = mcp_oauth.protected_resource_metadata(request)
    if document is None:
        raise HTTPException(
            status_code=404,
            detail="OAuth is not configured for the MCP endpoint. Set IDENTITY_PROVIDER=oidc "
                   "or MCP_OAUTH_ISSUER; see docs/IDENTITY.md.",
        )
    return JSONResponse(
        document,
        headers={
            "Cache-Control": "public, max-age=3600",
            # Browser-hosted MCP clients (an inspector, a web app) fetch this
            # cross-origin before they have any credential; it is public.
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get(
    "/api/mcp/oauth",
    summary="How OAuth-capable MCP clients connect",
    tags=["mcp"],
)
async def get_mcp_oauth(request: Request):
    """Whether connector-style clients (Claude Desktop, claude.ai, ChatGPT,
    Claude Code without a token) can sign in, and the URL they paste."""
    from services import mcp_oauth

    return mcp_oauth.summary(request)


# Redirect bare /mcp (no trailing slash) to /mcp/ so MCP clients that use the old
# exported URL still work. Uses 307 to preserve the HTTP method (POST stays POST).
@router.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def mcp_trailing_slash_redirect(request: Request):
    url = str(request.url)
    redirect_url = url.replace("/mcp?", "/mcp/?", 1) if "?" in url else url.rstrip("/") + "/"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(redirect_url, status_code=307)
