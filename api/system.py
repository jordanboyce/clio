"""System, environment, and configuration endpoints."""

import logging
from typing import List, Optional
from pathlib import Path

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from config import settings
from services.ai_service import detect_ollama
from services.config_manager import config_manager
from services.indexer_manager import indexer_manager
from services.embedder import embedding_availability
from api.deps import require_admin

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health", tags=["health"])
async def health(collection_id: str = "default"):
    """Health check endpoint."""
    try:
        stats = indexer_manager.get_collection_stats(collection_id)
        return {
            "status": "healthy",
            "offline_mode": settings.offline_mode,
            "collection_id": collection_id,
            "indexed_chunks": stats["total_chunks"],
            "total_documents": stats["total_documents"],
            "total_pages": stats["total_pages"],
            "embedding": embedding_availability(),
        }
    except Exception as e:
        return {
            "status": "healthy",
            "offline_mode": settings.offline_mode,
            "indexed_chunks": 0,
            "error": str(e),
            "embedding": embedding_availability(),
        }

@router.post(
    "/api/file-picker",
    summary="Open native file picker dialog",
    tags=["local"],
)
def open_file_picker(multiple: bool = True, include_sizes: bool = False):
    # sync: the tkinter dialog blocks until dismissed — in the threadpool that
    # stalls one worker, not the whole server (UI + MCP kept freezing before)
    """
    Open a native OS file picker dialog.

    Only meaningful for a local install where the server runs on the user's
    own machine — the dialog opens on the *server's* display. Headless and
    Docker deployments report native_file_picker=false via /api/capabilities
    and the frontend falls back to browser upload.

    Args:
        multiple: If True, allow selecting multiple files (default: True)
        include_sizes: If True, include file sizes in response (default: False)

    Returns:
        {"paths": ["C:/path/to/file1.pdf", ...], "sizes": {"C:/path/to/file1.pdf": 12345, ...}}
    """
    # The dialog opens on the server's own display and reveals server paths;
    # under private collections that is an operator action, not a user one.
    require_admin("browse the server filesystem")
    from services.file_picker import open_file_dialog
    import os

    try:
        paths = open_file_dialog(multiple=multiple)
        result = {"paths": paths}

        if include_sizes and paths:
            sizes = {}
            for path in paths:
                try:
                    sizes[path] = os.path.getsize(path)
                except OSError:
                    sizes[path] = 0
            result["sizes"] = sizes

        return result
    except Exception as e:
        logger.error(f"File picker error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to open file picker: {str(e)}",
        )


@router.post(
    "/api/folder-picker",
    summary="Open native folder picker dialog",
    tags=["local"],
)
def open_folder_picker_endpoint():  # sync: see open_file_picker
    """
    Open a native OS folder picker dialog.

    Local-install only — see open_file_picker.

    Returns:
        {"path": "C:/path/to/folder"} or {"path": null} if cancelled
    """
    require_admin("browse the server filesystem")
    from services.file_picker import open_folder_dialog

    try:
        path = open_folder_dialog()
        return {"path": path}
    except Exception as e:
        logger.error(f"Folder picker error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to open folder picker: {str(e)}",
        )


@router.get(
    "/api/capabilities",
    summary="Query server capabilities for adaptive UI",
    tags=["system"],
)
async def get_capabilities():
    """
    Returns server-side capability flags so the frontend can adapt.

    native_file_picker: False in headless/Docker environments where
    tkinter cannot open a display; the frontend should fall back to
    browser-native <input type="file"> upload in that case.

    ocr_available / audio_available / postgres_available: whether the
    corresponding optional dependency set (requirements-ocr.txt,
    requirements-audio.txt, requirements-postgres.txt) is installed, so the
    UI can hide or annotate features that need an extra install.
    """
    from importlib.util import find_spec
    from services.file_picker import is_native_picker_available
    from services.link_fetcher import link_indexing_available
    return {
        "native_file_picker": is_native_picker_available(),
        "ocr_available": find_spec("docling") is not None or find_spec("pytesseract") is not None,
        "audio_available": find_spec("faster_whisper") is not None,
        "postgres_available": find_spec("psycopg2") is not None,
        # Off under OFFLINE_MODE or LINK_INDEXING_ENABLED=false; the Sources
        # panel hides "Link" rather than offering a button that always fails.
        "link_indexing": link_indexing_available(),
    }


