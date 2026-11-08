# Hosting Clio behind a corporate firewall

The guide for putting one Clio server on a corporate VM, behind TLS, on a network
that blocks most things by default, for people and for [registered apps](APP_API.md).
Follow it in order. It ends with a script that says whether it worked.

**Using an agent to do this?** Install [skills/clio-deploy](../skills/clio-deploy/SKILL.md)
and point it at this page. It adds the rules an agent must keep to on someone's network.

Going further: [ONPREM.md](ONPREM.md) for private models, [IDENTITY.md](IDENTITY.md) for
sign-in, [SECURE_PROFILE.md](SECURE_PROFILE.md) for the hardened profile,
[AIRGAP.md](AIRGAP.md) for no network at all.

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
                         |  only what you configure
           your IdP, model endpoint, Postgres (optional)
```

## Before you start

**You need:**

- A Linux VM with Docker and Compose 2.24 or later (`docker compose version`). It needs
  no compiler, no Python and no internet.
- A hostname, and a certificate and key for it from your internal CA.
- A way in for people: your IdP, or a long random password for a small team.
- Optional: an OpenAI-style model endpoint. Without one, search and citations work, and
  written answers do not.

**Which version:** this guide needs a release newer than **1.2.0**, which has no proxy
overlay, probes or app API. If the newest tag on the registry is still 1.2.0, build from
`master` with `package_image.sh` below. `check_deployment.sh` says so if the image is too old.

## Ask for these first

Firewall changes take the longest, so send these before anything else. Everything in
the second table is optional, and depends only on what you configure.

**Inbound to the VM**

| From | To | Port | Why |
| --- | --- | --- | --- |
| Users, consuming apps, agents | VM | 443/tcp | Clio over TLS |
| Users | VM | 80/tcp | Optional: redirects to 443 |
| Your scraper | VM | 443/tcp | Optional: `/metrics` |

Port **8473 is not opened to anything.** Clio publishes no port.

**Outbound from the VM.** With `OFFLINE_MODE=1` Clio attempts nothing else: no model
downloads, no hosted providers, no web fetching, and no telemetry in any mode. Clio needs
no internet access at all to run.

| To | Port | Why | Needed when |
| --- | --- | --- | --- |
| Your IdP | 443 | Token signing keys, cached for an hour | `IDENTITY_PROVIDER=oidc` |
| Your model endpoint | as served | Answers, and embeddings if not run locally | `AI_BASE_URL`, `EMBEDDING_BASE_URL` |
| Your Postgres | 5432 | App metadata | `DB_BACKEND=postgresql` |
| Your git server | 443 or 22 | Cloning sources | `GIT_ALLOWED_HOSTS` |
| Your webhook | as served | Change alerts | `ALERT_WEBHOOK_URL` |

The running deployment reports its own list: **About → Check this deployment**, or
`GET /api/system/readiness`, under `egress`.

**To install by pulling rather than carrying files**, the VM's Docker daemon also needs
`ghcr.io` and `pkg-containers.githubusercontent.com` on 443 (or your internal registry
mirroring `ghcr.io/jordanboyce/clio`), and Docker Hub for the nginx image. Nothing at
run time needs `huggingface.co`: the search model and reranker are inside the image.
Only choosing a different local model later downloads one. If you will, and your
network has a Hugging Face mirror (an Artifactory or Nexus remote repository), set
`HF_ENDPOINT` to it and put its host in `NO_PROXY`; otherwise copy the model in as
[AIRGAP.md](AIRGAP.md) describes.

## Get the install across

**If the VM can reach a registry**, get these files from the repository, keeping their
paths: everything listed in [scripts/bundle_files.txt](../scripts/bundle_files.txt). Then set
`CLIO_IMAGE=ghcr.io/jordanboyce/clio:<version>-slim` and `CLIO_PULL_POLICY=missing` in the
next step, and `PROXY_PULL_POLICY=missing` is already the default.

**If it cannot**, build a bundle on a connected machine with Docker:

```bash
./scripts/package_image.sh --slim --with-proxy-image --tag 1.3.0 --out /media/transfer
```

```powershell
.\scripts\package_image.ps1 -Slim -WithProxyImage -Tag 1.3.0 -Out D:\transfer
```

`--slim` is about 1.5 GB against 4 GB, with search, the reranker, chat and MCP. Leave it
out if you need OCR of scanned PDFs or audio transcription. Add `--offline-bundle` for
Docling OCR models, which cannot be fetched later.

The bundle holds both images, checksums, the compose files, the nginx config, this guide
and the agent skills. Carry the whole directory across, then on the VM:

```bash
cd <bundle>
sha256sum -c clio-1.3.0.tar.gz.sha256 proxy-image.tar.gz.sha256
docker load < clio-1.3.0.tar.gz
docker load < proxy-image.tar.gz
```

To check an image's signature, SBOM and provenance, see [Verifying the image](#verifying-the-image).

## Configure

Run the rest from the bundle directory, or a checkout of the repository.

```bash
cp .env.onprem.example .env
chmod 600 .env
```

Edit `.env`. At minimum:

```bash
CLIO_IMAGE=clio:1.3.0
CLIO_PULL_POLICY=never
PROXY_PULL_POLICY=never
CLIO_HOSTNAME=clio.corp.example
CLIO_PROFILE=secure
OFFLINE_MODE=1

