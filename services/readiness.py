"""Deployment readiness: is this install actually ready to run where it is going?

One list of checks, three consumers: ``GET /api/system/readiness`` (the About
dialog and any monitoring), ``scripts/preflight.py`` (run before an image is
carried into a secure environment, and again on arrival), and the startup
guard for ``CLIO_PROFILE=secure``, which refuses to start when a *fatal*
posture check fails instead of logging a warning nobody reads.

Each check answers a question an accreditation reviewer asks: is the network
boundary what we said it is, is everything the product needs already on the
machine, and where could data leave. Nothing here changes state; the only
network traffic is the optional ``probe`` of the configured model endpoint.

The report also lists every outbound path the current configuration allows
(``egress``) so "what can this talk to?" has a single answer that is
generated from the live settings rather than written from memory.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config import CLOUD_AI_PROVIDERS, settings

logger = logging.getLogger(__name__)

PASS, WARN, FAIL = "pass", "warn", "fail"

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_LOW_DISK_WARN = 2 * 1024 ** 3
_LOW_DISK_FAIL = 200 * 1024 ** 2


def _check(id: str, group: str, title: str, status: str, detail: str, fix: str = "") -> Dict[str, Any]:
    return {"id": id, "group": group, "title": title, "status": status, "detail": detail, "fix": fix}


def is_secure_profile() -> bool:
    return settings.clio_profile == "secure"


def _secure_or(secure_status: str, other: str = WARN) -> str:
    """A posture gap is a failure under the secure profile and a warning otherwise."""
    return secure_status if is_secure_profile() else other


def reachable_beyond_host(cfg=None) -> bool:
    """Can another machine open the port?

    HOST decides, except inside a container, where HOST is always 0.0.0.0
    and the published interface (BIND_ADDRESS, passed through by compose) is
    what actually limits exposure. A loopback publish is loopback.

    ``cfg`` lets a caller that holds a fresher Settings (main.py after a
    reload under test) be judged on that rather than the one imported here.
    """
    cfg = cfg if cfg is not None else settings
    if cfg.host in _LOOPBACK_HOSTS:
        return False
    bind = cfg.bind_address.strip()
    return not bind or bind not in _LOOPBACK_HOSTS


_reachable_beyond_host = reachable_beyond_host


def _has_gate() -> Optional[str]:
    from services import identity

    if settings.auth_password:
        return "AUTH_PASSWORD"
    if identity.requires_identity():
        return f"identity provider ({settings.identity_provider})"
    return None


# ── Posture ──────────────────────────────────────────────────────────────

def _posture_checks() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    out.append(_check(
        "profile", "posture", "Deployment profile", PASS,
        "secure: offline, fail-closed on unsafe exposure" if is_secure_profile()
        else "standard (set CLIO_PROFILE=secure for the hardened defaults)",
    ))

    if settings.offline_mode:
        out.append(_check("offline", "posture", "Offline mode", PASS,
                          "Cloud AI providers, hosted embeddings, web links and model downloads are disabled."))
    else:
        out.append(_check("offline", "posture", "Offline mode", _secure_or(FAIL),
                          "OFFLINE_MODE is off: cloud providers and downloads are permitted.",
                          "Set OFFLINE_MODE=1 (the secure profile does this for you)."))

    gate = _has_gate()
    if gate:
        out.append(_check("auth", "posture", "Access control", PASS, f"Requests need a credential ({gate})."))
    elif _reachable_beyond_host():
        out.append(_check("auth", "posture", "Access control", _secure_or(FAIL),
                          f"Listening on {settings.host} with no password or identity provider: anyone who can "
                          "reach the port can read, change and delete every document.",
                          "Set AUTH_PASSWORD, set IDENTITY_PROVIDER, or bind HOST=127.0.0.1 behind a proxy."))
    else:
        out.append(_check("auth", "posture", "Access control", PASS,
                          "Bound to loopback only; the machine's own users are the audience."))

    if settings.cors_allow_origins.strip() == "*":
        out.append(_check("cors", "posture", "Cross-origin access", _secure_or(FAIL),
                          "CORS_ALLOW_ORIGINS=* lets any web page the user visits read this API.",
                          "List the origins that need access, or leave it empty."))
    else:
        out.append(_check("cors", "posture", "Cross-origin access", PASS,
                          "Only listed origins" if settings.cors_allow_origins.strip() else "No cross-origin access."))

    if _reachable_beyond_host() and not (settings.ssl_certfile and settings.ssl_keyfile):
        out.append(_check("tls", "posture", "Transport encryption", WARN,
                          "Not serving TLS itself. Fine behind a TLS-terminating proxy; otherwise traffic and the "
                          "password cross the network in clear text.",
                          "Set SSL_CERTFILE/SSL_KEYFILE, or terminate TLS at a reverse proxy."))
    else:
        out.append(_check("tls", "posture", "Transport encryption", PASS,
                          "TLS configured." if settings.ssl_certfile else "Loopback only; no network exposure."))

    out.append(_check(
        "audit", "posture", "Agent access audit",
        PASS if settings.mcp_audit_tool_calls else WARN,
        "Every MCP tool call is recorded." if settings.mcp_audit_tool_calls
        else "MCP reads are not individually audited (writes always are).",
        "" if settings.mcp_audit_tool_calls else "Set MCP_AUDIT_TOOL_CALLS=true.",
    ))
    return out


def secure_profile_errors() -> List[str]:
    """Fatal problems under ``CLIO_PROFILE=secure`` (empty for any other profile).

    The startup guard raises with these, so a hardened deployment cannot come
    up in a weaker shape than it was declared to have.
    """
    if not is_secure_profile():
        return []
    problems = [c["detail"] + (f" {c['fix']}" if c["fix"] else "")
                for c in _posture_checks() if c["status"] == FAIL]
    if settings.embedding_provider not in ("local", "ollama", "openai_compatible"):
        problems.append(
            f"EMBEDDING_PROVIDER={settings.embedding_provider} sends your documents to a hosted service. "
            "Use local, ollama or openai_compatible (your own endpoint)."
        )
    if settings.ai_provider and settings.ai_provider in CLOUD_AI_PROVIDERS and settings.ai_provider != "openai_compatible":
        problems.append(
            f"AI_PROVIDER={settings.ai_provider} is a hosted service. Use ollama or openai_compatible "
            "(your own endpoint)."
        )
    return problems


# ── Models and capabilities ──────────────────────────────────────────────

def _embedding_check() -> Dict[str, Any]:
    from services import embedder

    provider = settings.embedding_provider
    if provider != "local":
        from services.embedding_providers import PROVIDER_IDS  # noqa: F401 - validated at settings load
        hosted = provider not in ("ollama", "openai_compatible")
        if hosted:
            return _check("embedding", "models", "Embedding model",
                          FAIL if (settings.offline_mode or is_secure_profile()) else WARN,
                          f"Provider '{provider}' is a hosted service: chunk text and queries leave the machine.",
                          "Use EMBEDDING_PROVIDER=local (default) or your own endpoint.")
        return _check("embedding", "models", "Embedding model", PASS,
                      f"{provider} at your own endpoint ({settings.embedding_base_url or settings.ollama_base_url}).")

    model = settings.embedding_model
    try:
        backend = embedder.resolve_local_backend(model)
    except RuntimeError as e:
        return _check("embedding", "models", "Embedding model", FAIL, str(e),
                      "Install fastembed (default) or the PyTorch extras, or use the prebuilt image.")
    if embedder.local_model_cached(model, backend):
        return _check("embedding", "models", "Embedding model", PASS,
                      f"{model} is in the local cache ({backend}); nothing is downloaded at run time.")
    return _check("embedding", "models", "Embedding model", FAIL if embedder.hub_offline() else WARN,
                  f"{model} is not in the local cache" + ("; offline mode cannot download it." if embedder.hub_offline()
                                                          else "; it will download on first use."),
                  "Build the image with OFFLINE_BUNDLE=1, or run scripts/prefetch_offline_models.py on a connected "
                  "machine and carry the cache across.")


def _reranker_check() -> Dict[str, Any]:
    from services import reranker

    if settings.enable_reranker is False:
        return _check("reranker", "models", "Result reranker", PASS, "Disabled by ENABLE_RERANKER=false.")
    state = reranker.status()
    if state["active"] and state["cached"]:
        return _check("reranker", "models", "Result reranker", PASS,
                      f"{settings.reranker_model} is on disk ({state['backend']}) and active.")
    if state["backend"] is None:
        return _check("reranker", "models", "Result reranker", WARN,
                      f"No backend can serve {settings.reranker_model}: neither fastembed nor "
                      "sentence-transformers is installed. Search works; ordering of the best results "
                      "is slightly less accurate.",
                      "pip install fastembed (part of requirements.txt).")
    return _check("reranker", "models", "Result reranker", WARN,
                  f"{settings.reranker_model} is not on disk for {state['backend']}. "
                  "Search works; ordering of the best results is slightly less accurate.",
                  "Build the image with BAKE_EMBEDDING=1 (the default) or OFFLINE_BUNDLE=1, or run "
                  "scripts/prefetch_offline_models.py on a connected machine and carry the cache across.")


def _capability_checks() -> List[Dict[str, Any]]:
    from importlib.util import find_spec

    out = []
    tesseract = shutil.which("tesseract") is not None
    docling = find_spec("docling") is not None
    out.append(_check(
        "ocr", "models", "Scanned-document OCR", PASS if (tesseract or docling) else WARN,
        "Local OCR available (" + ", ".join(n for n, ok in (("Docling", docling), ("Tesseract", tesseract)) if ok) + ")."
        if (tesseract or docling) else "No local OCR engine: scanned PDFs and images index without text.",
        "" if (tesseract or docling) else "Install tesseract or use the full image.",
    ))
    whisper = find_spec("faster_whisper") is not None
    out.append(_check(
        "audio", "models", "Audio transcription", PASS if whisper else WARN,
        "faster-whisper installed (local transcription)." if whisper else "Audio files cannot be transcribed.",
        "" if whisper else "Install requirements-audio.txt or use the full image.",
    ))
    from services import git_source
    git_ok = git_source.git_available()
    out.append(_check(
        "git", "models", "Git repositories", PASS if git_ok else WARN,
        "git is installed; local checkouts and allowlisted remotes can be indexed." if git_ok
        else "git is not installed: repositories are indexed as plain folders and remote clones are unavailable.",
        "" if git_ok else "Install git (the image includes it).",
    ))
    return out


def _ai_check(probe: bool) -> Dict[str, Any]:
    provider, base_url = settings.ai_provider, settings.ai_base_url
    if not provider:
        return _check("ai", "models", "Chat model", WARN,
                      "No deployment-wide model is set. Search, indexing and agent access work; chat needs a model "
                      "chosen in the browser.",
                      "Set AI_PROVIDER, AI_BASE_URL and AI_MODEL to your private endpoint.")
    hosted = provider in CLOUD_AI_PROVIDERS and provider != "openai_compatible"
    if hosted:
        return _check("ai", "models", "Chat model", FAIL if (settings.offline_mode or is_secure_profile()) else WARN,
                      f"{provider} is a hosted service: questions and retrieved passages leave the machine.",
                      "Use AI_PROVIDER=ollama or openai_compatible with your own endpoint.")
    target = base_url or (settings.ollama_base_url if provider == "ollama" else "")
    if not probe or not target:
        return _check("ai", "models", "Chat model", PASS,
                      f"{provider}" + (f" at {target}" if target else "") +
                      (f", model {settings.ai_model}" if settings.ai_model else "") +
                      ("" if probe else " (not probed; run with probe to test the connection)."))
    import httpx

    url = target.rstrip("/") + ("/api/tags" if provider == "ollama" else "/models")
    try:
        r = httpx.get(url, timeout=4.0, headers={"Authorization": f"Bearer {settings.ai_api_key}"} if settings.ai_api_key else None)
        if r.status_code < 400:
            return _check("ai", "models", "Chat model", PASS, f"{target} answered ({provider}).")
        return _check("ai", "models", "Chat model", FAIL, f"{url} returned HTTP {r.status_code}.",
                      "Check AI_BASE_URL and AI_API_KEY.")
    except Exception as e:  # noqa: BLE001
        return _check("ai", "models", "Chat model", FAIL, f"Could not reach {url}: {type(e).__name__}.",
                      "Check AI_BASE_URL, the network route and any private CA (mount it at /certs/ca).")


# ── Storage ──────────────────────────────────────────────────────────────

def _storage_checks() -> List[Dict[str, Any]]:
    out = []
    data_dir = Path(settings.data_dir)
    try:
        probe_file = data_dir / ".readiness-write-test"
        probe_file.write_text("ok")
        probe_file.unlink()
        writable = True
    except OSError as e:
        writable = False
        err = str(e)
    if not writable:
        out.append(_check("data_dir", "storage", "Data directory", FAIL, f"{data_dir} is not writable: {err}",
                          "Fix the volume permissions for the user the container runs as."))
    else:
        free = shutil.disk_usage(data_dir).free
        status = FAIL if free < _LOW_DISK_FAIL else WARN if free < _LOW_DISK_WARN else PASS
        out.append(_check("data_dir", "storage", "Data directory", status,
                          f"{data_dir} is writable; {free / 1024 ** 3:.1f} GiB free.",
                          "" if status == PASS else "Free disk space or move DATA_DIR to a larger volume."))
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        conn.close()
        out.append(_check("fts5", "storage", "Keyword index engine", PASS, "SQLite FTS5 is available."))
    except sqlite3.Error:
        out.append(_check("fts5", "storage", "Keyword index engine", FAIL,
                          "This SQLite build lacks FTS5, which keyword search requires.",
                          "Use a standard CPython build or the provided image."))
    return out


# ── Egress ───────────────────────────────────────────────────────────────

def egress_paths() -> List[Dict[str, str]]:
    """Every outbound path the live configuration permits, with where it goes."""
    offline = settings.offline_mode
    paths: List[Dict[str, str]] = []

    def add(kind: str, destination: str, state: str, note: str = "") -> None:
        paths.append({"kind": kind, "destination": destination, "state": state, "note": note})

    # Embeddings
    if settings.embedding_provider == "local":
        add("Embeddings", "in-process", "none", "Chunks and queries never leave the machine.")
    elif settings.embedding_provider in ("ollama", "openai_compatible"):
        add("Embeddings", settings.embedding_base_url or settings.ollama_base_url, "internal", "Your own endpoint.")
    else:
        add("Embeddings", f"hosted ({settings.embedding_provider})", "blocked" if offline else "open",
            "Chunk text and queries are sent out.")

    # Chat model
    if not settings.ai_provider:
        add("Chat model", "chosen per browser", "blocked" if offline else "open",
            "Cloud providers are refused in offline mode; local ones go to the URL entered.")
    elif settings.ai_provider in ("ollama", "openai_compatible"):
        add("Chat model", settings.ai_base_url or settings.ollama_base_url, "internal", "Your own endpoint.")
    else:
        add("Chat model", f"hosted ({settings.ai_provider})", "blocked" if offline else "open",
            "Questions and retrieved passages are sent out.")

    # Web links
    from services.link_fetcher import link_indexing_available
    links_on = link_indexing_available()
    add("Web links", "any public web host" if links_on else "disabled", "open" if links_on else "blocked",
        "Fetched by the server when someone adds a link." if links_on else "The server never fetches web pages.")

    # Scholarly registries (research workspace metadata and references)
    from services.research.lookup import lookup_available
    lookups_on = lookup_available()
    add("Scholarly registries", "api.openalex.org, api.crossref.org, export.arxiv.org, openlibrary.org" if lookups_on else "disabled",
        "open" if lookups_on else "blocked",
        "Only identifiers (DOI, arXiv id, ISBN) and cited titles are sent, never passages."
        if lookups_on else "Paper metadata comes from the paper's own text only.")

    # Git
    allowed = [h.strip() for h in settings.git_allowed_hosts.split(",") if h.strip()]
    if allowed:
        add("Git remotes", ", ".join(allowed), "internal", "Only these hosts may be cloned.")
    elif offline:
        add("Git remotes", "disabled", "blocked", "Remote clones are refused in offline mode.")
    else:
        add("Git remotes", "any public git host", "open", "Private networks still need GIT_ALLOWED_HOSTS.")

    # Alerts
    if settings.alert_webhook_url:
        host = urlparse(settings.alert_webhook_url).hostname or settings.alert_webhook_url
        add("Change alerts", host, "internal", "Change summaries are POSTed here (operator-configured).")

    # Vision OCR / content review with a model
    if settings.vision_ocr_provider not in ("", "none"):
        local = settings.vision_ocr_provider == "ollama"
        add("Image OCR", settings.vision_ocr_provider, "internal" if local else ("blocked" if offline else "open"),
            "Page images are sent to the model." )
    if settings.content_policy_llm_review and settings.content_policy_llm_provider:
        add("Content review", settings.content_policy_llm_provider, "blocked" if offline and
            settings.content_policy_llm_provider not in ("ollama", "openai_compatible") else "internal",
            "Sampled pages are sent for review.")

    add("Model downloads", "huggingface.co", "blocked" if offline else "open",
        "Models load from the local cache only." if offline else "Models download on first use if missing.")
    add("Telemetry / analytics", "none", "none", "The server and the frontend make no analytics calls.")
    return paths


# ── Report ───────────────────────────────────────────────────────────────

def run_checks(probe: bool = False) -> Dict[str, Any]:
    """The full readiness report. Never raises: a check that errors is a failed check."""
    checks: List[Dict[str, Any]] = []
    builders = [
        ("posture", _posture_checks),
        ("embedding", lambda: [_embedding_check()]),
        ("reranker", lambda: [_reranker_check()]),
        ("capabilities", _capability_checks),
        ("ai", lambda: [_ai_check(probe)]),
        ("storage", _storage_checks),
    ]
    for name, build in builders:
        try:
            checks.extend(build())
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Readiness check '{name}' errored: {e}")
            checks.append(_check(name, "errors", f"{name} check", FAIL, f"The check itself failed: {e}"))

    try:
        egress = egress_paths()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Readiness egress listing failed: {e}")
        egress = []

    counts = {s: sum(1 for c in checks if c["status"] == s) for s in (PASS, WARN, FAIL)}
    return {
        "ready": counts[FAIL] == 0,
        "profile": settings.clio_profile,
        "offline_mode": settings.offline_mode,
        "counts": counts,
        "checks": checks,
        "egress": egress,
        "probed": probe,
    }