@router.get(
    "/api/about",
    summary="Version and deployment facts for the About dialog",
    tags=["system"],
)
async def get_about():
    """What the About dialog shows: the running version (the image's
    CLIO_VERSION, or the git describe of a source checkout, or "dev"), the
    embedding model in use, and whether MCP and offline mode are on. No
    secrets, no paths — it is readable by anyone who can open the app."""
    import os
    import subprocess
    version = os.environ.get("CLIO_VERSION", "").strip()
    if not version:
        try:
            version = subprocess.run(
                ["git", "describe", "--tags", "--always", "--dirty"],
                capture_output=True, text=True, timeout=2,
                cwd=str(Path(__file__).resolve().parent.parent),
            ).stdout.strip() or "dev"
        except Exception:
            version = "dev"
    return {
        "version": version,
        "embedding_model": settings.embedding_model,
        "mcp_enabled": settings.enable_mcp,
        "offline_mode": settings.offline_mode,
    }


class ScanFolderRequest(BaseModel):
    """Request body for scanning a folder."""
    path: str
    recursive: bool = True
    file_extensions: Optional[List[str]] = None


@router.post(
    "/api/scan-folder",
    summary="Scan folder and return list of supported files",
    tags=["local"],
)
def scan_folder(request: ScanFolderRequest):  # sync: filesystem walk runs in the threadpool
    """
    Scan a folder and return the list of supported files found.

    This is a lightweight operation that just lists files - no indexing.
    Use this to preview what files will be indexed before starting.
    """
    # Takes an arbitrary server path and reports what exists there — a
    # path-probing oracle under private collections, so operator-only.
    require_admin("browse the server filesystem")
    import os

    folder_path = Path(request.path)

    if not folder_path.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path does not exist: {request.path}",
        )

    if not folder_path.is_dir():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Path is not a directory: {request.path}",
        )

    # Default exclude patterns
    default_excludes = [
        'node_modules', '.git', '__pycache__', 'venv', '.venv',
        'dist', 'build', '.idea', '.vscode', 'target', 'bin', 'obj'
    ]

    # Determine which extensions to look for
    from services.document_extractor import is_supported_filename
    extensions_filter = set(e.lower() for e in request.file_extensions) if request.file_extensions else None

    def wanted(path: Path) -> bool:
        if extensions_filter is not None:
            return path.suffix.lower() in extensions_filter
        return is_supported_filename(path.name)

    files_found = []

    def should_skip_dir(dirname: str) -> bool:
        return dirname in default_excludes or dirname.startswith('.')

    if request.recursive:
        for root, dirs, files in os.walk(folder_path):
            # Filter out excluded directories
            dirs[:] = [d for d in dirs if not should_skip_dir(d)]

            for filename in files:
                file_path = Path(root) / filename
                if wanted(file_path):
                    files_found.append({
                        "path": str(file_path),
                        "name": filename,
                        "relative_path": str(file_path.relative_to(folder_path)),
                        "size": file_path.stat().st_size if file_path.exists() else 0
                    })
    else:
        for file_path in folder_path.iterdir():
            if file_path.is_file() and wanted(file_path):
                files_found.append({
                    "path": str(file_path),
                    "name": file_path.name,
                    "relative_path": file_path.name,
                    "size": file_path.stat().st_size if file_path.exists() else 0
                })

    # Sort by relative path for consistent display
    files_found.sort(key=lambda f: f["relative_path"])

    return {
        "folder": request.path,
        "files": files_found,
        "total": len(files_found)
    }

