# Hosting Clio on an enterprise network

The runbook for putting one Clio server on a corporate VM, behind TLS, for people and
for [registered apps](APP_API.md). It builds on the other deployment docs and links
to them for detail: [ONPREM.md](ONPREM.md) for private models, [IDENTITY.md](IDENTITY.md)
for sign-in, [SECURE_PROFILE.md](SECURE_PROFILE.md) for the hardened profile,
[AIRGAP.md](AIRGAP.md) for no-internet installs.

Clio is one process with its data on one volume, so it runs as **a single replica**. A
second replica against the same data is not supported. Plan availability around quick
restarts and backups ([what to back up](DEPLOYMENT.md#data-directory-and-backups)).

## The shape

```
                  corporate network
                         |
                     :443 TLS
                  +------+------+
  users, apps --> |    nginx    |  docker-compose.proxy.yml
                  +------+------+
                         | clio-proxy (internal network, no published port)
                  +------+------+
                  |    Clio     |  docker-compose.onprem.yml + .hardened.yml
                  +------+------+
                         |
           your IdP, model endpoint, Postgres (optional)
```

## Installing

**1. Get the image.** Pull it, or carry it in as a file with
`scripts/package_image.sh` where there is no registry access.

**2. Configure.**

```bash
cp .env.onprem.example .env
```

At minimum, set these in `.env`:

```bash
CLIO_IMAGE=ghcr.io/jordanboyce/clio:1.2.0   # or pin a digest
CLIO_PULL_POLICY=missing
CLIO_HOSTNAME=clio.corp.example
CLIO_PROFILE=secure

# Your own OpenAI-style model endpoint, or leave unset for search-only
AI_PROVIDER=openai_compatible
AI_BASE_URL=https://llm.corp.example/v1
AI_MODEL=your-model

# Who gets in: one of the two
IDENTITY_PROVIDER=oidc
AUTH_REQUIRE_IDENTITY=true
OIDC_ISSUER=https://sso.corp.example/realms/main
OIDC_AUDIENCE=clio
ADMIN_EMAILS=you@corp.example
# ...or a shared password for a small team:
# AUTH_PASSWORD=<long random value>

# Encrypt stored credentials with a key from your secret manager
SECRET_ENCRYPTION_KEY=<openssl rand -base64 32>
```

`IDENTITY_PROVIDER` on its own turns nobody away: it says how to tell who a caller is.
Keep `AUTH_REQUIRE_IDENTITY=true` with it, or set `PRIVATE_COLLECTIONS=true`. The
readiness report and the secure profile both call this out.

**3. Add your certificates.**

- `certs/tls/server.crt` (full chain) and `certs/tls/server.key`, for the proxy.
- Any internal CA that signs your IdP or model endpoint, as `.crt` files in `certs/ca/`.

The hardened overlay runs as an unprivileged user from the first instruction, so it
cannot install CAs at start. If you need a private CA there, build a derived image
([how](DEPLOYMENT.md#hardened-container)), or leave `user:` out of the overlay and keep
the rest.

**4. Check it before starting it.**

```bash
docker run --rm --env-file .env $CLIO_IMAGE python scripts/preflight.py --probe
```

**5. Start it.**

```bash
docker compose -f docker-compose.onprem.yml -f docker-compose.hardened.yml -f docker-compose.proxy.yml up -d
```

With `docker-compose.hardened.yml`, data lives in the named volume `clio_clio-data`.

## Check it worked

```bash
# Ready, through TLS, from another machine
curl -fsS https://clio.corp.example/readyz

# The bypass is closed: this must fail
curl -sS --max-time 5 http://clio.corp.example:8473/health && echo "REACHABLE - fix this"

# Port 80 redirects
curl -sI http://clio.corp.example/ | head -n 1

# Anonymous callers are refused, and learn nothing from /health
curl -s -o /dev/null -w '%{http_code}\n' https://clio.corp.example/api/collections   # 401
curl -s https://clio.corp.example/health                                             # {"status":"healthy"}

# Security headers, including HSTS
curl -sI https://clio.corp.example/health
```

The last one must show `Strict-Transport-Security`. If it does not, Clio did not learn
that the request was HTTPS, so check that `FORWARDED_ALLOW_IPS` matches the proxy.

**About → Check this deployment** in the app (or `GET /api/system/readiness`) reports
posture, models, storage and every outbound path the configuration allows.

## Network

**Inbound:** only the proxy's 443 and 80.

**Outbound:** only what you configure, and the readiness report lists it live. With
`OFFLINE_MODE=1` nothing else is attempted: no model downloads, no hosted providers, no
web fetching, and no telemetry in any mode.

| To | Why | When |
| --- | --- | --- |
| Your IdP | Token signing keys (cached for an hour) | `IDENTITY_PROVIDER=oidc` |
| Your model endpoint | Answers, and embeddings if not local | `AI_BASE_URL`, `EMBEDDING_BASE_URL` |
| Your Postgres | App metadata | `DB_BACKEND=postgresql` |
| Your git server | Cloning sources | `GIT_ALLOWED_HOSTS` |
| Your webhook | Change alerts | `ALERT_WEBHOOK_URL` |

**Outbound proxy:** set `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY` in `.env`. List internal
hosts in `NO_PROXY`, or traffic to your model endpoint will try to leave through the proxy.

**Your own proxy or load balancer:** the one in `docker-compose.proxy.yml` is a reference.
To use your own instead, leave that file out and have it:

- terminate TLS, and forward to Clio on a network nothing else can reach;
- send `X-Forwarded-For` and `X-Forwarded-Proto`, and **strip** any client copy of
  `X-Forwarded-User`;
- leave response buffering off (`proxy_buffering off`), or streamed answers arrive all at
  once, and allow long read timeouts (research answers can take minutes between bytes);
- allow large request bodies, up to your biggest upload.

On Clio, set `FORWARDED_ALLOW_IPS` to that proxy's address and `MCP_ALLOWED_HOSTS` to the
hostname. Without `FORWARDED_ALLOW_IPS`, rate limits see every password or anonymous caller
as the proxy, and they all share one budget.

If your SSO terminates at the proxy, it must let `/api/v1/*` through to Clio without an
interactive login, because apps authenticate there with their own credential.

## Health probes

| Path | Answers | Use for |
| --- | --- | --- |
| `/livez` | 200 while the process serves requests, touching nothing | Liveness: restart if this fails |
| `/readyz` | 200, or 503 with a reason code (`initializing`, `database`, `storage`, `timeout`) | Readiness, and the load balancer's health check |
| `/health` | Status for anyone. Counts and model state only for a caller who could see the rest of the app | The app's own status bar. Not a probe |

All three work with no credential. `/readyz` stays 200 while the search model is missing,
because search still works by keyword without it. The readiness report says when it is.

## Monitoring and logs

**Logs:** set `LOG_FORMAT=json` for one JSON object per line. Access lines carry
`http_method`, `http_path`, `http_status`, `duration_ms`, `user`, `auth_via` and
`request_id`, and cover `/api/`, `/documents`, `/search` and `/mcp`. Query strings are left
out. Containers log to stdout, so point your Docker logging driver at your SIEM.

**Metrics:** set `METRICS_ENABLED=true` and `METRICS_TOKEN=<token>`. That token opens
`/metrics` and nothing else. See [Monitoring](DEPLOYMENT.md#monitoring).

**Audit trail:** Admin → Audit trail, or `GET /api/admin/audit` (add `format=csv` to export).
It records uploads, deletions, shares, tokens, app changes and admin decisions, and agent and
app reads with `MCP_AUDIT_TOOL_CALLS=true` (on under the secure profile). It keeps
`AUDIT_RETENTION_DAYS`, 365 by default.

## Limits

Per identity, or per address for password and anonymous callers. Registered apps have
[budgets of their own](APP_API.md#registering-an-app). `0` means unlimited.

| Setting | Default | Covers |
| --- | --- | --- |
| `RATE_LIMIT_CHAT_PER_MINUTE` | 6 | Answers |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 60 | `/search` |
| `RATE_LIMIT_DOCUMENTS_PER_MINUTE` | 600 | `/documents/*`: uploads, listings, previews, job polling |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | 120 | The rest of `/api/` |
| `RATE_LIMIT_MCP_PER_MINUTE` | unlimited | `/mcp`, per token |

## What your security review will ask

- **Authentication:** OIDC, an authenticating proxy, or a shared password
  ([IDENTITY.md](IDENTITY.md)). Apps: per-app keys or IdP client credentials
  ([APP_API.md](APP_API.md)).
- **Secrets at rest:** credentials are encrypted with `SECRET_ENCRYPTION_KEY`. App keys and
  MCP tokens are stored as hashes only.
- **Data at rest:** Clio does not encrypt the data volume. Use encrypted storage on the host.
- **Container:** non-root, read-only root filesystem, no capabilities, process limit
  (`docker-compose.hardened.yml`).
- **Response headers:** `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, and HSTS over HTTPS. Override with `CONTENT_SECURITY_POLICY` and
  `HSTS_MAX_AGE_SECONDS`, or blank the first if your proxy sets its own.
- **Network:** the table above, generated live by the readiness report.

**Verifying the image.** From the first release built with these steps, published images
carry an SBOM and build provenance, and a keyless signature:

```bash
cosign verify ghcr.io/jordanboyce/clio:<version> \
  --certificate-identity-regexp '^https://github.com/jordanboyce/clio/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

docker buildx imagetools inspect ghcr.io/jordanboyce/clio:<version> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/jordanboyce/clio:<version> --format '{{ json .Provenance }}'
```

Run these where you can reach the registry and Sigstore's public services, before carrying
the image in. The dependency audit in `.github/workflows/dependency-audit.yml` runs weekly.

## Going live

- [ ] `CLIO_PROFILE=secure`, and `scripts/preflight.py --probe` is clean
- [ ] Every check under [Check it worked](#check-it-worked) gives the expected result
- [ ] `SECRET_ENCRYPTION_KEY` comes from your secret manager, and is backed up with the data
- [ ] `ADMIN_EMAILS` names real operators
- [ ] The image is pinned by digest, and its signature checked
- [ ] Backups of the data volume are running, and one has been restored
- [ ] `LOG_FORMAT=json` reaches your SIEM
- [ ] Each consuming app has its own registration, its own expiry, and only the collections it needs
- [ ] Someone owns rotating app keys and updating the image
