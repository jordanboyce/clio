"""Pre-fetch every model Clio can need at runtime, for air-gapped deploys.

Run this on a machine WITH internet access:

  - inside the Docker build:  docker compose build --build-arg OFFLINE_BUNDLE=1
  - or on a connected workstation (venv active):  python scripts/prefetch_offline_models.py
    then copy the caches to the air-gapped host (see docs/AIRGAP.md).

Downloads land in the standard caches (HF_HOME for HuggingFace models,
~/.cache/docling for Docling), which is exactly where the runtime looks when
OFFLINE_MODE=1 forbids downloads.

Core models (embedding, reranker) fail the run if they can't be fetched.
Optional extras (Whisper, Docling) are skipped with a notice when their
package isn't installed.
"""

import argparse
import os
import sys


def fetch_embedding(model_name: str) -> str:
    from sentence_transformers import SentenceTransformer

    SentenceTransformer(model_name, trust_remote_code=True)
    return f"embedding model '{model_name}'"


def fetch_fastembed(model_name: str) -> str:
    """Seed the ONNX copy of the embedding model into the image cache.

    The core runtime embeds with fastembed; this puts its export of the model
    where services/embedder.py looks first (FASTEMBED_IMAGE_CACHE), so an
    offline container starts warm on either backend.
    """
    from fastembed import TextEmbedding

    # The Docker build runs this from /tmp with the app at the working
    # directory; make the app package importable either way.
    repo = os.environ.get("CLIO_ROOT") or os.getcwd()
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from services.embedder import FASTEMBED_IMAGE_CACHE, fastembed_model_id

    model_id = fastembed_model_id(model_name)
    if not model_id:
        return f"fastembed has no export of '{model_name}' (skipped; PyTorch backend serves it)"
    FASTEMBED_IMAGE_CACHE.mkdir(parents=True, exist_ok=True)
    TextEmbedding(model_id, cache_dir=str(FASTEMBED_IMAGE_CACHE))
    return f"fastembed model '{model_id}' -> {FASTEMBED_IMAGE_CACHE}"


def fetch_reranker(model_name: str) -> str:
    from sentence_transformers import CrossEncoder

    CrossEncoder(model_name)
    return f"reranker '{model_name}'"


def fetch_whisper(model_size: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "whisper: SKIPPED (faster-whisper not installed)"
    WhisperModel(model_size, device="cpu", compute_type="int8")
    return f"whisper '{model_size}'"


def fetch_docling() -> str:
    """Warm every cache Docling might read from — the API moved between
    versions, so try the dedicated downloader first, then force a pipeline
    init (which snapshot-downloads into the HF cache on older versions)."""
    try:
        import docling  # noqa: F401
    except ImportError:
        return "docling OCR: SKIPPED (docling not installed)"

    errors = []
    ok = False
    try:
        from docling.utils.model_downloader import download_models

        download_models()
        ok = True
    except Exception as e:  # noqa: BLE001 - older docling lacks this module
        errors.append(f"download_models: {e}")
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.document_converter import DocumentConverter

        DocumentConverter().initialize_pipeline(InputFormat.PDF)
        ok = True
    except Exception as e:  # noqa: BLE001
        errors.append(f"initialize_pipeline: {e}")
    if not ok:
        raise RuntimeError("; ".join(errors))
    return "docling OCR models"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2"),
    )
    parser.add_argument(
        "--reranker-model",
        default=os.environ.get("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"),
    )
    parser.add_argument(
        "--whisper-model",
        default=os.environ.get("WHISPER_MODEL", "base"),
    )
    parser.add_argument("--skip-whisper", action="store_true")
    parser.add_argument("--skip-docling", action="store_true")
    args = parser.parse_args()

    steps = [
        ("fastembed", lambda: fetch_fastembed(args.embedding_model), True),
        ("embedding", lambda: fetch_embedding(args.embedding_model), True),
        ("reranker", lambda: fetch_reranker(args.reranker_model), True),
    ]
    if not args.skip_whisper:
        steps.append(("whisper", lambda: fetch_whisper(args.whisper_model), False))
    if not args.skip_docling:
        steps.append(("docling", fetch_docling, False))

    failed = False
    for name, fetch, required in steps:
        try:
            print(f"[prefetch] fetching {name}…", flush=True)
            print(f"[prefetch] OK: {fetch()}", flush=True)
        except Exception as e:  # noqa: BLE001 - report and continue/fail at end
            print(f"[prefetch] FAILED ({name}): {e}", file=sys.stderr, flush=True)
            if required:
                failed = True

    if failed:
        print("[prefetch] core model fetch failed — aborting", file=sys.stderr)
        return 1
    print("[prefetch] done — caches are ready for offline use")
    return 0


if __name__ == "__main__":
    sys.exit(main())
