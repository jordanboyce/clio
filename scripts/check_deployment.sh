#!/bin/sh
# Check a running Clio deployment from another machine, against what
# docs/ENTERPRISE.md promises. It only reads, and exits 0 when nothing failed.
#
#   sh scripts/check_deployment.sh https://clio.corp.example
#   sh scripts/check_deployment.sh --cacert corp-root.crt https://clio.corp.example
#
# To include the deployment's own readiness report, set CLIO_CHECK_TOKEN to the
# password or an admin's token. It is passed to curl on stdin, never as an argument.
#
# Requests go straight to the host and ignore HTTP_PROXY: an outbound proxy
# answering for it would make a closed port look open. Other curl options, such
# as --resolve, can go in CLIO_CHECK_CURL_OPTS.
set -u

CACERT=""
DIRECT_PORT=8473
URL=""
while [ $# -gt 0 ]; do
    case "$1" in
        --cacert) CACERT="$2"; shift 2 ;;
        --direct-port) DIRECT_PORT="$2"; shift 2 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) URL="${1%/}"; shift ;;
    esac
done
if [ -z "$URL" ]; then
    echo "usage: $0 [--cacert FILE] [--direct-port N] https://clio.corp.example" >&2
    exit 2
fi

FAILED=0
WARNED=0
pass() { echo "PASS  $1"; }
warn() { echo "WARN  $1"; WARNED=$((WARNED + 1)); }
fail() { echo "FAIL  $1"; [ $# -gt 1 ] && echo "      $2"; FAILED=$((FAILED + 1)); }

# shellcheck disable=SC2086 # CLIO_CHECK_CURL_OPTS is meant to split into options.
fetch() {
    if [ -n "$CACERT" ]; then
        curl -sS --noproxy '*' --max-time 20 --cacert "$CACERT" ${CLIO_CHECK_CURL_OPTS:-} "$@"
    else
        curl -sS --noproxy '*' --max-time 20 ${CLIO_CHECK_CURL_OPTS:-} "$@"
    fi
}
status_of() { fetch -o /dev/null -w '%{http_code}' "$1" 2>/dev/null; }

SCHEME="${URL%%://*}"
HOSTPORT="${URL#*://}"
HOSTPORT="${HOSTPORT%%/*}"
HOST="${HOSTPORT%%:*}"

# ── Reachable at all? ─────────────────────────────────────────────────────
ERROR=$(fetch -o /dev/null "$URL/livez" 2>&1)
if [ $? -ne 0 ]; then
    fail "Cannot reach $URL" "$(printf '%s\n' "$ERROR" | head -n 1)"
    case "$ERROR" in
        *certificate*|*SSL*) echo "      The host does not trust this certificate. Pass the issuing CA with --cacert." ;;
    esac
    echo
    echo "Stopped: nothing else can be checked."
    exit 1
fi
pass "Reachable at $URL"

# The probes answer anyone, so a 401 or 404 here means they do not exist. Anything
# further would read a password gate as an app API that is there.
READY_STATUS=$(status_of "$URL/readyz")
case "$READY_STATUS" in
    401|404)
        fail "This image is too old for this guide: it has no /readyz" "Install a release newer than 1.2.0."
        echo
        echo "Stopped: the rest assumes a newer release."
        exit 1
        ;;
esac
READY=$(fetch "$URL/readyz" 2>/dev/null)
case "$READY" in
    *'"ready"'*) pass "Ready to serve (/readyz)" ;;
    *) fail "Not ready" "/readyz answered: $READY" ;;
esac

