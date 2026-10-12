# Clio — single production Dockerfile.
# Start with: docker compose up -d
#
# The result is meant to be PORTABLE: build once, `docker save` it, carry it
# into a customer's network or an air gap, and configure it there. Nothing
# site-specific is baked in — the model endpoint, credentials, CA
# certificates and data all arrive at run time (see docs/ONPREM.md). The only
# build-time choices are which optional model weights to include
# (OFFLINE_BUNDLE, WITH_DOCLING, BAKE_EMBEDDING), because those are downloads,
# not config.

# ── Stage 1: build the Vue frontend ──
# (debian-based node image: the pinned rollup/esbuild natives are glibc builds)
FROM node:24-bookworm-slim AS frontend
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
COPY frontend/ ./
RUN npm run build

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
# The image keeps torch (docling and the reranker need it), so both embedding
# backends are installed: fastembed from requirements.txt and
# sentence-transformers from requirements-torch.txt.
ARG WITH_DOCLING=0
COPY requirements.txt requirements-torch.txt requirements-ocr.txt requirements-audio.txt requirements-docling.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "torch==2.11.0" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt -r requirements-torch.txt -r requirements-ocr.txt -r requirements-audio.txt \
    && if [ "$WITH_DOCLING" = "1" ]; then \
         pip install --no-cache-dir torchvision --index-url https://download.pytorch.org/whl/cpu \
         && pip install --no-cache-dir -r requirements-docling.txt; \
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
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libgomp1 \
    libgl1 \
    libglib2.0-0 \
    tesseract-ocr \
    tesseract-ocr-eng \
    poppler-utils \
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
RUN python -c "import torch, faiss, fastembed, sentence_transformers, fastapi, uvicorn, ctranslate2, pytesseract; print('runtime deps ok')" \
    && if [ "$WITH_DOCLING" = "1" ]; then python -c "import cv2; print('docling deps ok')"; fi

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
ARG EMBEDDING_MODEL=all-MiniLM-L6-v2
ENV HF_HOME=/opt/hf-cache
# Best effort: a build behind a firewall that blocks huggingface.co must
# still produce a working image. If the bake cannot download, the build
# prints a warning and continues; the app then offers the download (with
# progress) at first start, or Ollama / a hosted provider instead.
RUN if [ "$BAKE_EMBEDDING" = "1" ]; then \
      ( python -c "from fastembed import TextEmbedding; m='${EMBEDDING_MODEL}'; TextEmbedding(m if '/' in m else 'sentence-transformers/' + m, cache_dir='/opt/clio/models/fastembed')" \
        && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')" ) \
      || echo "WARNING: could not bake the embedding model '${EMBEDDING_MODEL}' (huggingface.co unreachable from this build?). The image still works: the app downloads it at first start, or pick Ollama / a hosted provider. See docs/AIRGAP.md to pre-seed."; \
    fi

# Optional full offline bundle for air-gapped deployments: additionally bake
# the reranker, Whisper (audio transcription), and Docling (local OCR) models
# so nothing is ever fetched at runtime. Adds roughly 1 GB to the image.
#   docker compose build --build-arg OFFLINE_BUNDLE=1
# Pair with OFFLINE_MODE=1 at runtime; full walkthrough in docs/AIRGAP.md.
ARG OFFLINE_BUNDLE=0
ARG WHISPER_MODEL=base
COPY scripts/prefetch_offline_models.py /tmp/prefetch_offline_models.py
RUN if [ "$OFFLINE_BUNDLE" = "1" ]; then \
      python /tmp/prefetch_offline_models.py --whisper-model "$WHISPER_MODEL"; \
    fi && rm /tmp/prefetch_offline_models.py

# Application code and the built frontend
COPY . .
COPY --from=frontend /build/dist ./frontend/dist

RUN mkdir -p /app/data/documents /app/data/indexes

# Runtime setup that cannot be baked: site CA certificates and the data dirs.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Provenance for an image that travels as a file. `docker inspect` on the
# other side of an air gap is the only way to tell what was loaded, so the
# version and commit go in as labels rather than living in a build log.
ARG APP_VERSION=dev
ARG VCS_REF=unknown
LABEL org.opencontainers.image.title="Clio"       org.opencontainers.image.description="Private document indexing, grounded chat and MCP access"       org.opencontainers.image.source="https://github.com/jordanboyce/clio"       org.opencontainers.image.licenses="MIT"       org.opencontainers.image.version="${APP_VERSION}"       org.opencontainers.image.revision="${VCS_REF}"
ENV CLIO_VERSION=${APP_VERSION}

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
CMD uvicorn main:app --host ${HOST:-0.0.0.0} --port ${PORT:-8473}

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:${PORT:-8473}/health || exit 1
