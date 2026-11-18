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


_KEYWORDS_ONLY = ("Search uses keywords only. New sources still index, and get search by meaning by "
                  "themselves once a model is there.")
_OTHER_PROVIDERS = "or choose Ollama or your own embedding server in Settings"


def _in_image() -> bool:
    """Running from the published image (it sets CLIO_VERSION), not a checkout."""
    import os
    return bool(os.environ.get("CLIO_VERSION", "").strip())


def _download_failed(error: str) -> bool:
    text = (error or "").lower()
    return any(w in text for w in ("timed out", "timeout", "connect", "could not download", "resolve",
                                   "network", "unreachable", "huggingface", "hub", "ssl", "proxy"))


def _semantic() -> Dict[str, Any]:
    """B7: what failed and the fix that matches it, from the status and the
    provider. Names the public model hub when it is the source; never a
    mirror's or an embedding server's address, which are internal."""
    import os

    from services.embedding_providers import get_provider
    from services.embedding_status import embedding_status

    status = embedding_status.status()
    state = status.get("status")
    provider = status.get("provider") or settings.embedding_provider or "local"
    label = "Semantic search"
    if state == "ready":
        return _feature("semantic", label, ON, "Search finds passages by meaning as well as by keyword.")
    if state in ("loading", "downloading"):
        return _feature("semantic", label, LIMITED, "The search model is being prepared. Search uses keywords until then.")

    def limited(what: str, fix: str) -> Dict[str, Any]:
        return _feature("semantic", label, LIMITED, f"{what} {_KEYWORDS_ONLY}", fix)

    if state == "unconfigured":
        return limited("No search model has been chosen yet.",
                       "Choose where the search index runs in Settings, Indexing.")
    if provider != "local":
        name = {"ollama": "Ollama", "openai_compatible": "Your embedding server"}.get(
            provider, (get_provider(provider) or {}).get("label") or provider)
        if provider == "ollama":
            fix = "Check that Ollama is running and has the model, then use Check again in Settings, Indexing."
        elif provider == "openai_compatible":
            fix = ("Check that the embedding server is running and that this server can reach it, then use "
                   "Check again in Settings, Indexing.")
        else:
            fix = "Check the provider's key and that this server can reach it, in Settings, Indexing."
        return limited(f"{name} did not answer when the search model was tried.", fix)

    error = status.get("error") or ""
    mirror = bool(os.environ.get("HF_ENDPOINT", "").strip())
    if state == "missing":
        what = "The search model is not on this server, and this server is set not to download models."
        fix = (f"Use an image that includes the model, or pre-seed it into the data folder, {_OTHER_PROVIDERS}."
               if _in_image() else
               f"Copy the model into the data folder's models folder from a machine that has it, {_OTHER_PROVIDERS}.")
        return limited(what, fix)
    if "backend" in error.lower() and "install" in error.lower():
        return limited("No local runtime for search models is installed on this server.",
                       f"Install fastembed on this server, {_OTHER_PROVIDERS}.")
    if _download_failed(error):
        if mirror:
            return limited("The search model could not be downloaded from the download mirror this server uses.",
                           f"Check that the mirror is reachable from this server, pre-seed the model, {_OTHER_PROVIDERS}.")
        return limited("The search model could not be downloaded: this server cannot reach huggingface.co.",
                       f"Pre-seed the model, point the server at a download mirror your network allows, "
                       f"{_OTHER_PROVIDERS}.")
    return limited("The search model could not be loaded.",
                   f"See the server log for the reason, then retry from Settings, Indexing, {_OTHER_PROVIDERS}.")


def _reranker() -> Dict[str, Any]:
    from services import reranker

    label = "Reranker"
    state = reranker.status()
    if state["active"]:
        return _feature("reranker", label, ON, "The best results are put in a more accurate order.")
    if state["setting"] == "off":
        return _feature("reranker", label, OFF, "Turned off. Results come back in retrieval order.",
                        "Turn it on in Settings, Features.")
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
    if not settings.enable_transcription:
        return _feature("transcription", label, OFF, "Turned off, so audio and video recordings are refused.",
                        "Turn it on in Settings, Features.")
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
                        "Turn it on in Settings, Features.")
    if not _vision_usable():
        # B6: no vision model is a choice, not a fault; the pictures work.
        return _feature("figures", label, ON,
                        "Pictures are kept and shown beside results. Choose a vision model in Settings to have "
                        "each one described, so they can also be found by what they show.")
    return _feature("figures", label, ON,
                    f"Pictures are kept, and described by a vision model ({_vision_provider()}) so they can be "
                    "found. The connection is tested when a picture is read.")