# ── Transport ─────────────────────────────────────────────────────────────
HEADERS=$(fetch -D - -o /dev/null "$URL/health" 2>/dev/null)
has_header() { printf '%s' "$HEADERS" | grep -qi "^$1:"; }
if [ "$SCHEME" = "https" ]; then
    if has_header "strict-transport-security"; then
        pass "Clio knows requests arrive over HTTPS (HSTS sent)"
    else
        fail "No HSTS header: Clio does not know the request was HTTPS" \
             "Set FORWARDED_ALLOW_IPS to your proxy's address. Without it, client addresses are lost too."
    fi
    PLAIN=$(curl -sS --noproxy '*' --max-time 10 ${CLIO_CHECK_CURL_OPTS:-} -o /dev/null -w '%{http_code}' "http://$HOST/livez" 2>/dev/null)
    case "$PLAIN" in
        301|308) pass "Plain HTTP redirects to HTTPS" ;;
        000) pass "Plain HTTP is closed" ;;
        *) warn "Plain HTTP answered $PLAIN on port 80. Expected a redirect or a closed port" ;;
    esac
else
    warn "Not using HTTPS: passwords and documents cross the network in clear text"
fi

for name in x-content-type-options x-frame-options content-security-policy referrer-policy; do
    if ! has_header "$name"; then
        fail "Response header missing: $name" "A proxy on the way may be stripping it."
    fi
done
has_header "x-content-type-options" && has_header "content-security-policy" && pass "Security headers present"

# ── Who can get in ────────────────────────────────────────────────────────
ANON=$(status_of "$URL/api/collections")
case "$ANON" in
    401) pass "Anonymous callers are refused" ;;
    200) fail "Anyone can read this deployment with no credential" \
              "Set AUTH_PASSWORD, or IDENTITY_PROVIDER with AUTH_REQUIRE_IDENTITY=true." ;;
    *) fail "Anonymous request answered $ANON, expected 401" ;;
esac

HEALTH=$(fetch "$URL/health" 2>/dev/null)
if [ "$ANON" = "401" ]; then
    case "$HEALTH" in
        '{"status":"healthy"}') pass "/health tells anonymous callers only its status" ;;
        *) fail "/health discloses more than its status to an anonymous caller" "$HEALTH" ;;
    esac
fi

APP=$(status_of "$URL/api/v1/me")
case "$APP" in
    401) pass "App API refuses a missing credential" ;;
    *) fail "/api/v1/me answered $APP with no credential, expected 401" ;;
esac

# ── The bypass ────────────────────────────────────────────────────────────
if [ "$HOSTPORT" != "$HOST:$DIRECT_PORT" ]; then
    DIRECT=$(curl -sS --noproxy '*' --max-time 5 ${CLIO_CHECK_CURL_OPTS:-} -o /dev/null -w '%{http_code}' "http://$HOST:$DIRECT_PORT/livez" 2>/dev/null)
    if [ "$DIRECT" = "000" ] || [ -z "$DIRECT" ]; then
        pass "Clio's own port $DIRECT_PORT is closed from here: the proxy is the only way in"
    else
        fail "Clio's port $DIRECT_PORT answers directly ($DIRECT), bypassing the proxy" \
             "Remove the published port, and block it at the firewall."
    fi
fi

# ── The deployment's own report ───────────────────────────────────────────
if [ -n "${CLIO_CHECK_TOKEN:-}" ]; then
    REPORT=$(printf 'header = "Authorization: Bearer %s"\n' "$CLIO_CHECK_TOKEN" | fetch -K - "$URL/api/system/readiness" 2>/dev/null)
    case "$REPORT" in
        *'"ready":true'*) pass "Readiness report: ready" ;;
        *'"ready":false'*) fail "Readiness report: not ready" "Read it at About > Check this deployment, or GET /api/system/readiness." ;;
        *) warn "Could not read the readiness report. Is CLIO_CHECK_TOKEN an admin's credential?" ;;
    esac
else
    echo "SKIP  Readiness report (set CLIO_CHECK_TOKEN to include it)"
fi

echo
if [ "$FAILED" -gt 0 ]; then
    echo "$FAILED failed, $WARNED warned."
    exit 1
fi
echo "All checks passed, $WARNED warned."
