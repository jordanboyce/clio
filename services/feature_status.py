"""Which of Clio's capabilities work right now, for the footer of the app.

Answers "will this work if I use it now?", which is not the same as "is it
installed": Tesseract in the image does not help while OCR is switched off.

Reads settings and the local disk only. It never contacts anything, so it costs
nothing to poll and sends nothing out of a locked-down network. Where a feature
depends on something remote, the detail says it is only tested when used.

Anyone signed in can read this, so it names no paths, addresses or keys.
Administrators get those from the readiness report.
"""

from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List

from config import CLOUD_AI_PROVIDERS, settings

ON = "on"                    # works now
LIMITED = "limited"          # works in a reduced form
OFF = "off"                  # switched off, not connected, or not in this install
UNAVAILABLE = "unavailable"  # wanted, but cannot work here

ENGINE_NAMES = {"docling": "Docling", "pytesseract": "Tesseract"}


def _feature(id: str, label: str, state: str, detail: str, fix: str = "") -> Dict[str, Any]:
    return {"id": id, "label": label, "state": state, "detail": detail, "fix": fix}


def _uses_blocked_provider(provider: str) -> bool:
    return settings.offline_mode and provider in CLOUD_AI_PROVIDERS and provider != "openai_compatible"


def _vision_provider() -> str:
    return (settings.vision_ocr_provider or "none").strip().lower()


def _vision_usable() -> bool:
    """A vision model is chosen, has its key, and offline mode allows it."""
    from services.document_extractor import DocumentExtractor

    provider = _vision_provider()
    if provider == "none" or _uses_blocked_provider(provider):
        return False
    return DocumentExtractor(
        vision_ocr_provider=provider, vision_ocr_api_key=settings.vision_ocr_api_key,
    ).vision_ocr_usable()


def _semantic() -> Dict[str, Any]:
    from services.embedding_status import embedding_status

    status = embedding_status.status()
    state = status.get("status")
    label = "Semantic search"
    if state == "ready":
        return _feature("semantic", label, ON, "Search finds passages by meaning as well as by keyword.")
    if state in ("loading", "downloading"):
        return _feature("semantic", label, LIMITED, "The search model is being prepared. Search uses keywords until then.")
    return _feature(
        "semantic", label, LIMITED,
        "No search model is available, so search uses keywords only. New sources still index, and "
        "get search by meaning by themselves once a model is there.",
        "Use the full or slim image, which include a model, pre-seed one, or choose your own "
        "embedding server in Settings.",
    )


def _reranker() -> Dict[str, Any]:
    from services import reranker

    label = "Reranker"
    state = reranker.status()
    if state["active"]:
        return _feature("reranker", label, ON, "The best results are put in a more accurate order.")
    if state["setting"] == "off":
        return _feature("reranker", label, OFF, "Turned off. Results come back in retrieval order.",
                        "Remove ENABLE_RERANKER=false.")
    return _feature("reranker", label, LIMITED,
                    "The model is not on this server. Results come back in retrieval order.",
                    "Use the full or slim image, which include it, or pre-seed it.")


def _ocr() -> Dict[str, Any]:
    from services.ocr_engine import get_available_engines

    label = "OCR"
    if not settings.enable_ocr:
        return _feature("ocr", label, OFF,
                        "Turned off. A PDF that is only scanned pages is refused, and scanned pages in "
                        "other PDFs are skipped.",
                        "Turn OCR on in Settings. The slim image has no OCR engine, so it also needs the "
                        "full image or a vision model.")
    if _vision_usable():
        return _feature("ocr", label, ON, f"Scanned pages are read by a vision model ({_vision_provider()}). "
                                          "The connection is tested when a scan is read.")
    engines = [ENGINE_NAMES.get(name, name) for name in get_available_engines()]
    if engines:
        return _feature("ocr", label, ON, f"Scanned pages are read locally with {' and '.join(engines)}.")
    return _feature("ocr", label, UNAVAILABLE,
                    "Turned on, but no OCR engine can run. A PDF that is only scanned pages is refused.",
                    "Use the full image, install Tesseract and Poppler, or choose a vision model in Settings.")


def _transcription() -> Dict[str, Any]:
    from services import audio_transcriber, embedder

    label = "Transcription"
    if find_spec("faster_whisper") is None:
        return _feature("transcription", label, OFF,
                        "Not in this install, so audio and video recordings are refused.",
                        "Use the full image, or install requirements-audio.txt.")
    # WHISPER_MODEL may be a folder, and this report names no paths.
    model = Path(settings.whisper_model).name
    if audio_transcriber.model_on_disk(settings.whisper_model):
        return _feature("transcription", label, ON,
                        f"Speech in audio and video recordings is transcribed locally (Whisper {model}). "
                        "What appears on screen is not read.")
    if embedder.hub_offline():
        return _feature("transcription", label, UNAVAILABLE,
                        f"The Whisper model '{model}' is not on this server and offline mode cannot download "
                        "it, so recordings are refused.",
                        "Use the full image, which includes 'base', or pre-seed the model.")
    return _feature("transcription", label, LIMITED,
                    f"The Whisper model '{model}' downloads on the first recording. If this network "
                    "blocks that, recordings are refused.",
                    "Use the full image, which includes 'base', or pre-seed the model.")


