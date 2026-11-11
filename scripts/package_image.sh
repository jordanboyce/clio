#!/usr/bin/env bash
# Build a portable Clio image and package it for a machine that cannot
# build one: an air-gapped network, a customer site, an appliance.
#
#   ./scripts/package_image.sh                       # default image
#   ./scripts/package_image.sh --offline-bundle      # + reranker/Whisper/OCR models
#   ./scripts/package_image.sh --no-bake-embedding   # skip the built-in embedding model
#   ./scripts/package_image.sh --slim                # 1.5 GB: no PyTorch, OCR or audio
#   ./scripts/package_image.sh --with-proxy-image    # + nginx, for a host with no registry
#   ./scripts/package_image.sh --tag 1.4.0 --out /media/transfer
#   ./scripts/package_image.sh --embedding-model BAAI/bge-small-en-v1.5 --tag 1.4.0-bge
#                                # bake (and default to) another embedding model;
#                                # all-MiniLM-L6-v2 when not given
#
# Produces, in the output directory:
#   clio-<tag>.tar.gz        the image (docker load < this)
#   clio-<tag>.tar.gz.sha256 checksum to verify after the transfer
#   proxy-image.tar.gz       the reverse proxy's image, with --with-proxy-image
#   README.txt               what to do on the target host
#   everything in scripts/bundle_files.txt, at the same paths: compose files,
#   the example configuration, the guides, the checks and the agent skills
#
# Copy that directory across and follow docs/ENTERPRISE.md. Nothing else is needed
# on the target host beyond Docker itself.
set -euo pipefail

cd "$(dirname "$0")/.."

TAG="local"
OUT="dist-image"
OFFLINE_BUNDLE=0
WITH_DOCLING=0
BAKE_EMBEDDING=1
# Keep in step with the Dockerfile's ARG EMBEDDING_MODEL and config.py.
EMBEDDING_MODEL="all-MiniLM-L6-v2"
WHISPER_MODEL="base"
PLATFORM=""
SLIM=0
WITH_PROXY_IMAGE=0
# Keep in step with the default in docker-compose.proxy.yml.
PROXY_IMAGE="nginx:1.29-alpine"

usage() {
    # The comment block at the top of this file, without its leading "# ".
    awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --offline-bundle) OFFLINE_BUNDLE=1; shift ;;
        --with-docling) WITH_DOCLING=1; shift ;;
        --no-bake-embedding) BAKE_EMBEDDING=0; shift ;;
        # The model the image bakes and a container uses unless EMBEDDING_MODEL
        # is set at run time. With --offline-bundle the prefetch fetches it too.
        --embedding-model) EMBEDDING_MODEL="$2"; shift 2 ;;
        --slim) SLIM=1; shift ;;
        --with-proxy-image) WITH_PROXY_IMAGE=1; shift ;;
        --whisper-model) WHISPER_MODEL="$2"; shift 2 ;;
        # Build for the target's architecture when it differs from this
        # machine's (an arm64 laptop packaging for an amd64 server).
        --platform) PLATFORM="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
done

IMAGE="clio:${TAG}"
VCS_REF="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"

if [ -z "$EMBEDDING_MODEL" ]; then
    echo "--embedding-model needs a model id" >&2
    exit 1
fi

echo "==> Building ${IMAGE} (slim=${SLIM}, offline_bundle=${OFFLINE_BUNDLE}, docling=${WITH_DOCLING}, bake_embedding=${BAKE_EMBEDDING}, embedding_model=${EMBEDDING_MODEL})"
build_args=(
    --build-arg "OFFLINE_BUNDLE=${OFFLINE_BUNDLE}"
    --build-arg "WITH_DOCLING=${WITH_DOCLING}"
    --build-arg "BAKE_EMBEDDING=${BAKE_EMBEDDING}"
    --build-arg "EMBEDDING_MODEL=${EMBEDDING_MODEL}"
    --build-arg "SLIM=${SLIM}"
    --build-arg "WHISPER_MODEL=${WHISPER_MODEL}"
    --build-arg "APP_VERSION=${TAG}"
    --build-arg "VCS_REF=${VCS_REF}"
)
[ -n "$PLATFORM" ] && build_args+=(--platform "$PLATFORM")

docker build "${build_args[@]}" -t "$IMAGE" .

mkdir -p "$OUT"
ARCHIVE="${OUT}/clio-${TAG}.tar.gz"

echo "==> Saving ${IMAGE} to ${ARCHIVE}"
docker save "$IMAGE" | gzip > "$ARCHIVE"

echo "==> Checksumming"
# The checksum is what the receiving side verifies before loading; keep it
# beside the archive with a bare filename so `sha256sum -c` works there.
( cd "$OUT" && sha256sum "clio-${TAG}.tar.gz" > "clio-${TAG}.tar.gz.sha256" )

if [ "$WITH_PROXY_IMAGE" = 1 ]; then
    echo "==> Saving ${PROXY_IMAGE}"
    docker pull ${PLATFORM:+--platform "$PLATFORM"} "$PROXY_IMAGE"
    docker save "$PROXY_IMAGE" | gzip > "${OUT}/proxy-image.tar.gz"
    ( cd "$OUT" && sha256sum proxy-image.tar.gz > proxy-image.tar.gz.sha256 )
fi

echo "==> Copying deployment files"
while IFS= read -r entry || [ -n "$entry" ]; do
    entry="${entry%$'\r'}"
    case "$entry" in ""|\#*) continue ;; esac
    mkdir -p "${OUT}/$(dirname "$entry")"
    cp "$entry" "${OUT}/${entry}"
done < scripts/bundle_files.txt
mkdir -p "${OUT}/certs/tls" "${OUT}/certs/ca"

cat > "${OUT}/README.txt" <<EOF
Clio ${TAG} — offline install bundle
built $(date -u +%Y-%m-%dT%H:%M:%SZ) from ${VCS_REF}
slim: ${SLIM}   offline model bundle: ${OFFLINE_BUNDLE}   docling OCR: ${WITH_DOCLING}   bake embedding: ${BAKE_EMBEDDING}
embedding model: ${EMBEDDING_MODEL}
proxy image included: ${WITH_PROXY_IMAGE}

The embedding model above is the one this image uses when EMBEDDING_MODEL is
not set; leave it unset in .env to use it. Collections indexed earlier keep the
model they were built with (docs/AIRGAP.md: "Changing the embedding model").
Check a loaded image with:
  docker inspect -f '{{ index .Config.Labels "dev.clio.embedding-model" }}' clio:${TAG}

Follow docs/ENTERPRISE.md. In this bundle the image is clio:${TAG}.
An agent doing this for you should read skills/clio-deploy/SKILL.md first.

No firewall or TLS proxy to think about? The short way:

  sha256sum -c clio-${TAG}.tar.gz.sha256
  docker load < clio-${TAG}.tar.gz
  cp .env.onprem.example .env      # then edit: model endpoint, AUTH_PASSWORD
  # set CLIO_IMAGE=clio:${TAG} in .env
  docker compose -f docker-compose.onprem.yml up -d

Then open http://<host>:8473. See docs/ONPREM.md (air gap: docs/AIRGAP.md).
EOF

echo
echo "Bundle ready in ${OUT}:"
ls -lh "$OUT"
