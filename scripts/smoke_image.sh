#!/usr/bin/env bash
# Smoke-test a built Clio image the way a locked-down site runs it.
#
#   scripts/smoke_image.sh <image> <expected embedding model>
#   scripts/smoke_image.sh ghcr.io/jordanboyce/clio@sha256:... BAAI/bge-small-en-v1.5
#
# The container gets the hardened settings of docker-compose.hardened.yml
# (non-root, read-only root filesystem, no capabilities, a process limit, /tmp
# as the only writable path besides the data volume) and NO network, so the
# embedding model and the reranker can only come from the image itself. Then:
#
#   1. /readyz answers 200 and /health reports the expected embedding model ready
#   2. a DOCX (built inside the container) and an Open-PSA MEF model upload and
#      index, through the extraction helper processes
#   3. a semantic search runs on the baked model and finds the uploaded text
#   4. the PRA API lists the model
#   5. the log holds no traceback
#
# Every request runs inside the container (docker exec curl): with no network
# there is nothing to publish a port on. Needs docker, and python3 on the host.
set -euo pipefail

IMAGE="${1:?usage: smoke_image.sh <image> <expected embedding model>}"
EXPECT_MODEL="${2:?usage: smoke_image.sh <image> <expected embedding model>}"
NAME="clio-smoke-$$"
VOLUME="clio-smoke-data-$$"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
MEF="$HERE/tests/fixtures/pra/flight_hydraulics.xml"

cleanup() {
    docker logs "$NAME" > "/tmp/$NAME.log" 2>&1 || true
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOLUME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "SMOKE FAIL: $*" >&2
    echo "---- container log (last 80 lines) ----" >&2
    docker logs --tail 80 "$NAME" >&2 || true
    exit 1
}

api() {  # api METHOD PATH [extra curl args...] -> body on stdout
    local method="$1" path="$2"; shift 2
    docker exec -i "$NAME" curl -sS --max-time 120 -X "$method" "http://127.0.0.1:8473$path" "$@"
}

json() {  # json '<python expression on d>' < body
    python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

echo "== starting $IMAGE (hardened, no network)"
docker volume create "$VOLUME" >/dev/null
docker run -d --name "$NAME" \
    --user 10001:10001 --read-only --cap-drop ALL \
    --security-opt no-new-privileges:true --pids-limit 512 \
    --tmpfs /tmp:size=512m,mode=1777 \
    -v "$VOLUME:/app/data" \
    -e PYTHONDONTWRITEBYTECODE=1 -e HOME=/tmp \
    -e HF_HUB_OFFLINE=1 \
    --network none \
    "$IMAGE" >/dev/null

echo "== waiting for /readyz"
for _ in $(seq 1 90); do
    code="$(docker exec "$NAME" curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8473/readyz 2>/dev/null || true)"
    [ "$code" = "200" ] && break
    sleep 2
done
[ "$code" = "200" ] || fail "/readyz never answered 200 (last: ${code:-none})"

echo "== waiting for the embedding model ($EXPECT_MODEL)"
for _ in $(seq 1 90); do
    health="$(api GET /health)"
    status="$(echo "$health" | json 'd["embedding"]["status"]')"
    [ "$status" = "ready" ] && break
    [ "$status" = "error" ] && break
    sleep 2
done
model="$(echo "$health" | json 'd["embedding"]["model"]')"
missing="$(echo "$health" | json 'd["embedding"].get("local_model_missing")')"
echo "   embedding: model=$model status=$status local_model_missing=$missing"
[ "$model" = "$EXPECT_MODEL" ] || fail "embedding model is $model, expected $EXPECT_MODEL"
[ "$status" = "ready" ] || fail "embedding model not ready (status $status): it was not baked into the image"

echo "== uploading a DOCX and an Open-PSA MEF model"
docker exec "$NAME" python -c "
import docx
d = docx.Document()
d.add_paragraph('The hydraulic accumulator precharge is checked every 400 flight hours.')
d.save('/tmp/smoke.docx')
"
body="$(api POST '/documents/upload?collection_id=default' -F 'files=@/tmp/smoke.docx')"
echo "$body" | json 'd["documents_processed"]' | grep -qx 1 || fail "DOCX upload: $body"
body="$(api POST '/documents/upload?collection_id=default' -F 'files=@-;filename=flight_hydraulics.xml' < "$MEF")"
echo "$body" | json 'd["documents_processed"]' | grep -qx 1 || fail "MEF upload: $body"

echo "== semantic search on the baked model"
body="$(api POST '/search?collection_id=default' -H 'Content-Type: application/json' \
    -d '{"query": "how often is the accumulator precharge inspected", "top_k": 3, "mode": "semantic"}')"
mode="$(echo "$body" | json 'd["retrieval"]["mode"]')"
top="$(echo "$body" | json 'd["results"][0]["filename"] if d["results"] else ""')"
reranked="$(echo "$body" | json 'd["retrieval"].get("reranked")')"
echo "   mode=$mode top=$top reranked=$reranked"
[ "$mode" = "semantic" ] || fail "search fell back to $mode: the embedding model did not run"
[ "$top" = "smoke.docx" ] || fail "semantic search did not find the DOCX first (got '$top')"

echo "== PRA API"
body="$(api GET '/api/pra/models?collection_id=default')"
echo "$body" | json 'd["models"][0]["source_format"]' | grep -qx pra-mef || fail "PRA model not listed: $body"

echo "== log"
if docker logs "$NAME" 2>&1 | grep -q "Traceback"; then
    fail "the container log holds a traceback"
fi

echo "SMOKE OK: $IMAGE ($EXPECT_MODEL)"
