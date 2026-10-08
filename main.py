"""
Clio — privacy-focused document indexing, grounded chat, and MCP access.

This module only assembles the app: middleware, lifespan, routers, and
frontend serving. Endpoints live in `api/` (one router module per domain);
business logic lives in `services/`.
"""

import asyncio
import base64
import logging
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from services.indexer_manager import indexer_manager
from services.reindex_service import reindex_service
from services.mcp_server import embedded_mcp_app, mcp_server_lifespan
from api import deps
from api import (
    admin,
    artifacts,
    chat,
    collections,
    documents,
    expertise,
    governance,
    mcp,
    register,
    search,
    sharing,
    system,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ── Security posture ────────────────────────────────────────────────────────
# The app has no authentication unless AUTH_PASSWORD or AUTH_REQUIRE_IDENTITY
# is set, and every route —
# search, upload, delete, chat on your provider keys, the whole /mcp tool
# surface — is reachable by anyone who can open a socket to it. So the two
# settings that decide who can open that socket are checked before we serve.

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _check_security_posture() -> None:
    """Refuse or warn on configurations that promise more safety than we deliver.

    Raises:
        RuntimeError: multi-user mode is requested. Ownership is enforced on
            collection metadata only — search, documents, chat and MCP all take
            a collection_id and never check it — so the flag would advertise an
            isolation boundary that does not exist. See docs/DEPLOYMENT.md.
    """
    # These strings stay ASCII-only: they surface on consoles (Windows cp1252,
    # Docker logs) where an em-dash renders as mojibake.
    if settings.enable_multi_user:
        raise RuntimeError(
            "ENABLE_MULTI_USER is not supported. The flag only filtered the "
            "collection LIST - search, document retrieval, chat and the /mcp "
            "tools accept any collection_id without an ownership check, so it "
            "never isolated anything. Remove ENABLE_MULTI_USER. For per-person "
            "collections with enforced ownership, set PRIVATE_COLLECTIONS=true "
            "behind Cloudflare Access - see docs/DEPLOYMENT.md."
        )

    # Identity source (Cloudflare Access, your own OIDC provider, or an
    # authenticating reverse proxy). Validated whether or not private
    # collections are on: a deployment that configured an identity source at
    # all should learn immediately that it is wrong.
    from services import identity as identity_service

    identity_service.validate_config()

    if settings.auth_require_identity and not identity_service.active_provider():
        raise RuntimeError(
            "AUTH_REQUIRE_IDENTITY requires a verified identity source. Set "
            "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD for Cloudflare Access, "
            "or configure another identity provider. See docs/IDENTITY.md."
        )

    if settings.private_collections and not identity_service.active_provider():
        raise RuntimeError(
            "PRIVATE_COLLECTIONS requires a verified identity source. Set "
            "IDENTITY_PROVIDER to one of: cloudflare_access (CF_ACCESS_* - "
            "internet-facing), oidc (OIDC_ISSUER + OIDC_AUDIENCE - your own "
            "IdP, the on-prem answer), or trusted_header (an authenticating "
            "reverse proxy). Without one, ownership would be enforced against "
            "an identity anyone can forge, which is the half-boundary this "
            "app refuses to ship. See docs/IDENTITY.md."
        )

    # Deployment default LLM (on-prem private endpoints). A typo here would
    # otherwise surface as a failed chat hours later, on someone else's
    # screen, so it fails at startup like the other deployment-shape flags.
    if settings.ai_provider:
        from config import ALL_AI_PROVIDERS
        if settings.ai_provider not in ALL_AI_PROVIDERS:
            raise RuntimeError(
                f"AI_PROVIDER={settings.ai_provider!r} is not a provider this "
                f"app knows. Use one of: {', '.join(ALL_AI_PROVIDERS)}."
            )
        if settings.ai_provider == "openai_compatible" and not settings.ai_base_url:
            raise RuntimeError(
                "AI_PROVIDER=openai_compatible needs AI_BASE_URL - the "
                "OpenAI-style endpoint to call, e.g. "
                "http://vllm.internal:8000/v1. See docs/ONPREM.md."
            )
        logger.info(
            "Deployment AI provider: %s%s%s",
            settings.ai_provider,
            f" at {settings.ai_base_url}" if settings.ai_base_url else "",
            f" (model {settings.ai_model})" if settings.ai_model else "",
        )

    if settings.host not in _LOOPBACK_HOSTS and not (
        settings.auth_password or settings.auth_require_identity
    ):
        logger.warning(
            "SECURITY: bound to %s (reachable from the network) with no "
            "authentication requirement set. Anyone who can reach this port can read, "
            "modify and delete every document, and spend your AI provider "
            "credits. Set AUTH_PASSWORD or AUTH_REQUIRE_IDENTITY, or set "
            "HOST=127.0.0.1 to bind loopback only. See docs/DEPLOYMENT.md.",
            settings.host,
        )

    if settings.cors_allow_origins.strip() == "*" and not (
        settings.auth_password or settings.auth_require_identity
    ):
        logger.warning(
            "SECURITY: CORS_ALLOW_ORIGINS=* with no authentication requirement set. Any web "
            "page the user visits can read this API from their browser and "
            "exfiltrate indexed documents. List the origins that need access "
            "instead."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - initialize and cleanup services."""
    async with mcp_server_lifespan():
        logger.info("Initializing Clio API...")

        # Jobs do not survive the process that ran them, but their rows do.
        # Reconcile before anything can enqueue work, so a restart mid-index
        # does not leave a job that shows as running forever and blocks the
        # collection it was indexing into.
        from services.upload_service import upload_service
        upload_service.recover_orphaned_jobs()

        # Get the embedding model ready in the background with visible
        # progress (GET /api/embedding/status) instead of blocking startup on
        # a download; the job opens the default collection once the model is
        # loaded. Skipped under pytest or CLIO_SKIP_EMBEDDING_WARMUP=1, in
        # which case the default indexer is pre-loaded inline as before.
        from services.embedding_status import embedding_status
        if not embedding_status.warm_on_startup():
            logger.info("Loading default collection indexer...")
            try:
                default_indexer = indexer_manager.get_indexer("default")
                total_chunks = default_indexer.vector_store.get_total_chunks()
                logger.info(f"Default collection indexed chunks: {total_chunks}")
            except Exception as e:
                logger.warning(f"Could not load default indexer: {e}")

        # Set up reload callback for re-indexing service (collection-aware)
        def reload_indexer(collection_id: str = "default"):
            """Rebuild an indexer after re-indexing.

            A rebuild (not a reload) so that a re-index run under a newly
            chosen embedding provider — different vector dimension — swaps
            in cleanly instead of failing the old store's dimension check.
            """
            try:
                logger.info("=" * 60)
                logger.info(f"RELOAD CALLBACK TRIGGERED for collection: {collection_id}")
                logger.info("=" * 60)

                indexer_manager.rebuild_indexer(collection_id)

                stats = indexer_manager.get_collection_stats(collection_id)
                logger.info(f"Reload complete. Collection {collection_id} indexed chunks: {stats['total_chunks']}")
                logger.info("=" * 60)

            except Exception as e:
                logger.error(f"RELOAD FAILED for collection {collection_id}: {e}", exc_info=True)

        reindex_service.reload_callback = reload_indexer

        deps.mark_initialized()

        # Retention sweeps for the unbounded log tables (search_history
        # stores result snippets; chat_usage grows per turn). First run is
        # delayed past startup, then daily.
        from services.retention import retention_loop
        retention_task = asyncio.create_task(retention_loop())

        logger.info("Clio API ready")
        logger.info(f"Data directory: {settings.data_dir}")
        logger.info(f"Embedded MCP server: {'enabled' if settings.enable_mcp else 'disabled'}")

        yield

        # Cleanup on shutdown
        logger.info("Shutting down Clio API...")
        retention_task.cancel()
        indexer_manager.save_all()
        logger.info("Shutdown complete")


_check_security_posture()

app = FastAPI(
    title="Clio API",
    description="Privacy-focused document indexing, grounded chat, and MCP access",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS: origins come from CORS_ALLOW_ORIGINS (comma-separated). Empty — the
# default — installs no CORS middleware at all, so the same-origin policy keeps
# other sites from reading responses. The bundled frontend is same-origin and
# the Vite dev server proxies to the backend, so neither needs an exception;
# non-browser clients (MCP, curl, SDKs) are unaffected by CORS entirely.
# Credentialed cross-origin requests are allowed only for explicitly listed
# origins — wildcard + credentials would make Starlette echo any Origin back,
# letting arbitrary web pages drive the API with the browser's stored
# credentials.
_cors_origins = [o.strip() for o in settings.cors_allow_origins.split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_cors_origins != ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Rate limiting ───────────────────────────────────────────────────────────
# Registered BEFORE the auth block on purpose: Starlette runs the last-added
# middleware first, so auth ends up outermost and resolves
# request.state.auth_identity before the limiter reads it. Deliberately
# always-on (including on a local/open deployment) — an open deployment still deserves limits.
from middleware.rate_limit import enforce_rate_limit as _enforce_rate_limit

app.middleware("http")(_enforce_rate_limit)


# ── Request authentication ─────────────────────────────────────────────────
# AUTH_PASSWORD accepts a shared secret; AUTH_REQUIRE_IDENTITY instead
# requires an assertion from a configured identity provider.
# browser flow zero-UI; Bearer covers API and MCP clients. /health stays open
# so platform probes work unauthenticated.

def _password_from_auth_header(header: str) -> str:
    """Extract the presented password from a Basic or Bearer Authorization header."""
    scheme, _, value = header.partition(" ")
    scheme = scheme.lower()
    value = value.strip()
    if scheme == "bearer":
        return value
    if scheme == "basic":
        try:
            decoded = base64.b64decode(value).decode("utf-8")
        except Exception:
            return ""
        return decoded.partition(":")[2]
    return ""


# Paths a person can reach before they have any identity: the health probe
# and the online-registration page with its two endpoints. Everything the
# registration endpoints do is validated and rate-limited on their own
# (api/register.py); a deployment that enables registration must also allow
# these paths through its identity proxy.
_PUBLIC_PATHS = {
    "/health",
    "/register",
    "/api/register",
    "/api/register/config",
    "/request-access",
}
# The built frontend bundle is public too: the registration page is the same
# SPA, so its hashed assets and brand files must load before sign-in. The
# bundle holds no secrets — every fact about the deployment comes from the
# API, which stays gated.
_PUBLIC_STATIC = {"/favicon.ico", "/manifest.webmanifest", "/apple-touch-icon.png",
                  "/clio-mark.png", "/clio-mark-dark.png", "/clio-icon-maskable.png",
                  "/clio-og.png"}


def _client_ip(request) -> str | None:
    """Peer address as the server sees it, for the identity layer's optional
    CIDR check. Deliberately NOT read from X-Forwarded-For here — a value the
    caller supplies cannot guard anything (services/identity.py explains why
    even this one is defence in depth only)."""
    return request.client.host if request.client else None


def _is_public_path(path: str) -> bool:
    return (
        path in _PUBLIC_PATHS
        or path in _PUBLIC_STATIC
        or path.startswith("/assets/")
        or path.startswith("/icons/")
        # RFC 9728 protected-resource metadata: an MCP client reads it
        # before it has any credential. It names the authorization server
        # and nothing else (services/mcp_oauth.py).
        or path.startswith("/.well-known/oauth-protected-resource")
    )


@app.middleware("http")
async def require_auth(request, call_next):
    if _is_public_path(request.url.path):
        return await call_next(request)
    # CORS preflights are sent without credentials by spec, and this
    # middleware runs outside CORSMiddleware — pass them through so the
    # preflight can be answered; the actual request still authenticates.
    if request.method == "OPTIONS":
        return await call_next(request)

    # Verified identity: a Cloudflare Access assertion, a bearer JWT from the
    # site's own IdP, or a header an authenticating proxy already vouched for
    # (services/identity.py). Any of these is stronger auth than the shared
    # password — per-identity and revocable at the source — and accepting one
    # removes the browser's second login prompt after SSO. Verification runs
    # in a thread: it can hit the network for a JWKS refresh.
    # ...but only for a request that actually carries one: has_candidate is a
    # cheap header look, so a password-authenticated call never spends a
    # threadpool slot to discover it has no assertion.
    # On /mcp the verifier also covers OAuth bearer tokens from the
    # authorization server the endpoint advertises (services/mcp_oauth.py).
    from services.identity import InsufficientScope, get_identity_verifier, is_mcp_path
    verifier = get_identity_verifier(request.url.path)
    if verifier is not None and verifier.has_candidate(request.headers):
        try:
            who = await asyncio.to_thread(
                verifier.verify_request, request.headers, _client_ip(request)
            )
        except InsufficientScope as exc:
            # A token that is valid for this app but not for /mcp. 403 with
            # the scope named, per the MCP authorization spec, so the client
            # can step up instead of looping on a 401 it cannot satisfy.
            from services.mcp_oauth import challenge
            return JSONResponse(
                {"detail": f"This token lacks the scope '{exc.scope}' required for the MCP endpoint."},
                status_code=403,
                headers={"WWW-Authenticate": challenge(request, error="insufficient_scope")},
            )
        if who:
            request.state.auth_identity = who
            request.state.auth_via = verifier.via
            return await _call_with_user_context(request, call_next)

    presented = _password_from_auth_header(request.headers.get("authorization", ""))

    if settings.auth_password and not settings.auth_require_identity:
        if presented and secrets.compare_digest(presented, settings.auth_password):
            request.state.auth_identity = None
            request.state.auth_via = "password"
            return await _call_with_user_context(request, call_next)

    # Personal MCP access tokens: self-serve alternative to a Cloudflare
    # Access service token, minted from the app itself (Settings → MCP)
    # by anyone who can already reach it. Deliberately scoped to /mcp —
    # a leaked token cannot touch the rest of the API or the UI. Under
    # private collections it resolves to the identity that created it,
    # so an MCP client sees exactly that person's collections.
    if (request.url.path == "/mcp" or request.url.path.startswith("/mcp/")) and presented and presented.startswith("asy_mcp_"):
        from services.mcp_tokens import verify_token
        token_record = await asyncio.to_thread(verify_token, presented)
        if token_record is not None:
            request.state.auth_identity = token_record.get("user_id")
            request.state.auth_via = "mcp_token"
            # Restricted collections are only reachable over MCP through
            # a token explicitly scoped to them (services/governance.py).
            request.state.mcp_token_scope = token_record.get("collection_scope") or None
            # An allowlisted token sees ONLY its scoped collections.
            request.state.mcp_token_allowlist = bool(token_record.get("allowlist"))
            # Adding/updating sources over MCP is opt-in per token.
            request.state.mcp_token_can_write = bool(token_record.get("can_write"))
            # Which credential made the call, for the MCP audit trail. The
            # id, never the token — the plaintext is unrecoverable by design.
            request.state.mcp_token_id = token_record.get("id")
            # Any MCP rate budget is per token, not per identity or IP.
            request.state.rate_limit_key = f"mcp-token:{token_record.get('id')}"
            return await _call_with_user_context(request, call_next)

    # An explicit MCP credential must retain its permissions even on a
    # local/open appliance. Never silently turn a revoked token into an
    # anonymous (fully trusted) request.
    if (
        not settings.auth_password
        and not settings.auth_require_identity
        and not settings.private_collections
        and not (
            presented and presented.startswith("asy_mcp_")
        )
    ):
        return await call_next(request)

    # Browsers get the Basic challenge so the native prompt still works. The
    # /mcp subtree gets the Bearer challenge of the MCP authorization spec:
    # with an authorization server configured it carries the RFC 9728
    # resource_metadata URL an OAuth client discovers from, otherwise a
    # plain Bearer realm for bearer-header clients.
    if is_mcp_path(request.url.path):
        from services.mcp_oauth import challenge
        www_authenticate = challenge(request, error="invalid_token" if presented else None)
    else:
        www_authenticate = 'Basic realm="Clio"'
    return JSONResponse(
        {"detail": "Not authenticated"},
        status_code=401,
        headers={"WWW-Authenticate": www_authenticate},
    )

async def _call_with_user_context(request, call_next):
    """Bind the verified identity and collection scope to this request.

    Routers read identity from request.state; service-layer code that has
    no Request in reach (deps.get_indexer, the chat tool loop) reads the
    contextvars. Set before call_next so the downstream task inherits them.
    """
    from middleware.user_context import (
        set_request_identity,
        reset_request_identity,
        set_request_user,
        reset_request_user,
    )
    identity = getattr(request.state, "auth_identity", None)
    identity_token = set_request_identity(identity)
    user_token = set_request_user(identity) if settings.private_collections else None
    try:
        return await call_next(request)
    finally:
        if user_token is not None:
            reset_request_user(user_token)
        reset_request_identity(identity_token)

for module in (system, documents, search, chat, artifacts, collections, mcp, sharing, expertise, admin, governance, register):
    app.include_router(module.router)


# ── Request metadata (ids, access log, in-flight counters) ─────────────────
# Added last, which makes it the OUTERMOST middleware: it times auth and rate
# limiting too, and reads the identity auth left on request.state for its
# access line. /api/admin/stats reads its counters.
from middleware.request_meta import track_request as _track_request

app.middleware("http")(_track_request)


# ── Frontend serving ────────────────────────────────────────────────────────
# The Vue app is built by Vite into frontend/dist (not committed to git).
# Hashed assets under /assets are immutable and cached for a year; index.html
# always revalidates so a new deploy is picked up immediately.

FRONTEND_DIST = Path(__file__).parent / "frontend" / "dist"


class SPAStaticFiles(StaticFiles):
    """StaticFiles with cache headers tuned for a Vite SPA build."""

    def file_response(self, full_path, stat_result, scope, status_code: int = 200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        if "/assets/" in scope.get("path", ""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def _spa_response() -> HTMLResponse:
    index_path = FRONTEND_DIST / "index.html"
    if index_path.exists():
        return HTMLResponse(
            content=index_path.read_text(encoding="utf-8"),
            status_code=200,
            headers={"Cache-Control": "no-cache"},
        )
    return HTMLResponse(
        content=(
            "<h1>Clio API</h1>"
            "<p>Frontend build not found — run <code>cd frontend && npm run build</code>, "
            "or visit <a href='/docs'>/docs</a> for API documentation.</p>"
        ),
        status_code=200,
    )


@app.get("/", response_class=HTMLResponse, tags=["ui"], include_in_schema=False)
async def web_interface():
    """Serve the web interface."""
    return _spa_response()


@app.get("/register", response_class=HTMLResponse, tags=["ui"], include_in_schema=False)
async def register_page():
    """The public registration page: the same SPA bundle, which renders the
    registration view when loaded at this path (frontend/src/main.js)."""
    return _spa_response()


@app.get(
    "/request-access",
    response_class=HTMLResponse,
    tags=["ui"],
    include_in_schema=False,
)
async def request_access_page():
    """Give people who are not on the Access allowlist a contact path."""
    return HTMLResponse(
        content="""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light dark">
  <title>Request access · Clio</title>
  <style>
    :root { color-scheme: light dark; font: 16px/1.6 system-ui, sans-serif; }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
      background: Canvas; color: CanvasText; }
    main { box-sizing: border-box; width: min(100% - 2rem, 34rem); padding: 2rem 0; }
    h1 { margin: 0; font-size: 1.65rem; line-height: 1.2; letter-spacing: -.025em; }
    p { margin: 1rem 0; color: color-mix(in srgb, CanvasText 75%, Canvas); }
    a { color: LinkText; }
    .contact { display: inline-block; margin-top: .5rem; padding: .6rem .9rem;
      border: 1px solid currentColor; border-radius: .4rem; text-decoration: none;
      font-weight: 600; }
  </style>
</head>
<body>
  <main>
    <h1>Request access to Clio</h1>
    <p>Access is limited to invited people. Email Jordan with your name and the
       email address you want to use, and he can add you to the sign-in list.</p>
    <a class="contact" href="mailto:jordan.boyce@cyberlion.dev?subject=Clio%20access%20request">
      Email Jordan
    </a>
  </main>
</body>
</html>""",
        headers={"Cache-Control": "no-store"},
    )


# Mounts must come after all routes so they don't override API routes.
app.mount("/mcp", embedded_mcp_app, name="mcp")

if (FRONTEND_DIST / "index.html").exists():
    app.mount("/", SPAStaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    # Auto-reload is opt-in (RELOAD=1) because uvicorn's reloader runs the
    # actual server in a multiprocessing child. Anything that stops the parent
    # without a clean Ctrl+C — a task kill, a crashed terminal — orphans that
    # child, which keeps running and holds the port.
    # With reload off, this process IS the server: kill it and the port frees.
    dev_reload = os.environ.get("RELOAD", "").lower() in ("1", "true", "yes")

    uvicorn_kwargs: dict = {
        "host": settings.host,
        "port": settings.port,
        "reload": dev_reload,
    }

    cert_path = Path(settings.ssl_certfile).expanduser() if settings.ssl_certfile else None
    key_path = Path(settings.ssl_keyfile).expanduser() if settings.ssl_keyfile else None
    if cert_path and key_path and cert_path.is_file() and key_path.is_file():
        uvicorn_kwargs["ssl_certfile"] = str(cert_path)
        uvicorn_kwargs["ssl_keyfile"] = str(key_path)
        logger.info(f"HTTPS enabled — serving on https://{settings.host}:{settings.port}")
    elif settings.ssl_certfile or settings.ssl_keyfile:
        logger.warning(
            "ssl_certfile / ssl_keyfile configured but one or both files are missing — "
            "falling back to plain HTTP. Run certs/generate-cert.sh to create a dev pair."
        )

    uvicorn.run("main:app", **uvicorn_kwargs)