# Your own model endpoint. Leave these empty for search only.
AI_PROVIDER=openai_compatible
AI_BASE_URL=https://llm.corp.example/v1
AI_MODEL=your-model

# Who gets in: your IdP...
IDENTITY_PROVIDER=oidc
AUTH_REQUIRE_IDENTITY=true
OIDC_ISSUER=https://sso.corp.example/realms/main
OIDC_AUDIENCE=clio
ADMIN_EMAILS=you@corp.example
# ...or a shared password for a small team:
# AUTH_PASSWORD=<openssl rand -base64 32>

# Encrypts stored credentials. Keep it in your secret manager: a restored backup
# cannot read them without it.
SECRET_ENCRYPTION_KEY=<openssl rand -base64 32>

# One JSON object per log line, for your SIEM.
LOG_FORMAT=json
```

`IDENTITY_PROVIDER` on its own turns nobody away: it says how to tell who a caller is.
Keep `AUTH_REQUIRE_IDENTITY=true` with it, or set `PRIVATE_COLLECTIONS=true`.

**An outbound proxy**, if traffic must use one:

```bash
HTTP_PROXY=http://proxy.corp.example:3128
HTTPS_PROXY=http://proxy.corp.example:3128
# Everything internal stays off the proxy. Leave out nothing Clio calls directly.
NO_PROXY=localhost,127.0.0.1,clio-upstream,.corp.example
```

**Certificates:**

```bash
mkdir -p certs/tls certs/ca
cp <full chain> certs/tls/server.crt
cp <private key> certs/tls/server.key
chmod 600 certs/tls/server.key
# Any internal CA that signs your IdP or model endpoint. If an outbound proxy
# inspects TLS, the CA it re-signs with goes here too.
cp corp-root-ca.crt certs/ca/
```

## Start it

Check the configuration first, using your image's name. This changes nothing, and `--probe`
also contacts the model endpoint:

```bash
docker run --rm --env-file .env clio:1.3.0 python scripts/preflight.py --probe
```

Tell Compose which files make up this deployment, by adding one line to `.env`. Every later
`docker compose` command in this directory then finds the stack on its own. If you have **no
private CA** in `certs/ca/`:

```bash
COMPOSE_FILE=docker-compose.onprem.yml:docker-compose.hardened.yml:docker-compose.proxy.yml
```

**With a private CA**, leave out the hardened overlay, or build a derived image as in
[Hardened container](DEPLOYMENT.md#hardened-container). The overlay runs as an unprivileged
user from the first instruction, so it cannot install CAs at start:

```bash
COMPOSE_FILE=docker-compose.onprem.yml:docker-compose.proxy.yml
```

Then start it:

```bash
docker compose up -d
docker compose ps
```

With the hardened overlay, data is in the Docker volume `clio_clio-data`. Without it, it is
in `DATA_PATH` (`./data` by default).

## Check it worked

From **another machine** on the network, because a check from the VM itself cannot show that
a firewall rule works. `scripts/check_deployment.sh` needs only `sh` and `curl`:

```bash
sh scripts/check_deployment.sh --cacert corp-root-ca.crt https://clio.corp.example
```

Exit 0 means nothing failed. It checks that the server is reachable and ready, that TLS is
understood end to end, that security headers arrive, that anonymous callers are refused, and
that Clio's own port is closed. Set `CLIO_CHECK_TOKEN` to the password to add the
deployment's own readiness report. A failed check says what to do; the table below has more.

Then open `https://clio.corp.example`, and **About → Check this deployment** for posture,
models, storage and every outbound path the configuration allows.

