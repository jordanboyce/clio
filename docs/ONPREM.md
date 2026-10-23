# On-Premises Deployment

Clio ships as one portable container image. Build it once, carry it to
wherever it has to run, and configure it there: the model endpoint,
credentials, CA certificates and data all arrive at run time, so the bytes
that run in production are the bytes that were reviewed.

This guide covers running it against **your own models and endpoints**. For a
fully disconnected network, read this first and then
[AIRGAP.md](AIRGAP.md), which covers baking models into the image and
verifying zero egress. For remote access and identity, see
[DEPLOYMENT.md](DEPLOYMENT.md).

---

## 1. Get the image

**With registry access**

```bash
docker pull ghcr.io/jordanboyce/clio:latest
```

**Without** — build on a connected machine and carry the result in:

```bash
./scripts/package_image.sh --tag 1.0.0 --out /media/transfer
#   Windows:  .\scripts\package_image.ps1 -Tag 1.0.0 -Out D:\transfer
```

That produces a directory holding the image archive, its checksum, the
compose file, the example configuration and these guides. On the target
machine:

```bash
sha256sum -c clio-1.0.0.tar.gz.sha256
docker load < clio-1.0.0.tar.gz
```

Add `--offline-bundle` when the target will never reach the internet: it
bakes the Whisper and local-OCR models in, because those cannot be fetched
later (the embedding model and the search reranker are in every image
already).

Building yourself is equally fine — `docker compose build` — but a site with
no compiler, no internet and a change-control process usually wants the
file.

## 2. Configure it

```bash
cp .env.onprem.example .env
$EDITOR .env
```

The settings that matter on-prem:

| Setting | What it does |
|---|---|
| `CLIO_IMAGE` | The image to run — the tag you loaded, or a digest |
| `AI_PROVIDER` | `openai_compatible`, `ollama`, or a hosted provider |
| `AI_BASE_URL` | Your endpoint, e.g. `http://vllm.internal:8000/v1` |
| `AI_MODEL` | The model to ask for |
| `AI_API_KEY` | Credential for the endpoint (may be empty) |
| `AI_PROVIDER_LABEL` | What people see in the UI instead of a provider id |
| `OFFLINE_MODE` | `1` refuses cloud providers and all model downloads |
| `AUTH_PASSWORD` | Shared secret; required if the port is reachable |
| `DATA_PATH` | Where the corpus lives on this host |

`AI_*` is what makes this an appliance rather than a bring-your-own-key app.
Set it and **nobody configures a model in their browser**: chat, report
generation, OCR of scanned pages and images, and the content-policy reviewer
all use your endpoint, and Settings → AI Providers shows it as connected,
labelled, with no key or URL to enter. Someone can still bring their own key
for a different provider; a per-request key overrides the deployment's.

Leave `AI_*` empty and nothing changes from the laptop behaviour — each
browser configures its own provider.

## 3. Start it

```bash
docker compose -f docker-compose.onprem.yml up -d
docker compose -f docker-compose.onprem.yml logs -f clio
```

Then open `http://<host>:8473`.

The log line to look for is the one naming your endpoint:

```
Deployment AI provider: openai_compatible at http://vllm.internal:8000/v1 (model llama-3.3-70b-instruct)
```

A typo fails at startup rather than at someone's first question: an unknown
`AI_PROVIDER`, or `openai_compatible` without `AI_BASE_URL`, refuses to
boot.

---

## Endpoints that are known to work

Anything speaking the OpenAI chat-completions shape. `AI_BASE_URL` is the
root the `/chat/completions` route hangs off — include `/v1` when your server
serves it there.

| Server | `AI_BASE_URL` | Notes |
|---|---|---|
| vLLM | `http://vllm.internal:8000/v1` | `AI_MODEL` = the `--served-model-name` |
| Ollama | `http://ollama.internal:11434/v1` | or `AI_PROVIDER=ollama` with `OLLAMA_BASE_URL` for the native API |
| llama.cpp (`llama-server`) | `http://llama.internal:8080/v1` | `AI_API_KEY` empty unless `--api-key` was set |
| LM Studio | `http://lmstudio.internal:1234/v1` | enable the local server in its UI |
| Text Generation Inference | `http://tgi.internal:8080/v1` | |
| LiteLLM proxy | `http://litellm.internal:4000` | one endpoint in front of several models |
| NVIDIA NIM | `http://nim.internal:8000/v1` | |
| Azure OpenAI | via LiteLLM or a gateway | the native Azure route shape differs |

`AI_PROVIDER=ollama` uses Ollama's native API instead of its OpenAI shim.
Either works; the native path also sets the context window explicitly
(`OLLAMA_NUM_CTX`), which matters because Ollama otherwise truncates long
retrieved context silently.

**Reaching the endpoint from inside the container.** `localhost` inside a
container is the container. Use the service's real hostname, or
`host.docker.internal` for a service on the Docker host itself — the compose
files map it on every platform.

## Private TLS

If your endpoint is served with a certificate from an internal CA, drop the
CA's `.crt` (root, and any intermediates) into `certs/ca/`. The entrypoint
installs them on every start, into both the system trust store and certifi's
bundle — the Python HTTP clients use the latter, so the system store alone
would leave you with `CERTIFICATE_VERIFY_FAILED` from an endpoint that
`curl` reaches fine.

```bash
cp corp-root-ca.crt certs/ca/
docker compose -f docker-compose.onprem.yml restart clio
docker compose -f docker-compose.onprem.yml logs clio | grep "installing CA"
```