def _figures() -> Dict[str, Any]:
    label = "Figures"
    if not settings.extract_figures:
        return _feature("figures", label, OFF, "Turned off. Only the text of a source is indexed.",
                        "Remove EXTRACT_FIGURES=false.")
    if not _vision_usable():
        return _feature("figures", label, LIMITED,
                        "Pictures are kept and shown beside results, but cannot be found by what they show.",
                        "Choose a vision model in Settings to have each one described.")
    return _feature("figures", label, ON,
                    f"Pictures are kept, and described by a vision model ({_vision_provider()}) so they can be "
                    "found. The connection is tested when a picture is read.")


def _ai() -> Dict[str, Any]:
    label = "AI answers"
    provider = (settings.ai_provider or "").strip()
    if not provider:
        return _feature("ai", label, OFF, "No model is set for this server. Search does not need one.",
                        "Connect a model on your network, or set AI_PROVIDER, AI_BASE_URL and AI_MODEL.")
    if _uses_blocked_provider(provider):
        return _feature("ai", label, UNAVAILABLE, f"Set to {provider}, which offline mode does not allow.",
                        "Use local Ollama or an OpenAI-compatible server on your network.")
    return _feature("ai", label, ON, f"This server answers with {settings.ai_provider_label or provider}. "
                                     "The connection is tested when a question is asked.")


def _research() -> Dict[str, Any]:
    from services.research.lookup import lookup_available

    label = "Research"
    if lookup_available():
        return _feature("research", label, ON, "Papers are organised and checked, with citations looked up in "
                                               "public catalogues. Those are tested when used.")
    return _feature("research", label, LIMITED,
                    "Papers are organised and checked from their own text. Catalogue lookups are off "
                    + ("in offline mode." if settings.offline_mode else "(RESEARCH_LOOKUP_ENABLED=false)."))


def _links() -> Dict[str, Any]:
    from services.link_fetcher import link_indexing_available

    label = "Web links"
    if link_indexing_available():
        return _feature("links", label, ON, "Pages can be added by link. Each site is tested when it is fetched.")
    return _feature("links", label, OFF,
                    "Offline mode does not fetch web pages." if settings.offline_mode
                    else "Turned off by LINK_INDEXING_ENABLED=false.")


def _git() -> Dict[str, Any]:
    from services import git_source

    label = "Git"
    if not git_source.git_available():
        return _feature("git", label, LIMITED, "git is not installed. Repositories index as plain folders, "
                                               "and remote ones cannot be cloned.",
                        "Install git. The images include it.")
    if settings.offline_mode and not settings.git_allowed_hosts.strip():
        return _feature("git", label, LIMITED, "Local checkouts index. Remote repositories need an allowed host "
                                               "in offline mode.",
                        "Set GIT_ALLOWED_HOSTS to your internal git server, or mount a checkout and set "
                        "LOCAL_SOURCE_ROOTS to add it by path.")
    return _feature("git", label, ON, "Repositories can be indexed and kept in sync. Remote hosts are tested "
                                      "when cloned.")


def _mcp() -> Dict[str, Any]:
    label = "MCP"
    if settings.enable_mcp:
        return _feature("mcp", label, ON, "Agents can search this server through its MCP endpoint.")
    return _feature("mcp", label, OFF, "Turned off, so agents cannot connect.", "Remove ENABLE_MCP=false.")


def _pii() -> Dict[str, Any]:
    from services.pii_detector import presidio_installed

    label = "PII scan"
    if settings.pii_scan_action == "off":
        return _feature("pii", label, OFF, "Turned off. Sources are not scanned for personal data.",
                        "Set PII_SCAN_ACTION.")
    if presidio_installed():
        return _feature("pii", label, ON, "Sources are scanned for personal data with Presidio.")
    return _feature("pii", label, LIMITED,
                    "Sources are scanned with patterns only, which find things like emails and phone "
                    "numbers but not names.",
                    "Build the image with WITH_PII=1 for full detection.")


_CHECKS = (_semantic, _reranker, _ocr, _transcription, _figures, _ai, _research, _links, _git, _mcp, _pii)


def feature_status() -> List[Dict[str, Any]]:
    """Every feature's state. One that cannot be checked says so, and the rest still report."""
    out = []
    for check in _CHECKS:
        try:
            out.append(check())
        except Exception as e:  # noqa: BLE001 - the footer must not fail over one check
            name = check.__name__.lstrip("_")
            out.append(_feature(name, name.capitalize(), UNAVAILABLE, f"Could not be checked ({type(e).__name__})."))
    return out