## Connect apps and agents

- **Applications:** an admin registers each one in **Admin → Registered apps**.
  See [APP_API.md](APP_API.md). If your SSO fronts Clio, it must let `/api/v1/*` through
  without an interactive login, because apps authenticate there with their own credential.
- **Agents that use Clio:** connect over MCP at `https://clio.corp.example/mcp`
  ([how](DEPLOYMENT.md#mcp-clients)), or install [skills/clio](../skills/clio/SKILL.md).
  The machine they run on must trust your CA. `NODE_EXTRA_CA_CERTS=/path/to/ca.pem` covers
  Node-based clients and `SSL_CERT_FILE=/path/to/ca.pem` covers Python ones. Do not switch
  certificate checks off instead.

## When something is wrong

Read the logs first: `docker compose logs clio`, `docker compose logs proxy`.

| Symptom | Cause | Fix |
| --- | --- | --- |
| `invalid ... !reset` or `unknown tag` from Compose | Compose older than 2.24 | Upgrade Docker Compose |
| `container name "/clio" is already in use` | An older install on this VM | It may hold data. Check it with `docker ps -a` and `docker inspect clio`, and decide before removing anything |
| `required variable CLIO_HOSTNAME is missing` | Not set in `.env` | Set it, and run Compose from the directory holding `.env` |
| `CLIO_PROFILE=secure refuses to start` in the log | Exactly what is listed under it | Fix what it names. Do not switch profile to get past it |
| `Pool overlaps with other one` or `Address already in use` | `172.30.73.0/29` is taken on this network | Set `PROXY_SUBNET`, `PROXY_IP` and `CLIO_PROXY_NET_IP` |
| `cannot load certificate` in the proxy log | Missing, misnamed or unreadable file | It must be `certs/tls/server.crt` and `server.key` |
| `pull access denied` or a timeout on start | The image is not on the host | Load it, and set `CLIO_PULL_POLICY=never` and `PROXY_PULL_POLICY=never` |
| Timeout from another machine, but `curl` on the VM works | The firewall rule is not in place | Ask for it, using the table above |
| 502 from nginx | Clio is still starting or has stopped | `docker compose ps`, `docker compose logs clio` |
| `CERTIFICATE_VERIFY_FAILED` in Clio's log, or sign-in fails for everyone | Clio does not trust your IdP's or model's CA | Add it to `certs/ca/` and restart. Under the hardened overlay, see [above](#start-it) |
| `WARNING: started as a non-root user, cannot install CA certificates` | A CA in `certs/ca/` with the hardened overlay | Leave out the overlay, or build a derived image |
| Everything fails only through the outbound proxy, with TLS errors | The proxy inspects TLS | Add the CA it re-signs with to `certs/ca/` |
| The model endpoint times out, though the VM can reach it | The call is going to the outbound proxy | Add its host to `NO_PROXY` |
| `Offline mode` or "disabled" errors when connecting a hosted provider | `OFFLINE_MODE=1` | As intended. Use your own endpoint, or decide to allow it |
| Search says the model is not available (503) | The search model is not on disk, or `EMBEDDING_MODEL` was changed | Use the image's default model. Search still works by keyword ([what each one costs](AIRGAP.md#when-a-model-or-provider-is-out-of-reach)) |
| `check_deployment.sh`: no HSTS header | Clio does not trust the proxy's forwarding headers | With your own proxy, set `FORWARDED_ALLOW_IPS` to its address. The bundled proxy sets this for you |
| Everyone hits 429 at once | Rate limits see one address for everybody, the same cause as above | The same fix |
| 421 on `/mcp` | The hostname is not allowed | Set `CLIO_HOSTNAME`, or `MCP_ALLOWED_HOSTS` with your own proxy |
| Claude Desktop or ChatGPT cannot find where to sign in | Set the public address | `MCP_PUBLIC_URL=https://clio.corp.example/mcp` ([details](IDENTITY.md#oauth-for-mcp-clients)) |
| 413 on upload | Over the proxy's limit, or the collection's | Raise `CLIO_MAX_UPLOAD`, or `COLLECTION_STORAGE_LIMIT_BYTES`. The message names which |
| Answers arrive all at once, or cut off at about a minute | Another proxy on the way is buffering, or timing out | Turn off response buffering there, and raise its read timeout to 15 minutes |
| `/readyz` says `database` | Postgres is unreachable, or `app.db` cannot be opened | The log says which |
| `/readyz` says `storage` | The data directory is not writable by uid 10001 | Use the named volume, or `chown -R 10001:10001` the folder |
| 401 for one app, others fine | Revoked, expired, or a rotated key past its grace | Admin → Registered apps |

## Your own proxy or load balancer

The nginx in `docker-compose.proxy.yml` is a reference. To use your own, leave that file out
and have your proxy:

- terminate TLS, and forward to Clio on a network nothing else can reach;
- send `X-Forwarded-For` and `X-Forwarded-Proto`, and **strip** any client copy of
  `X-Forwarded-User`;
- leave response buffering off, and allow read timeouts of 15 minutes, because research
  answers can take minutes between bytes;
- allow request bodies up to your largest upload.

On Clio, set `FORWARDED_ALLOW_IPS` to that proxy's address, `MCP_ALLOWED_HOSTS` to the
hostname, and `BIND_ADDRESS` to anything but loopback, so the secure profile judges the
exposure that is really there. `check_deployment.sh` works the same against it.

## Health probes

| Path | Answers | Use for |
| --- | --- | --- |
| `/livez` | 200 while the process serves requests, touching nothing | Liveness: restart if this fails |
| `/readyz` | 200, or 503 with a reason code (`initializing`, `database`, `storage`, `timeout`) | Readiness, and the load balancer's health check |
| `/health` | Status for anyone. Counts and model state only for a caller who could see the rest of the app | The app's own status bar. Not a probe |

All three work with no credential. `/readyz` stays 200 while the search model is missing,
because search still works by keyword without it. The readiness report says when it is.

## Monitoring and logs

**Logs:** with `LOG_FORMAT=json`, access lines carry `http_method`, `http_path`, `http_status`,
`duration_ms`, `user`, `auth_via` and `request_id`, and cover `/api/`, `/documents`, `/search`
and `/mcp`. Query strings are left out. Containers log to stdout, so point your Docker logging
driver at your SIEM.

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

## Upgrading

Back up the data volume, then get the new image across the same way. Point `CLIO_IMAGE` at
it in `.env`, and:

```bash
docker compose up -d
sh scripts/check_deployment.sh --cacert corp-root-ca.crt https://clio.corp.example
```

`docker compose down -v` deletes the volume, and every document with it. A plain `up -d`
replaces the container and keeps the data.

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
- **Network:** the tables above, and the live list in the readiness report.

### Verifying the image

From the first release built with these steps, published images carry an SBOM and build
provenance, and a keyless signature. Run these before carrying the image in, somewhere that
can reach the registry and Sigstore's public services:

```bash
cosign verify ghcr.io/jordanboyce/clio:<version> \
  --certificate-identity-regexp '^https://github.com/jordanboyce/clio/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com

docker buildx imagetools inspect ghcr.io/jordanboyce/clio:<version> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/jordanboyce/clio:<version> --format '{{ json .Provenance }}'
```

A bundle you built yourself with `package_image.sh` is not signed: its checksums show that it
arrived intact, and your own build process is what vouches for it. The dependency audit in
`.github/workflows/dependency-audit.yml` runs weekly.

## Going live

- [ ] `CLIO_PROFILE=secure`, and `scripts/preflight.py --probe` is clean
- [ ] `scripts/check_deployment.sh` passes from another machine
- [ ] `SECRET_ENCRYPTION_KEY` comes from your secret manager, and is backed up with the data
- [ ] `ADMIN_EMAILS` names real operators
- [ ] The image is pinned by digest or version, and its origin checked
- [ ] Backups of the data volume are running, and one has been restored
- [ ] `LOG_FORMAT=json` reaches your SIEM
- [ ] Each consuming app has its own registration, its own expiry, and only the collections it needs
- [ ] Someone owns rotating app keys and updating the image