@router.get(
    "/api/config",
    summary="Get current configuration",
    tags=["config"],
)
async def get_config():
    """
    Get current configuration settings.

    Returns all configurable settings including embedding model,
    chunking parameters, and storage type.
    """
    return config_manager.get_current_config()


@router.post(
    "/api/config",
    summary="Update configuration",
    tags=["config"],
)
async def update_config(updates: dict):
    """
    Update configuration settings. Admin only under private collections.

    These settings apply to the whole deployment — the embedding model here
    decides whether every existing index still matches, and the provider
    keys are shared by everyone. Under private collections the app admits
    people who are only meant to read one shared collection, so this is
    gated on ADMIN_EMAILS rather than merely being signed in.

    Args:
        updates: Dictionary of configuration key-value pairs

    Returns:
        Object with success status, whether restart/reindex needed,
        and list of updated fields.

    Note: Some changes (like embedding model) require server restart
    and re-indexing all documents.
    """
    require_admin("change deployment settings")
    result = config_manager.update_config(updates)
    return result



@router.get(
    "/api/ollama/status",
    summary="Check Ollama availability",
    tags=["ai"],
)
async def get_ollama_status():
    """
    Check if Ollama is running and list available models.

    Returns:
        - available: Whether Ollama is accessible
        - models: List of available models with names and sizes
        - error: Error message if detection failed
    """
    return detect_ollama(settings.ollama_base_url)


