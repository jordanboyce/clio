"""GET /api/bootstrap: the web app's startup reads in one request.

Opening the app used to send about 26 requests, each spending the caller's
rate limit and, on a shared deployment, passing through the failed sign-in
budget (middleware/auth_failures.py). This route answers the same reads in one
response. It does not reimplement any of them: each section is the existing
GET route, run in-process through the app's router with this request's scope,
so it sees the identity, collection scope and exception handlers this request
was given after authentication, and returns exactly the body and status that
route would have. The list of routes is fixed here; a caller cannot name one.

The frontend (frontend/src/utils/bootstrap.js) answers a startup GET from the
matching section when it came back 2xx, and sends the real request otherwise,
so a section that fails here fails there exactly as it did before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

# What the first view reads whatever collection is open.
_GLOBAL: List[Tuple[str, Dict[str, Any]]] = [
    ("/api/me", {}),
    ("/api/user/me", {}),
    ("/api/config", {}),
    ("/api/features", {}),
    ("/api/capabilities", {}),
    ("/api/agent/config", {}),
    ("/api/ai/deployment", {}),
    ("/api/custom-file-types", {}),
    ("/api/collections", {}),
    ("/api/admin/review/summary", {}),  # an admin's badge; 403 for anyone else, who never asks
    ("/api/expertise/packs", {}),
    ("/api/chat/cache", {}),
    ("/api/reindex/status", {}),
    ("/documents/jobs", {"limit": 15}),
    ("/health", {}),
]

# And for the collection it opens on. The query parameters are the ones the
# app sends, so its requests match these keys.
def _for_collection(cid: str) -> List[Tuple[str, Dict[str, Any]]]:
    return [
        (f"/api/collections/{cid}/stats", {}),
        # Can probe the embedding model, so it is the section most likely to
        # run past SECTION_TIMEOUT_SECONDS; the app then asks for it itself.
        (f"/api/collections/{cid}/embedding-health", {}),
        ("/health", {"collection_id": cid}),
        ("/documents", {"collection_id": cid, "limit": 200, "offset": 0}),
        (f"/api/collections/{cid}/expertise", {}),
        ("/documents/sync-folders", {"collection_id": cid}),
        ("/documents/supersede-suggestions", {"collection_id": cid}),
        ("/documents/changes", {"collection_id": cid, "since": 0, "limit": 20}),
    ]


_COLLECTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# A section that takes longer than this is left out rather than holding up
# every other one; the app then asks for it directly.
SECTION_TIMEOUT_SECONDS = 10.0


def key_for(path: str, params: Dict[str, Any]) -> str:
    """The section key: the path and its query, parameters sorted by name."""
    items = sorted((k, str(v)) for k, v in params.items() if v is not None)
    return f"{path}?{urlencode(items)}" if items else path


async def _run_get(request: Request, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """One GET through the app's router, inside this request's scope."""
    query = urlencode(sorted((k, str(v)) for k, v in params.items())).encode()
    scope = dict(request.scope)
    scope.update({
        "type": "http", "method": "GET", "path": path, "raw_path": path.encode(),
        "query_string": query, "path_params": {},
        "headers": [(k, v) for k, v in request.scope["headers"] if k not in (b"content-length", b"content-type")],
    })
    for routed in ("endpoint", "route"):
        scope.pop(routed, None)

    async def receive() -> Dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    status: Dict[str, int] = {}
    body: List[bytes] = []

    async def send(message: Dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            status["code"] = message["status"]
        elif message["type"] == "http.response.body":
            body.append(message.get("body", b""))

    try:
        await asyncio.wait_for(request.app.router(scope, receive, send), SECTION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        return {"status": 504}
    except StarletteHTTPException as e:  # the router's own 404/405 for a route that is not there
        return {"status": e.status_code}
    except Exception:
        logger.exception("Bootstrap section %s failed", path)
        return {"status": 500}
    code = status.get("code", 500)
    if not 200 <= code < 300:
        return {"status": code}
    try:
        return {"status": code, "body": json.loads(b"".join(body) or b"null")}
    except ValueError:
        return {"status": 500}


@router.get("/api/bootstrap", summary="Everything the app reads when it opens, in one response")
async def bootstrap(request: Request, collection_id: Optional[str] = None) -> Dict[str, Any]:
    """The app's startup reads, each as ``{status, body}`` under its key.

    A key is the route's path and query (``/documents?collection_id=default&limit=200&offset=0``).
    ``body`` is present only for a 2xx answer: anything else is reported by
    status alone, and the app asks that route itself to get the error. With
    ``collection_id`` it also includes the reads for that collection.
    """
    wanted = list(_GLOBAL)
    if collection_id:
        if not _COLLECTION_ID.match(collection_id):
            raise HTTPException(status_code=400, detail="That is not a collection id.")
        wanted += _for_collection(collection_id)
    # This request passed authentication to get here, so it may see what
    # /health shows an authenticated caller (main.require_auth decides that
    # for /health itself, which is public).
    request.state.health_detail = True
    # Record a first-time user once, here, rather than in every section at once.
    from middleware.user_context import get_current_user_id

    get_current_user_id(request)
    results = await asyncio.gather(*(_run_get(request, path, params) for path, params in wanted))
    return {"responses": {key_for(path, params): result for (path, params), result in zip(wanted, results)}}