def _ai() -> Dict[str, Any]:
    label = "AI answers"
    provider = (settings.ai_provider or "").strip()
    if not provider:
        return _feature("ai", label, OFF, "No model is set for this server. Search does not need one.",
                        "Set this server's model in Settings, Features, or connect one in this browser "
                        "under AI Providers.")
    if _uses_blocked_provider(provider):
        return _feature("ai", label, UNAVAILABLE, f"Set to {provider}, which offline mode does not allow.",
                        "Use local Ollama or an OpenAI-compatible server on your network.")
    return _feature("ai", label, ON, f"This server answers with {settings.ai_provider_label or provider}. "
                                     "The connection is tested when a question is asked.")


def _research() -> Dict[str, Any]:
    from services.research.lookup import lookup_available

    # The id stays "research" (saved settings and the Features form key on
    # it); what it controls, and what people read, is catalogue lookups.
    label = "Catalogue lookups"
    if lookup_available():
        return _feature("research", label, ON, "Library papers get their citation details and references "
                                               "looked up in public catalogues. Those are tested when used.")
    return _feature("research", label, LIMITED,
                    "Library papers are organised and checked from their own text. Catalogue lookups are off"
                    + (" in offline mode." if settings.offline_mode else "."),
                    "" if settings.offline_mode else "Turn catalogue lookups on in Settings, Features.")


def _links() -> Dict[str, Any]:
    from services.link_fetcher import link_indexing_available

    label = "Web links"
    if link_indexing_available():
        return _feature("links", label, ON, "Pages can be added by link. Each site is tested when it is fetched.")
    if settings.offline_mode:
        return _feature("links", label, OFF, "Offline mode does not fetch web pages.")
    return _feature("links", label, OFF, "Turned off, so pages cannot be added by link.",
                    "Turn it on in Settings, Features.")


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
                        "Add your internal git server under Settings, Features, or mount a checkout and set "
                        "LOCAL_SOURCE_ROOTS to add it by path.")
    return _feature("git", label, ON, "Repositories can be indexed and kept in sync. Remote hosts are tested "
                                      "when cloned.")


def _mcp() -> Dict[str, Any]:
    label = "MCP"
    if settings.enable_mcp:
        return _feature("mcp", label, ON, "Agents can search this server through its MCP endpoint.")
    return _feature("mcp", label, OFF, "Turned off, so agents cannot connect.",
                    "Turn it on in Settings, Features.")


def _pii() -> Dict[str, Any]:
    from services.pii_detector import presidio_installed

    # The id stays "pii" so the status bar and saved layouts keep finding it.
    label = "Trust no one"
    if not settings.trust_no_one:
        return _feature("pii", label, OFF,
                        "Off: sources are trusted, so none is checked for instructions aimed at an AI "
                        "model or for personal data.",
                        "Turn on Trust no one in Settings, Features.")
    if settings.pii_scan_action == "off":
        return _feature("pii", label, LIMITED,
                        "Sources are checked for instructions aimed at an AI model, but not for "
                        "personal data.",
                        "Choose what the personal data scan does in Settings, Features.")
    if presidio_installed():
        return _feature("pii", label, ON,
                        "Sources are checked for instructions aimed at an AI model and, with Presidio, "
                        "for personal data.")
    return _feature("pii", label, LIMITED,
                    "Sources are checked for instructions aimed at an AI model, and for personal data "
                    "with patterns only, which find things like emails and phone numbers but not names.",
                    "Build the image with WITH_PII=1 for full detection.")


def _pra() -> Dict[str, Any]:
    label = "PRA models"
    if not settings.enable_pra:
        return _feature("pra", label, OFF,
                        "Off. SAPHIRE and Open-PSA files are indexed as plain text, with no model viewer.",
                        "Turn it on in Settings, Features, then index those files again.")
    return _feature("pra", label, ON,
                    "SAPHIRE and Open-PSA models are read as fault trees, event trees and cut sets.")


_CHECKS = (_semantic, _reranker, _ocr, _transcription, _figures, _ai, _research, _links, _git, _mcp, _pii, _pra)


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