This is a *runtime* mount, deliberately: it keeps one image usable at every
site. (Building your own image instead? `certs/ca/` is baked in at build
time too, so either path works.)

Self-signed certificates on the endpoint itself work the same way — add the
certificate, not an exception; there is no "skip TLS verification" switch,
and adding one would be the wrong thing to ship.

## Embeddings stay local

`EMBEDDING_PROVIDER=local` runs `all-MiniLM-L6-v2` inside the container on
CPU: document text and queries become vectors without leaving the machine.
That model is baked into the image, so it works with no network at all.

Settings → Indexing → Embedding can point at a hosted embedding API or your
own OpenAI-compatible `/embeddings` endpoint
(`EMBEDDING_PROVIDER=openai_compatible` + `EMBEDDING_BASE_URL`). Two things
to know: hosted providers see your document text at index time, and
`OFFLINE_MODE=1` refuses them; and changing the provider or model means
re-indexing every collection, since the vectors are not comparable across
models.

## Git repositories and change alerts

Point Clio at your internal git server and it keeps a collection current and
tells people when the code or documents change:

```bash
GIT_ALLOWED_HOSTS=git.corp.example          # the only hosts that may be cloned
GIT_HTTP_TOKEN=<read-only token>            # https, sent only to allowlisted hosts; or mount an ssh key for ssh:// and git@host: addresses
ALERT_WEBHOOK_URL=https://chat.corp.example/hooks/clio   # optional
ALERT_WEBHOOK_SECRET=<shared secret>                     # signs each POST (X-Clio-Signature)
```

`OFFLINE_MODE=1` normally refuses remote clones; an allowlisted host is the
exception, so an air-gapped site can still follow a repository on its own
network. Local checkouts need none of this: add the folder and Clio reads it
with `git ls-files`, so `.gitignore` is respected. Clones are read-only,
shallow and live under `DATA_DIR/repos`. Turn on **Watch** for a source in the
Sources panel and every real change appears in the header's Updates bell, the
MCP `list_changes` tool and, if configured, the webhook. The image includes
`git` and the ssh client.

## Who can reach it

There is no per-user isolation by default: everyone who can reach the port
sees the whole corpus, which is the right model for a team appliance and the
wrong one for a multi-tenant service. Pick one:

- **AUTH_PASSWORD** — a shared secret, prompted natively by browsers, sent as
  a bearer token by MCP clients. The minimum whenever the port is reachable
  beyond the host.
- **An authenticating reverse proxy** in front (your SSO, mTLS, whatever the
  site already runs). Keep `BIND_ADDRESS=127.0.0.1` so the proxy is the only
  route in.
- **`PRIVATE_COLLECTIONS=true`** for per-person ownership and sharing. It
  needs a verified identity on every request: point `IDENTITY_PROVIDER` at
  your own IdP (`oidc` — Keycloak, Entra ID, Okta, PingFederate) or at the
  proxy that already authenticates for you (`trusted_header`, which is how
  mTLS and Kerberos front ends attach). See [IDENTITY.md](IDENTITY.md).

## Agents over MCP

The embedded MCP endpoint at `/mcp` lets internal agents search and read the
corpus. On-prem, mint personal tokens in Settings → MCP (read-only unless you
allow writes), and if you serve the app under a hostname rather than
localhost, list it in `MCP_ALLOWED_HOSTS` — the MCP SDK's DNS-rebinding
protection trusts only localhost otherwise.

A token carries the identity of whoever minted it, so under
`PRIVATE_COLLECTIONS` an agent sees exactly that person's collections. Set
`MCP_AUDIT_TOOL_CALLS=true` where reads have to be accountable: every tool
call is then recorded with the credential, collection and documents it
touched — see [IDENTITY.md](IDENTITY.md).

Clients that take a bearer header (Claude Code, Codex, VS Code, AnythingLLM)
attach with the config generated in Settings → MCP. Connector-style clients
(Claude Desktop, claude.ai, ChatGPT, and Claude Code without a token) sign
in at your IdP instead: with `IDENTITY_PROVIDER=oidc` set
`MCP_PUBLIC_URL=https://clio.internal/mcp` and hand people that URL.
The app is only the OAuth resource server; the IdP does the sign-in and
registers the clients. Setup per IdP, the scope option and what is
verified: [IDENTITY.md](IDENTITY.md#oauth-for-mcp-clients).

## Upgrading

```bash
docker load < clio-1.1.0.tar.gz          # or docker pull
# point CLIO_IMAGE at the new tag in .env
docker compose -f docker-compose.onprem.yml up -d
```

Data lives in the volume, not the image, so the container is disposable.
Roll back by pointing `CLIO_IMAGE` at the previous tag. Index formats
are forward-compatible within a major version; a release that needs a
re-index says so.

Back up `DATA_PATH` — it holds the documents, the indexes, the app database
and the audit trail. Stop the container first, or accept that a copy taken
mid-write may need SQLite recovery.

## Checklist

- [ ] Image loaded and pinned by tag or digest (`docker inspect` shows the
      version and commit labels)
- [ ] `AI_PROVIDER` / `AI_BASE_URL` / `AI_MODEL` set, and the startup log
      names the endpoint you expect
- [ ] A question in the UI gets an answer with citations
- [ ] `AUTH_PASSWORD` set, or an authenticating proxy in front
- [ ] `certs/ca/` populated if the endpoint uses a private CA
- [ ] `OFFLINE_MODE=1` if this deployment must not reach the internet
      (verify: `/health` reports `"offline_mode": true`)
- [ ] `DATA_PATH` on storage that is backed up
- [ ] Storage cap and rate limits reviewed for the size of the group
