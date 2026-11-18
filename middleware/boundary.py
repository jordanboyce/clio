"""Which Host a request may name, and which pages may change state.

Two checks at the edge of the app, before authentication:

* **Host allowlist.** A page on a domain someone else controls can point that
  domain at 127.0.0.1 (DNS rebinding). The browser then treats this app as
  that page's own origin and lets it read every answer, so the same-origin
  policy protects nothing. Refusing a Host the deployment does not answer to
  closes that. ALLOWED_HOSTS names the hosts (loopback is always among them).
  Unset, the default install (loopback, no credential required, where the
  browser is the only gate) answers to the loopback names only. Anything else
  accepts any Host until the names are listed: a network bind is reached by
  names nobody can guess (an IP address, a LAN name), and a loopback install
  behind a reverse proxy sees the proxy's public name. Both require a
  credential the rebinding page's origin does not hold, or the readiness
  report warns.

* **Origin check on state-changing methods.** A form or a fetch from another
  site can POST a multipart upload without a CORS preflight, and the browser
  attaches any password it holds for this app. A POST, PUT, PATCH or DELETE
  whose Origin (or, when it sends none, whose Sec-Fetch-Site) says it came
  from a foreign page is refused with 403. Requests that carry neither header
  (curl, SDKs, agents) are not browsers and pass. /api/v1 takes only an app's
  bearer credential, which a browser never attaches by itself, and /mcp is
  checked by the MCP SDK, so both are left alone.

Probe paths are never refused, since platform probes name whatever Host they
like (a pod IP, a health-check domain); a probe with a foreign Host is marked
so /health gives it the bare status only.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import FrozenSet, Optional
from urllib.parse import urlsplit

import config

logger = logging.getLogger(__name__)

LOOPBACK_NAMES: FrozenSet[str] = frozenset({"localhost", "127.0.0.1", "::1"})
_STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PROBE_PATHS = frozenset({"/health", "/livez", "/readyz"})
# Sec-Fetch-Site values a browser sends only for the app's own pages, or for
# something the person did themselves (a bookmark, the address bar). Page
# script cannot set the header.
_BROWSER_VOUCHED = frozenset({"same-origin", "none"})

# Each refused Host is logged once, so a misconfigured proxy shows up in the
# log without a scanner filling it.
_logged_hosts: set = set()
_logged_lock = threading.Lock()
_LOGGED_HOSTS_MAX = 100


def hostname(value: str) -> str:
    """The host part of a Host header or list entry, lower case.

    ``Clio.Example:8473`` -> ``clio.example``; ``[::1]:8473`` -> ``::1``;
    ``https://clio.example/`` -> ``clio.example``.
    """
    value = (value or "").strip().lower()
    if "://" in value:
        value = urlsplit(value).netloc
    value = value.rsplit("@", 1)[-1]
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


@lru_cache(maxsize=16)
def _allowlist(allowed_hosts: str, open_loopback: bool) -> Optional[FrozenSet[str]]:
    configured = {hostname(h) for h in allowed_hosts.split(",") if h.strip()}
    configured.discard("")
    if configured:
        return LOOPBACK_NAMES | configured
    return LOOPBACK_NAMES if open_loopback else None


def allowed_hostnames(cfg=None) -> Optional[FrozenSet[str]]:
    """Host names a request may name, or None when any is accepted."""
    from services import readiness

    cfg = cfg if cfg is not None else config.settings
    open_loopback = not readiness.reachable_beyond_host(cfg) and not readiness.auth_gate(cfg)
    return _allowlist(cfg.allowed_hosts or "", open_loopback)


def _cors_origins(cfg) -> list:
    return [o.strip().rstrip("/").lower() for o in (cfg.cors_allow_origins or "").split(",") if o.strip()]


def _origin_allowed(request, origin: str, cfg, allowlist: Optional[FrozenSet[str]]) -> bool:
    cors = _cors_origins(cfg)
    if "*" in cors:
        return True  # the operator let any page in, and is warned about it at startup
    origin = origin.strip().rstrip("/").lower()
    if origin in cors:
        return True
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False  # "null": a sandboxed frame, a file:// page, a data: URL
    # The page is on the address the request was sent to. A proxy that rewrites
    # Host usually says what it was in X-Forwarded-Host; a cross-site page cannot
    # add that header without a preflight, which fails.
    for header in ("host", "x-forwarded-host"):
        sent_to = request.headers.get(header, "").split(",")[0].strip().lower()
        if sent_to and parts.netloc == sent_to:
            return True
    names = allowlist if allowlist is not None else LOOPBACK_NAMES
    return hostname(parts.netloc) in names


def _exempt_from_origin_check(path: str) -> bool:
    return path.startswith("/api/v1/") or path == "/mcp" or path.startswith("/mcp/")


def _log_refused_host(host: str) -> None:
    with _logged_lock:
        if host in _logged_hosts or len(_logged_hosts) >= _LOGGED_HOSTS_MAX:
            return
        _logged_hosts.add(host)
    logger.warning(
        "Refused a request for Host %r: not one this deployment answers to. If people reach "
        "Clio under that name, add it to ALLOWED_HOSTS.", host,
    )


async def enforce_boundary(request, call_next):
    """The middleware body, registered in main.py outside authentication."""
    from fastapi.responses import JSONResponse

    cfg = config.settings
    allowlist = allowed_hostnames(cfg)
    host_header = request.headers.get("host", "")
    if allowlist is not None and host_header and hostname(host_header) not in allowlist:
        if request.url.path in _PROBE_PATHS:
            request.state.foreign_host = True
            return await call_next(request)
        _log_refused_host(hostname(host_header))
        return JSONResponse(
            {
                "detail": "This server does not answer to that host name. An operator can add it "
                          "to ALLOWED_HOSTS.",
                "code": "host_not_allowed",
            },
            status_code=421,
        )

    if request.method not in _STATE_CHANGING or _exempt_from_origin_check(request.url.path):
        return await call_next(request)
    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if fetch_site in _BROWSER_VOUCHED:
        return await call_next(request)
    origin = request.headers.get("origin")
    if origin is not None:
        allowed = _origin_allowed(request, origin, cfg, allowlist)
    else:
        allowed = fetch_site != "cross-site"
    if allowed:
        return await call_next(request)
    logger.info(
        "Refused cross-site %s %s (Origin %r, Sec-Fetch-Site %r)",
        request.method, request.url.path, (origin or "")[:200], fetch_site,
    )
    return JSONResponse(
        {
            "detail": "This request came from a page on another site and was refused. If a web "
                      "app on another origin needs to call this API, list that origin in "
                      "CORS_ALLOW_ORIGINS.",
            "code": "cross_site_request",
        },
        status_code=403,
    )