@router.get(
    "/api/ollama/vision-models",
    summary="List Ollama models that support vision/image input",
    tags=["ai"],
)
async def get_ollama_vision_models():
    """
    Return only Ollama models that support vision by checking each model's
    architecture families via /api/show. Vision models include 'clip' in their
    families list (CLIP is the vision encoder used by all multimodal Ollama models).
    Falls back to name-based detection if /api/show is unavailable.
    """
    import httpx
    import asyncio

    status = detect_ollama(settings.ollama_base_url)
    if not status.get("available"):
        return {"available": False, "models": [], "error": status.get("error")}

    base_url = status.get("base_url", settings.ollama_base_url).rstrip("/")
    all_models = status.get("models", [])

    # Known vision model name patterns as a fallback
    VISION_NAME_PATTERNS = [
        "llava", "vl", "vision", "minicpm-v", "moondream",
        "bakllava", "cogvlm", "internvl", "clip",
    ]

    def name_looks_like_vision(name: str) -> bool:
        name_lower = name.lower()
        return any(p in name_lower for p in VISION_NAME_PATTERNS)

    async def check_model_vision(client: httpx.AsyncClient, model: dict) -> Optional[dict]:
        try:
            resp = await client.post(
                f"{base_url}/api/show",
                json={"name": model["name"]},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                families = data.get("details", {}).get("families") or []
                if "clip" in families or "mllama" in families:
                    return {**model, "vision_detected_by": "families"}
            # Fall back to name heuristic
            if name_looks_like_vision(model["name"]):
                return {**model, "vision_detected_by": "name"}
            return None
        except Exception:
            # If /api/show fails entirely, use name heuristic
            if name_looks_like_vision(model["name"]):
                return {**model, "vision_detected_by": "name"}
            return None

    async def gather_vision_models():
        async with httpx.AsyncClient() as client:
            tasks = [check_model_vision(client, m) for m in all_models]
            results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    vision_models = await gather_vision_models()
    return {
        "available": True,
        "models": vision_models,
        "total_models": len(all_models),
    }


@router.get(
    "/api/me",
    summary="Who is signed in, and how",
    tags=["system"],
)
async def whoami(request: Request):
    """
    Identity and sign-out info for the header menu.

    - Behind Cloudflare Access with CF_ACCESS_* configured, `identity` is the
      SSO email (or service-token name) from the verified Access JWT, and
      `logout_url` is Cloudflare's session-clearing endpoint on this host.
    - Password-authenticated or open deployments report those modes instead;
      browsers cache Basic credentials, so there is no reliable app-side
      sign-out for the password path.
    """
    via = getattr(request.state, "auth_via", None) or ("open" if not settings.auth_password else "password")
    identity = getattr(request.state, "auth_identity", None)
    return {
        "authenticated_via": via,
        "identity": identity,
        "logout_url": "/cdn-cgi/access/logout" if via == "cloudflare-access" else None,
    }


# ── Embedding provider picker ───────────────────────────────────────────────


class EmbeddingTestRequest(BaseModel):
    """Unsaved embedding settings to probe. Field names match /api/config."""

    embedding_provider: str
    embedding_model: Optional[str] = None
    remote_embedding_model: Optional[str] = None
    embedding_base_url: Optional[str] = None
    embedding_api_key: Optional[str] = None
    ollama_base_url: Optional[str] = None


@router.get(
    "/api/embedding/providers",
    summary="Embedding providers the picker can offer",
    tags=["config"],
)
async def list_embedding_providers():
    """
    The embedding catalog with, per provider, whether a usable key is already
    on file (`key_configured`) and where it would come from (`key_source`:
    "embedding" for the embedding-specific key, "provider_card" for the team
    key saved under AI Providers, or null). Hidden entries are included only
    when they are the current selection, so an old config still renders.
    Keys themselves are never returned.
    """
    from services.embedding_providers import EMBEDDING_PROVIDERS, CLOUD_EMBEDDING_PROVIDERS
    from services.embedder import embedding_signature

    current = settings.embedding_provider
    out = []
    for p in EMBEDDING_PROVIDERS:
        if p.get("hidden") and p["id"] != current:
            continue
        key_source = None
        if p["needs_key"] or p["id"] == "openai_compatible":
            if settings.embedding_api_key and p["id"] == current:
                key_source = "embedding"
            elif p["id"] == "ollama_cloud" and settings.ollama_cloud_api_key:
                key_source = "embedding"
            elif p.get("key_provider"):
                try:
                    from services.app_database import app_db
                    if app_db.get_agent_api_key(p["key_provider"]):
                        key_source = "provider_card"
                except Exception:
                    pass
        entry = {k: v for k, v in p.items()}
        entry["key_configured"] = key_source is not None
        entry["key_source"] = key_source
        entry["blocked_offline"] = bool(settings.offline_mode and p["id"] in CLOUD_EMBEDDING_PROVIDERS)
        out.append(entry)

    return {
        "providers": out,
        "current": {
            "provider": current,
            "signature": embedding_signature(),
            "offline_mode": settings.offline_mode,
        },
    }


@router.post(
    "/api/embedding/test",
    summary="Try an embedding configuration before saving it",
    tags=["config"],
)
async def test_embedding_settings(req: EmbeddingTestRequest):
    """
    Builds an embedding service from the submitted (unsaved) values and embeds
    one short probe string. Returns the vector dimension and round-trip time on
    success, or a plain-language reason on failure — the same message indexing
    would fail with later. Admin-only under private collections, like saving.
    """
    import asyncio
    import time

    from services.config_manager import MASKED_SECRET
    from services.embedder import create_embedding_service

    require_admin("test embedding settings")

    overrides = {k: v for k, v in req.model_dump().items() if v is not None}
    # The form echoes the mask for a stored key; that means "use what is saved".
    if overrides.get("embedding_api_key") == MASKED_SECRET:
        overrides.pop("embedding_api_key")

    def probe():
        t0 = time.perf_counter()
        svc = create_embedding_service(overrides=overrides)
        return {
            "ok": True,
            "model": svc.model_name,
            "dimensions": int(svc.embedding_dim),
            "seconds": round(time.perf_counter() - t0, 2),
        }

    try:
        return await asyncio.to_thread(probe)
    except Exception as e:
        return {"ok": False, "error": str(e)}
