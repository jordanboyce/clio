# Clio — single production Dockerfile.
# Start with: docker compose up -d
#
# The result is meant to be PORTABLE: build once, `docker save` it, carry it
# into a customer's network or an air gap, and configure it there. Nothing
# site-specific is baked in — the model endpoint, credentials, CA
# certificates and data all arrive at run time (see docs/ONPREM.md). The only
# build-time choices are which optional model weights to include
# (OFFLINE_BUNDLE, WITH_DOCLING, BAKE_EMBEDDING), because those are downloads,
# not config — and SLIM, which drops the optional ML runtimes for a lean
# image that still indexes, searches, reranks, chats and serves MCP.

# ── Stage 1: build the Vue frontend ──
# (debian-based node image: the pinned rollup/esbuild natives are glibc builds)
FROM node:24-bookworm-slim AS frontend
# Include this stage in the image's SBOM. Its JavaScript ends up bundled into the
# final image, where a scan of that image alone cannot see which packages it came from.
ARG BUILDKIT_SBOM_SCAN_STAGE=true
WORKDIR /build

# Trust an optional enterprise CA before npm reaches the registry. Node uses
# its bundled roots by default, so point both Node and npm at Debian's updated
# trust store after installing any .crt files supplied in certs/ca/.
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY certs/ca/ /tmp/ca/
RUN if ls /tmp/ca/*.crt 1>/dev/null 2>&1; then \
      cp /tmp/ca/*.crt /usr/local/share/ca-certificates/ && \
      update-ca-certificates; \
    fi && rm -rf /tmp/ca
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/certs/ca-certificates.crt \
    NPM_CONFIG_CAFILE=/etc/ssl/certs/ca-certificates.crt

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

# The browser runtime a website export carries (docs/STATIC_SEARCH.md):
# transformers.js and its WebAssembly build, pinned and checksummed in the
# script, about 26 MB. With it the export can be a whole site that embeds
# queries with Clio's own model file and loads nothing from Hugging Face or a
# CDN, which a locked-down network needs. Each package's package.json is kept
# under node_modules/ so this stage's SBOM scan names them.
COPY scripts/vendor_static_search.mjs /tmp/vendor_static_search.mjs
RUN node /tmp/vendor_static_search.mjs /opt/static-search && rm /tmp/vendor_static_search.mjs
COPY frontend/ ./
# The bundle banner records the build it came from (frontend/vite.config.js).
ARG APP_VERSION=dev
ARG VCS_REF=unknown
RUN CLIO_VERSION=${APP_VERSION} CLIO_VCS_REF=${VCS_REF} npm run build

# ── Stage 2: build Python dependencies ──
# Compilers live here and never reach the runtime image. Everything installs
# into a self-contained venv at /opt/venv that stage 3 copies wholesale; both
# stages share a base image and Python version, so the paths inside it stay
# valid. This keeps build-essential (~250 MB) out of what we ship while
# preserving the dependency layer cache.
FROM python:3.13-slim AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Optional corporate CA certificates, installed before any network fetch so
# pip works behind a TLS-intercepting proxy. Drop .crt files into certs/ca/
# before building; no-op when the directory holds only its .gitkeep.
COPY certs/ca/ /tmp/ca/
RUN if ls /tmp/ca/*.crt 1>/dev/null 2>&1; then \
      cp /tmp/ca/*.crt /usr/local/share/ca-certificates/ && \
      update-ca-certificates; \
    fi && rm -rf /tmp/ca
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# torch is installed first from the CPU-only wheel index: sentence-transformers
# pulls torch, and without this pin pip downloads the CUDA build (~2GB of GPU
# libraries that do nothing in this container).
#
# The default hosted image ships core + tesseract OCR + audio transcription.
# Docling (the heavyweight layout-model OCR: torch vision stack + opencv) is
# opt-in:  docker compose build --build-arg WITH_DOCLING=1
# It brings torchvision along, installed from the SAME CPU index as torch so
# the compiled ops match (a PyPI torchvision against index torch segfaults).
#
# The default image keeps torch (docling and the wider sentence-transformers
# model catalog need it), so both embedding backends are installed: fastembed
# from requirements.txt and sentence-transformers from requirements-torch.txt.
#
# SLIM=1 builds the lean image: the core requirements only — fastembed (ONNX)
# serves the embedding model AND the search reranker, so nothing is lost for
# indexing, search, chat and MCP. What goes: PyTorch, the sentence-transformers
# catalog, Tesseract/Docling OCR of scanned PDFs and Whisper transcription.
# About 40% of the size (1.6 GB vs 4.0 GB); the right image for a knowledge base that
# serves agents:   docker compose build --build-arg SLIM=1
# WITH_PII=1 adds Microsoft Presidio and the small English spaCy model for
# personal-data detection (names, addresses, ids). Without it the built-in
# regex set still finds emails, phones, cards, IBANs, SSNs and IPs:
#   docker compose build --build-arg WITH_PII=1
ARG WITH_DOCLING=0
ARG WITH_PII=0
ARG SLIM=0
COPY requirements.txt requirements-torch.txt requirements-ocr.txt requirements-audio.txt requirements-docling.txt requirements-pii.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && if [ "$SLIM" = "1" ]; then \
         pip install --no-cache-dir -r requirements.txt; \
       else \
         pip install --no-cache-dir "torch==2.11.0" --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r requirements.txt -r requirements-torch.txt -r requirements-ocr.txt -r requirements-audio.txt; \
       fi \
    && if [ "$WITH_DOCLING" = "1" ] && [ "$SLIM" != "1" ]; then \
         pip install --no-cache-dir torchvision --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r requirements-docling.txt; \
       fi \
    && if [ "$WITH_PII" = "1" ]; then \
         pip install --no-cache-dir -r requirements-pii.txt \
         && python -m spacy download en_core_web_sm; \
       fi

# ── Stage 3: runtime ──
FROM python:3.13-slim AS runtime

WORKDIR /app

# Runtime system dependencies only — no compilers.
#   curl            — the HEALTHCHECK below
#   tesseract/poppler — OCR of scanned PDFs works out of the box
#   libgomp1        — OpenMP runtime. torch, faiss-cpu and ctranslate2
#                     (faster-whisper) all link against it; it arrived
#                     implicitly with gcc before compilers moved to the deps
#                     stage, and without it `import torch` fails at startup
#                     with a missing libgomp.so.1.
#   libgl1, libglib2.0-0 — docling depends on full opencv-python (not the
#                     headless build), whose cv2 extension links libGL.so.1
#                     and libgthread-2.0. Neither ships in python:*-slim, so
#                     local Docling OCR raised ImportError on the first scanned
#                     PDF even though the image advertises OCR out of the box.
# The build-time smoke test below keeps all of this from regressing silently.
# SLIM=1 skips the OCR packages (no torch, no docling, no tesseract).
ARG SLIM=0
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    openssh-client \
    libgomp1 \
    && if [ "$SLIM" != "1" ]; then \
         apt-get install -y --no-install-recommends \
           libgl1 \
           libglib2.0-0 \
           tesseract-ocr \
           tesseract-ocr-eng \
           poppler-utils; \
       fi \
    && rm -rf /var/lib/apt/lists/*

# Corporate CA certificates again, this time for outbound TLS at runtime
# (AI providers, Ollama, HuggingFace) — the trust store is not part of the venv.
#
# This is the BUILD-time path, for a site that builds its own image. A
# prebuilt image can't use it, so docker/entrypoint.sh installs certificates
# mounted at /certs/ca on every start as well; either path works, and a site
# that uses neither pays nothing.
COPY certs/ca/ /tmp/ca/
RUN if ls /tmp/ca/*.crt 1>/dev/null 2>&1; then \
      cp /tmp/ca/*.crt /usr/local/share/ca-certificates/ && \
      update-ca-certificates; \
    fi && rm -rf /tmp/ca
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Fail the build — not the first user request — if a native dependency is
# missing its shared libraries. cv2 and ctranslate2 are imported directly
# rather than via docling/faster_whisper: they are the extensions that actually
# dlopen the system libs above, and importing them is fast and side-effect-free
# (importing docling itself would try to resolve model caches).
ARG WITH_DOCLING=0
RUN if [ "$SLIM" = "1" ]; then \
      python -c "import faiss, fastembed, onnxruntime, fastapi, uvicorn, mcp; from fastembed.rerank.cross_encoder import TextCrossEncoder; print('slim runtime deps ok')"; \
    else \
      python -c "import torch, faiss, fastembed, sentence_transformers, fastapi, uvicorn, ctranslate2, pytesseract; print('runtime deps ok')" \
      && python -c "import io, wave; b = io.BytesIO(); w = wave.open(b, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(3200)); w.close(); b.seek(0); from faster_whisper.audio import decode_audio; assert len(decode_audio(b)) > 0; print('audio decode ok')"; \
    fi \
    && if [ "$WITH_DOCLING" = "1" ] && [ "$SLIM" != "1" ]; then python -c "import cv2; print('docling deps ok')"; fi

# Bake the embedding model into the image. Cold starts stay fast on platforms
# with ephemeral filesystems (Railway, Render, Fly), and the container never
# needs HuggingFace reachable at runtime.
#
# Both backends are baked so either starts warm: fastembed's ONNX copy into
# /opt/clio/models/fastembed (a fixed image path — the data directory is a
# volume, so a bake there would be hidden by the mount; FastEmbedService
# looks in the image path first, then under DATA_DIR/models/fastembed) and
# the sentence-transformers copy into HF_HOME as before.
#
# ON by default. The download needs huggingface.co reachable at build time;
# on a network that blocks the hub build with `--build-arg BAKE_EMBEDDING=0`
# and either pre-seed the caches (docs/AIRGAP.md) or point
# EMBEDDING_PROVIDER at a non-local backend (ollama / openai_compatible / a
# hosted API) — otherwise the model is fetched at first start instead.
ARG BAKE_EMBEDDING=1
# The model baked here is also the container's default (ENV EMBEDDING_MODEL
# below), so an image built with --build-arg EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
# uses bge-small with no .env edit. Keep this default in step with config.py and
# scripts/package_image.{sh,ps1}; the bake steps below compare against it.
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
# Declared here rather than at its own step below because it also decides
# whether a failed embedding bake fails the build.
ARG OFFLINE_BUNDLE=0
# The reranker rides along with the embedding bake, so a stock image reranks
# every search from the first query (ENABLE_RERANKER unset = on whenever the
# model is on disk). RERANKER_MODEL is the name the app is configured with;
# RERANKER_FASTEMBED_MODEL is fastembed's id for the same weights (the ONNX
# export; services/reranker.py holds the mapping, which is not on the image
# yet at this layer). Override both together to bake a different model.
ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ARG RERANKER_FASTEMBED_MODEL=Xenova/ms-marco-MiniLM-L-6-v2
# The speech model rides along too, in the full image only, so recordings work
# on a network that can never reach huggingface.co. About 145 MB.
ARG WHISPER_MODEL=base
ENV HF_HOME=/opt/hf-cache
# Best effort for the stock image: a build behind a firewall that blocks
# huggingface.co must still produce a working image. If the bake cannot
# download, the build prints a warning and continues; the app then offers the
# download (with progress) at first start, or Ollama / a hosted provider instead.
#
# Not for an embedding model the build asked for by name (anything but the
# default) or an offline bundle: such an image is made to run where nothing
# can be downloaded, so one without its model would only fail at the site.
# Those builds stop here instead.
RUN strict_embedding=0; \
    if [ "$EMBEDDING_MODEL" != "all-MiniLM-L6-v2" ] || [ "$OFFLINE_BUNDLE" = "1" ]; then strict_embedding=1; fi; \
    if [ "$BAKE_EMBEDDING" = "1" ]; then \
      ( python -c "from fastembed import TextEmbedding; m='${EMBEDDING_MODEL}'; TextEmbedding(m if '/' in m else 'sentence-transformers/' + m, cache_dir='/opt/clio/models/fastembed')" \
        && ( [ "$SLIM" = "1" ] || python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')" ) ) \
      || if [ "$strict_embedding" = "1" ]; then \
           echo "ERROR: could not bake the embedding model '${EMBEDDING_MODEL}' this build asked for (huggingface.co unreachable, or a model id fastembed does not know?). Stopping rather than producing an image without its model." >&2; \
           exit 1; \
         else \
           echo "WARNING: could not bake the embedding model '${EMBEDDING_MODEL}' (huggingface.co unreachable from this build?). The image still works: the app downloads it at first start, or pick Ollama / a hosted provider. See docs/AIRGAP.md to pre-seed."; \
         fi; \
      ( python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('${RERANKER_FASTEMBED_MODEL}', cache_dir='/opt/clio/models/fastembed')" \
        && ( [ "$SLIM" = "1" ] || python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKER_MODEL}')" ) ) \
      || echo "WARNING: could not bake the reranker '${RERANKER_MODEL}'. Search still works, in retrieval order; the app fetches the reranker at first start when it can. See docs/AIRGAP.md to pre-seed."; \
      [ "$SLIM" = "1" ] || python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_MODEL}', device='cpu', compute_type='int8')" \
      || echo "WARNING: could not bake Whisper model '${WHISPER_MODEL}'. Everything else works; recordings need the model fetched at run time or pre-seeded. See docs/AIRGAP.md."; \
    fi

# Optional full offline bundle for air-gapped deployments: additionally bake
# the Docling (local OCR) models, and fail the build if any model cannot be
# fetched, so nothing is ever needed at runtime. Adds roughly 500 MB.
#   docker compose build --build-arg OFFLINE_BUNDLE=1
# Pair with OFFLINE_MODE=1 at runtime; full walkthrough in docs/AIRGAP.md.
# (ARG OFFLINE_BUNDLE is declared with the embedding bake above.)
#
# The prefetch resolves model ids through services/embedder.py and
# services/reranker.py, and the application is not copied in until later, so
# those modules (and config.py, which they read) come along on their own.
# Without them every core fetch failed with "No module named 'services'".
COPY scripts/prefetch_offline_models.py config.py /tmp/clio-prefetch/
COPY services/__init__.py services/embedder.py services/embedding_providers.py services/reranker.py /tmp/clio-prefetch/services/
RUN if [ "$OFFLINE_BUNDLE" = "1" ]; then \
      cd /tmp/clio-prefetch \
      && CLIO_ROOT=/tmp/clio-prefetch DATA_DIR=/tmp/clio-prefetch/data \
         python prefetch_offline_models.py --embedding-model "$EMBEDDING_MODEL" --whisper-model "$WHISPER_MODEL"; \
    fi && rm -rf /tmp/clio-prefetch

# The unprivileged service user reads the baked models; the download step
# leaves some cache metadata owner-only.
RUN for d in /opt/clio /opt/hf-cache /opt/whisper-cache; do [ -d "$d" ] && chmod -R a+rX "$d"; done; true

# Application code and the built frontend
COPY . .
COPY --from=frontend /build/dist ./frontend/dist
COPY --from=frontend /opt/static-search/vendor /opt/clio/static-search/vendor

# The server runs as an unprivileged user (entrypoint.sh drops to it after the
# root-only steps). Code and models stay root-owned and read-only to it; only
# the data directory is writable.
RUN groupadd --gid 10001 clio     && useradd --uid 10001 --gid clio --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin clio     && mkdir -p /app/data/documents /app/data/indexes     && chown -R clio:clio /app/data

# For a build that named its embedding model (or an offline bundle), check with
# the app's own lookup that the copy baked above is the one a container will
# load: fastembed_cached_in must answer with the image cache, not "nothing", so
# the model can never be fetched (or fail to be) at the site instead.
RUN if [ "$BAKE_EMBEDDING" = "1" ] && { [ "$EMBEDDING_MODEL" != "all-MiniLM-L6-v2" ] || [ "$OFFLINE_BUNDLE" = "1" ]; }; then \
      DATA_DIR=/tmp/clio-bake-check HF_HUB_OFFLINE=1 python -c "import sys; from services.embedder import FASTEMBED_IMAGE_CACHE, fastembed_cached_in, fastembed_model_id; m = sys.argv[1]; fe = fastembed_model_id(m); found = fastembed_cached_in(m) if fe else None; print(f'embedding model {m!r}: fastembed id {fe!r}, found in {found}'); sys.exit(0 if fe and found == FASTEMBED_IMAGE_CACHE else 1)" "$EMBEDDING_MODEL" \
      || { echo "ERROR: the app does not find '${EMBEDDING_MODEL}' in /opt/clio/models/fastembed, where this build baked it." >&2; exit 1; }; \
      rm -rf /tmp/clio-bake-check; \
    fi

# Runtime setup that cannot be baked: site CA certificates and the data dirs.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Provenance for an image that travels as a file. `docker inspect` on the
# other side of an air gap is the only way to tell what was loaded, so the
# version and commit go in as labels rather than living in a build log.
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Clio"       org.opencontainers.image.description="Private document indexing, grounded chat and MCP access"       org.opencontainers.image.source="https://github.com/jordanboyce/clio"       org.opencontainers.image.licenses="MIT"       org.opencontainers.image.authors="Jordan Boyce"       dev.clio.lineage="clio.helicon.58a48ab52f51"       org.opencontainers.image.version="${APP_VERSION}"       org.opencontainers.image.revision="${VCS_REF}"       dev.clio.embedding-model="${EMBEDDING_MODEL}"
# The image's own default embedding model: the one baked above. A run-time
# EMBEDDING_MODEL (docker run -e, an env file, compose) still wins, and each
# collection keeps the model it was indexed with whatever this says.
ENV CLIO_VERSION=${APP_VERSION} \
    CLIO_VCS_REF=${VCS_REF} \
    EMBEDDING_MODEL=${EMBEDDING_MODEL}

EXPOSE 8473

# HOST is 0.0.0.0 here (the app defaults to loopback) because the container
# boundary, not the bind address, is what limits exposure. It is set as an ENV
# rather than only on the CMD line so the app's own startup security check sees
# the address it will actually be reachable on and can warn when the port is
# open with no AUTH_PASSWORD.
ENV PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    HOST=0.0.0.0

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# PaaS platforms (Railway, Render, Heroku-style) inject PORT and expect the
# app to listen on it; fall back to the documented default otherwise.
# Shell form is deliberate — exec form would not expand ${PORT}.
# SSL_CERTFILE and SSL_KEYFILE have to be passed on: uvicorn does not read them
# itself, and a file that is missing should stop the start, not fall back to HTTP.
CMD uvicorn main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8473} --no-server-header \
    ${SSL_CERTFILE:+--ssl-certfile "$SSL_CERTFILE"} ${SSL_KEYFILE:+--ssl-keyfile "$SSL_KEYFILE"}

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl --noproxy '*' -fsSk "$([ -n "$SSL_CERTFILE" ] && echo https || echo http)://localhost:${PORT:-8473}/readyz" || exit 1
