#!/bin/sh
# Container entrypoint.
#
# Everything here is about making ONE image portable: the same bytes an
# operator loads from a tarball, or pulls from a registry, have to work
# against whatever private PKI and endpoints the site runs. Anything baked at
# build time can't do that — the image would need rebuilding per site.
#
# Steps, all no-ops when nothing is mounted:
#   1. Trust the site's CA certificates (mount them at /certs/ca).
#   2. Make the Python HTTP clients trust them too — they pin certifi's own
#      bundle, so the system trust store alone is not enough.
#   3. Hand over to the CMD (uvicorn).
set -e

CA_DIR="${EXTRA_CA_CERTS_DIR:-/certs/ca}"

ca_files() {
    # Only real files: an empty mount, or one holding just a .gitkeep, must
    # leave the trust store untouched rather than emptying it.
    find "$CA_DIR" -maxdepth 1 -type f \( -name '*.crt' -o -name '*.pem' \) 2>/dev/null
}

if [ "$(id -u)" = "0" ] && [ -d "$CA_DIR" ] && [ -n "$(ca_files)" ]; then
    echo "entrypoint: installing CA certificates from $CA_DIR"

    # 1. System trust store (curl, the healthcheck, anything linking OpenSSL).
    #    update-ca-certificates only reads .crt, so .pem files are copied
    #    under a .crt name rather than silently ignored.
    for f in $(ca_files); do
        cp "$f" "/usr/local/share/ca-certificates/$(basename "$f" | sed 's/\.pem$/.crt/')"
    done
    update-ca-certificates >/dev/null

    # 2. certifi's bundle, which httpx (and therefore the OpenAI SDK, the
    #    Anthropic SDK and every OpenAI-compatible endpoint call) uses
    #    instead of the system store. Rebuilt from a pristine copy on every
    #    start so restarting the container can't append the same CA twice,
    #    and so removing a CA from the mount actually removes it.
    CERTIFI_PEM="$(python -c 'import certifi; print(certifi.where())' 2>/dev/null || true)"
    if [ -n "$CERTIFI_PEM" ] && [ -f "$CERTIFI_PEM" ]; then
        [ -f "$CERTIFI_PEM.orig" ] || cp "$CERTIFI_PEM" "$CERTIFI_PEM.orig"
        cp "$CERTIFI_PEM.orig" "$CERTIFI_PEM"
        for f in $(ca_files); do
            printf '\n' >> "$CERTIFI_PEM"
            cat "$f" >> "$CERTIFI_PEM"
        done
    fi
fi

# The data directory is a bind mount or a volume, so it may arrive empty or
# owned by whoever created it on the host.
DATA="${DATA_DIR:-/app/data}"
APP_UID="${CLIO_UID:-10001}"
APP_GID="${CLIO_GID:-10001}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA/documents" "$DATA/indexes"
    # Hand the data directory to the unprivileged service user. Recursive only
    # when the top-level owner is wrong (first start, or a volume restored by
    # root), so a restart over a large corpus stays instant. Some bind mounts
    # (Docker Desktop on Windows) ignore ownership; that is fine, not fatal.
    if [ "$(stat -c %u "$DATA" 2>/dev/null)" != "$APP_UID" ]; then
        chown -R "$APP_UID:$APP_GID" "$DATA" 2>/dev/null             || echo "entrypoint: could not chown $DATA; continuing"
    fi
    # Everything above needed root. The server does not: drop to the service
    # user so a bug in document parsing or a prompt-injected tool call cannot
    # write outside the data directory or touch the system.
    exec setpriv --reuid="$APP_UID" --regid="$APP_GID" --clear-groups         --inh-caps=-all --bounding-set=-all --no-new-privs "$@"
fi

# Already unprivileged (compose `user:` / Kubernetes runAsUser). CA installation
# above is skipped in that case; bake the CAs into a derived image instead.
if [ -d "$CA_DIR" ] && [ -n "$(ca_files)" ]; then
    echo "entrypoint: WARNING: started as a non-root user, cannot install CA certificates from $CA_DIR"
fi
mkdir -p "$DATA/documents" "$DATA/indexes" 2>/dev/null || true
exec "$@"
