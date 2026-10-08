# Deploying Clio for a Team

By default Clio is a **shared team appliance**. Everyone who can reach it
sees the whole corpus — every collection, every document, every table, and the
same `/mcp` knowledge surface (subject to restricted-source handling). This
default does not isolate users. Choose private collections when users should
have different access; choose shared-appliance mode only for a trusted group.

Deployments fronted by Cloudflare Access can additionally opt into
[private collections](#private-collections) — per-person ownership and
sharing, enforced at every entry point against the verified Access identity.

## The security model in one paragraph

The app has no login of its own. Access is controlled at the edge by whatever
you put in front of it, and the app is bound so that the edge is the only route
in. Nothing else is required — no accounts to provision inside Clio, no
password resets, no per-user configuration.

| Setting | Default | What it controls |
|---|---|---|
| `HOST` | `127.0.0.1` | Which interfaces accept connections. Docker sets `0.0.0.0`; there the published port is the boundary. |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface published by Docker Compose. Existing LAN deployments must explicitly set this when upgrading. |
| `AUTH_PASSWORD` | empty | A shared secret required on every request except `/health`. Use it when the app stays reachable on a network your proxy does not exclusively own. |
| `CORS_ALLOW_ORIGINS` | empty | Which *other* web origins may read responses in a browser. Empty is correct unless a separate web app calls the API. |

The app warns at startup if it is bound to the network with no password, and
refuses to start with `ENABLE_MULTI_USER=true` (see
[Private collections](#private-collections) for the supported replacement).

## Choose a deployment shape

### A. Just you, on your machine

Python binds to `HOST=127.0.0.1`. Docker Compose publishes to
`BIND_ADDRESS=127.0.0.1` even though the process inside the container listens on
all container interfaces. Neither default exposes the app to your LAN.

```bash
docker compose up -d     # or: python main.py
```

### B. A team, via SSO — recommended

Put an identity-aware proxy in front and let it decide who gets in. Your
colleagues are already signed into Google/Okta/Entra, so for them access is
just opening the URL — no password to type, share, or rotate. You get
per-person revocation, MFA, and an access log without writing any code.

Good options: **Cloudflare Access**, **Tailscale** (with `tailscale serve`),
**oauth2-proxy**, **Authelia**, **Pomerium**.

The critical part is step 2 — the proxy must be the *only* route in. A proxy
you can walk around protects nothing.

**1. Keep Clio off the public network.** With compose, drop the `ports:`
mapping so the container is reachable only on the internal Docker network:

```yaml
services:
  clio:
    # ports:              # ← removed: no direct access from the host
    #   - "8473:8473"
    expose:
      - "8473"
    environment:
      - HOST=0.0.0.0      # inside the container; the network is the boundary
```

**2. Put the proxy on that network** and point it at `http://clio:8473`.
Only the proxy publishes a port.

**3. Verify the bypass is actually closed.** From another machine, try to reach
the app directly, not through the proxy:

```bash
curl -sS --max-time 5 http://<host>:8473/health && echo "REACHABLE — fix this"
```

That must fail. If it succeeds, the app is exposed and your SSO is decorative.

**Optional — belt and braces.** If the app must stay reachable on a network the
proxy does not exclusively own, set `AUTH_PASSWORD` and have the proxy inject
it, so requests that skip the proxy are rejected:

```
Authorization: Bearer <AUTH_PASSWORD>
```

Configure this on the proxy, not in a browser. If you set `AUTH_PASSWORD`
*without* injecting it, people get a Basic-auth popup on top of their SSO
login — two prompts for one door.

### C. A team, via private network

Tailscale, WireGuard, or a corporate VPN. The network is the boundary: bind to
the private interface and skip `AUTH_PASSWORD` entirely. Simplest option if
everyone is already on the VPN, though you get no per-request audit trail.

### C2. On-premises appliance, own models

Shape C plus a model you run: the image is loaded from a file or an internal
registry, `AI_PROVIDER` / `AI_BASE_URL` / `AI_MODEL` point the whole
deployment at your endpoint, and nobody configures a provider in a browser.
Combine with `OFFLINE_MODE=1` when the deployment must not reach the
internet at all. Use `docker-compose.onprem.yml`; the walkthrough is
[ONPREM.md](ONPREM.md), and the disconnected specialisation is
[AIRGAP.md](AIRGAP.md).

For per-person ownership here — no Cloudflare in the path — set
`IDENTITY_PROVIDER=oidc` against your own IdP, or `trusted_header` behind the
proxy that already authenticates ([IDENTITY.md](IDENTITY.md)). Shape C's "no
per-request audit trail" caveat goes away with it: events get a real actor,
and `MCP_AUDIT_TOOL_CALLS=true` extends that to what agents read.

### D. Public URL with a shared password

`AUTH_PASSWORD` alone, no proxy. Browsers prompt natively (HTTP Basic, any
username); API and MCP clients send `Authorization: Bearer <password>`.

Fine for a demo or a short-lived deployment. Not great for a standing team
tool: one password everyone shares, no way to revoke one person, and no record
of who did what. Prefer **B**.

## MCP clients

`/mcp` is part of the same app, so it sits behind the same door — with one
wrinkle: **MCP clients authenticate as a token, not as a person.**

- **Personal access tokens (recommended for people).** Settings → MCP →
  Personal access tokens lets anyone who can already reach the app — a
  browser session, SSO or `AUTH_PASSWORD` — mint their own bearer token for
  Claude Code, Codex, or GitHub Copilot without touching Cloudflare's
  dashboard or the server's `.env`. The tab hands back ready-to-paste config
  for each client with the token already filled in. A token only ever works
  against `/mcp`; it cannot reach the rest of the API or the UI, and it's
  revocable per-token from the same screen. Under
  [private collections](#private-collections) it carries the identity of
  whoever created it, so it sees exactly that person's collections.
  Tokens are **read-only by default**; tick *Allow adding and updating
  sources* when generating one to let the agent use the `write_document`
  tool (save notes, summaries, markdown into a collection). A write still
  needs write permission on the collection, passes the content policy scan,
  is attributed to the token's owner, and lands in the audit trail.
  Two more knobs at mint time: an **expiry** (30 days, 90 days, a year, or
  never — the form defaults to 90 days; an expired token is refused exactly
  like a revoked one) and **Limit this token to specific collections**,
  which makes the ticked collections the *only* ones the agent can see over
  MCP, whatever their sensitivity — the right shape for a client that works
  on one project. Without that limit a token sees everything its owner can,
  plus any restricted collections it was explicitly granted. The table shows
  each token's expiry, last use and call count, so idle or runaway agents are
  easy to spot and revoke.
- With `AUTH_PASSWORD` alone (no personal token), clients send
  `Authorization: Bearer <password>` and work normally, but anonymously —
  see below.
- **Sign in from the client (OAuth).** Claude Desktop, claude.ai, ChatGPT
  and Claude Code (`claude mcp login`) can attach with just the endpoint
  URL and a browser sign-in at your identity provider — no token to paste
  or revoke. It needs an authorization server for `/mcp`: an `oidc`
  deployment has one already (set `MCP_PUBLIC_URL`); a Cloudflare Access
  deployment names one with `MCP_OAUTH_ISSUER` + `MCP_OAUTH_AUDIENCE`, and
  the edge must let `/mcp` and `/.well-known/oauth-protected-resource`
  through (the provisioning script does). The token resolves to the same
  person a browser session would. Details, per-IdP client registration and
  the optional required scope: [IDENTITY.md](IDENTITY.md#oauth-for-mcp-clients).
- With an SSO proxy and no personal token, a headless MCP client has no
  browser to complete the login in. Either use a proxy that issues service
  tokens (Cloudflare Access does — see `docs/REMOTE-ACCESS.md` for a script
  that provisions one), or reach the app over the private network from **C**.

Whatever the client, it gets the same full-corpus access as everyone else —
unless [private collections](#private-collections) are on, where a personal
token or a service token's `common_name` is an identity that collections can
be shared to, and a password-authenticated client reaches team collections
only.

## Private collections

`PRIVATE_COLLECTIONS=true` turns per-person ownership on. It exists because
its predecessor — an `ENABLE_MULTI_USER` flag that filtered only the
collection *list* while search, documents, chat, and every `/mcp` tool acted
on any `collection_id` unchecked — advertised an isolation boundary without
being one. That flag still refuses to start; this mode is the real version of
what it pretended to be.

**It requires a verified identity source.** Ownership enforced against an
identity anyone can forge is worthless, so the app refuses to start with the
flag on and no source configured. Set `IDENTITY_PROVIDER` to one of:

- `cloudflare_access` — `CF_ACCESS_TEAM_DOMAIN` + `CF_ACCESS_AUD`, deployment
  shape **B**. The Access JWT the edge attaches to every request is the
  identity: a person's email for SSO logins, a service token's name for MCP
  clients. Selected automatically when those two are set.
- `oidc` — a bearer JWT from your own identity provider (Keycloak, Entra ID,
  Okta, PingFederate). The answer for shapes **C** and **C2**, where there is
  no Cloudflare in the path.
- `trusted_header` — an authenticating reverse proxy (mTLS terminator,
  Kerberos front end, site SSO proxy) has already established who the caller
  is and passes it in a header.

Full configuration, and the guards each one needs, in
[IDENTITY.md](IDENTITY.md).

What changes when it is on:

- **Every collection has an owner** — the identity that created it. A
  collection is visible only to its owner until shared: the collection list,
  search, chat retrieval (including "search all collections"), document
  serving, and every `/mcp` tool all enforce it. An inaccessible collection
  id answers exactly like a missing one, so ids cannot be probed.
- **Everything that existed before stays shared.** Collections owned by
  `default` — all pre-existing data, including the default collection — form
  a *team* tier that everyone can see and write. Flipping the flag on changes
  nothing about existing data; new collections are simply private to their
  creators.
- **Sharing is built in.** An owner creates a share link (read or readwrite,
  optional expiry) from the collection's ⋮ menu; the recipient pastes the
  token in the same dialog to accept. Shares are revocable, and deletion
  stays owner-only.
- **Three tiers, not two.** `owner` decides how a collection is *built*:
  re-indexing, chunk size, embedding model, the guide, the sensitivity
  label, publishing and deletion. `readwrite` is a *contributor* — it adds
  and removes sources, and nothing else; re-indexing on an owner's behalf
  would rewrite their index under them, so that authority never leaves the
  owner. `read` consumes: search, chat, MCP. The gate is
  `api/deps.require_collection_access(..., write=True | owner=True)`; a new
  endpoint that reconfigures a collection must pass `owner=True`.
- **Publishing releases a reviewed collection to everyone, read-only.** An
  owner ticks "Share with everyone, read-only" in the collection's edit
  dialog. From then on every signed-in person can search it, ask about it
  and reach it from their AI tools over MCP, while the owner alone keeps
  the ability to add sources or change how it is built. This is the shape
  for material that has been reviewed and released — a handbook, a policy
  set, a reference corpus — where wide reach and a single accountable
  maintainer both matter. Two refusals guard it: a `restricted` sensitivity
  label is a boundary (the same rule that blocks sharing), and a *team*
  collection cannot be published, because it has no single owner to keep
  that authority — publishing it would lock it with nobody able to unlock
  it. An explicit share still wins over publication, so a collaborator with
  a readwrite share keeps it.
- **Anyone who can read a collection can clone it.** `POST
  /api/collections/{id}/clone` copies its sources into a new collection
  owned by the caller, inheriting chunk size, embedding model, guide and
  sensitivity, and starts a background indexing job. The copy is never
  published. This is the answer to "I need this corpus chunked differently":
  take your own copy rather than edit someone else's. The copy counts
  against the caller's storage cap, and the whole transfer is checked
  before any bytes move.
- **MCP clients map to identities.** A personal access token (see
  [MCP clients](#mcp-clients)) carries the identity of whoever generated it —
  no separate name to share collections to. An Access service token instead
  authenticates as itself, and its `common_name` is the identity to share a
  collection to. A client authenticating with `AUTH_PASSWORD` alone has no
  identity and sees team collections only, as does any password-authenticated
  browser session.

Two deliberate limits, so the boundary stays honest: password callers cannot
own or accept anything (no identity), and if you need a harder wall than
application-level checks — different compliance regimes, different tenants —
run a second instance. Separate instances remain a stronger boundary than any
in-app flag.

## Online registration

Admission is admin-push by default: someone shares a collection or types an
address into Admin → Access, and the app adds it to the Cloudflare Access
policy. `REGISTRATION_MODE` adds the pull side — a public page at
`<your-host>/register` where a person asks for a seat:

| Mode | What happens |
|---|---|
| `off` (default) | The page says registration is closed and points at sign-in. |
| `approval` | The request waits in Admin → Access → *Registration requests*; approving it admits the address at the edge (and emails the person when `RESEND_API_KEY` is set). |
| `open` | Matching addresses are admitted immediately. Anything that cannot be admitted automatically — seats full, Cloudflare error — queues for review instead of being lost. |

Guard rails in every mode: `REGISTRATION_ALLOWED_DOMAINS` (comma-separated;
empty = any domain) is enforced before a request is even recorded, and
`REGISTRATION_MAX_SEATS` (default 50, Cloudflare's free tier; `0` = no cap)
stops automatic admission at that many admitted addresses. The form is
rate-limited per IP (`RATE_LIMIT_REGISTER_PER_MINUTE`, default 5), carries a
honeypot field, records nothing for an address that already has access, and
re-opens a denied request rather than duplicating it. Every outcome lands in
the audit trail (`access.register`, `access.approve_registration`,
`access.deny_registration`).

**Requirements.** Edge admission must be configured (`CF_API_TOKEN`,
`CF_ACCOUNT_ID`, `CF_ACCESS_POLICY_ID`, `ADMIN_EMAILS`): approving a request
*is* an admission, so without them the page reports itself closed. The page
and its API must also be reachable before sign-in, which means a Cloudflare
Access **Bypass** on `/register`, `/api/register` (prefix; covers
`/api/register/config`), `/assets` (the hashed SPA bundle) and the brand
files. `scripts/provision_cloudflare.py` creates that app on its next run;
the app itself exempts exactly the same paths from its own auth
(`_PUBLIC_PATHS` in `main.py`) and nothing under them reveals deployment
data — the bundle is static, and the two endpoints answer only whether
registration is open and the result of one submission.

The person still signs in through Cloudflare Access (one-time PIN or your
IdP). Nothing here mints a session or stores a password; an approved address
is exactly as admitted as one an admin typed by hand.

## Content governance

Once several people can add sources, two questions follow: *who put this
here?* and *should it be here at all?* The app answers both with a set of
controls that are on by default and need no configuration, plus three
settings for the deployment's policy. Everything below is domain-neutral —
it applies to any corpus.

**Always on**

- **Attribution.** Every document row records the verified identity that
  added it (`uploaded_by`); the sidebar shows it under private collections.
  Background index jobs capture the identity that started them.
- **Audit trail.** Uploads, index jobs, deletions, shares (create, accept,
  revoke), MCP tokens (create, revoke), policy decisions, reports, label
  changes, policy acknowledgements and every admin action land in an
  append-only `audit_events` table. Admin tab → *Audit trail* filters it and
  exports CSV; `AUDIT_RETENTION_DAYS` (default 365, `0` = forever) is the
  only knob, and it is separate from the search-history sweep.
- **Report.** Anyone who can read a collection can report one of its
  documents (the flag icon in the sidebar). The report is audited and, when
  `RESEND_API_KEY` is set, emailed to `ADMIN_EMAILS`.
- **Hash blocklist.** Admin removal with *Remove & block* records the file's
  sha256; the same bytes are refused at every ingest path in every
  collection afterwards. Hashes can also be added by hand from an
  organisation's own list.
- **Sensitivity labels.** Every collection carries `public`, `internal`
  (default), `confidential` or `restricted`; a document can override it.
  The label travels on every search result, chat citation and MCP result.
  `restricted` is the one label that is a boundary: the collection cannot be
  shared, and MCP clients see it only through a personal token explicitly
  scoped to it (MCP tab → token generator).

**The scanner** (`CONTENT_POLICY_ACTION`, default `flag`)

Every page of extracted text — including OCR output, image descriptions,
audio transcripts, the first rows of spreadsheets, and source code for the
secrets pack — is run through local regex rule packs at ingest: child sexual
abuse material *indicators* (solicitation and slang, not mere mention),
attack planning and incitement, weapons and drug trade, explicit content,
credential dumps, and secrets. Nothing leaves the machine. The knob decides
what a finding does: `flag` indexes with a badge, `quarantine` indexes but
hides the document from search, chat and MCP until an admin approves it,
`reject` refuses it, `off` skips the scan. Findings in the two critical
categories are always at least quarantined. Secrets never flag on their own
but make the document default to `confidential`.

Set `CONTENT_POLICY_LLM_REVIEW=true` to add a second opinion from the chat
LLM on a sample of pages. It can escalate a document; it never clears a
rule-pack finding. It sends content to a provider chat already trusts, and
costs tokens per upload, so it is off by default. Air-gapped deployments can
point it at local Ollama.

Be honest about coverage. This detects **text signals**. It cannot detect
abuse imagery (that needs licensed hash databases that are not available
for self-hosting) and it misses anything phrased to evade keywords. It is a
tripwire; attribution, the audit trail and fast admin takedown are the
controls. And "criminal" is contextual — a security team indexing threat
intelligence is legitimate — which is why the default flags and reviews
rather than blocks.

**Acceptable-use acknowledgement** (`AUP_REQUIRED`, default `false`)

With it on, every identity must accept the policy (once per `AUP_VERSION`)
before adding sources; the app blocks the ingest endpoints until they do
and shows the policy as a takeover. Bump the version to re-prompt everyone;
set `AUP_TEXT` (markdown) to replace the built-in policy. Acceptance is
recorded per identity, so this only takes effect under
`PRIVATE_COLLECTIONS`. All three settings are editable live in Admin tab →
*Policy*.

**Admin actions** (Admin tab → *Content review*)

Approve a held or flagged document, remove it with or without blocking its
hash, act on user reports, and suspend an identity — which revokes every
MCP token it holds and, when edge admission is configured, withdraws its
Cloudflare Access admission. Suspension leaves the person's documents in
place; removal is a separate, reviewable decision.

## Capacity & scaling

Clio is **one process by design**: the FAISS indexes, background-job
registry, and SSE progress queues all live in the process's memory, so
`uvicorn --workers N` or multiple replicas would silently diverge. Scale
**vertically** (more CPU/RAM on one host), and run a **second independent
instance** when you need a harder wall — different tenants, different
compliance regimes. `DB_BACKEND=postgresql` moves only the app metadata
database; vectors and per-collection stores stay on local disk, so it is
neither HA nor a path to replicas.

The comfortable envelope is an org appliance: **roughly 10–50 people** on
an always-on host. Two shared chokepoints define it — the request
threadpool (~40 slots; a chat turn holds one for its whole agent loop, up
to ~11 provider round-trips) and a single embedding lock every query and
indexing batch passes through. The identity layer agrees: Cloudflare Zero
Trust is free to 50 seats.

What protects the deployment when many people share it:

| Knob | Default | What it does |
|---|---|---|
| `RATE_LIMIT_CHAT_PER_MINUTE` | 6 | Per-identity chat requests/min |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 30 | Per-identity searches/min |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | 120 | Everything else under `/api` |
| `RATE_LIMIT_MCP_PER_MINUTE` | 0 | `/mcp` agent calls, keyed per personal token; 0 = unlimited |
| `RESEARCH_MAX_CONCURRENT` | 3 | Parallel `research_documents` calls; extra callers queue |
| `RESEARCH_QUEUE_TIMEOUT_SECONDS` | 60 | How long a queued research call waits before a retryable "busy" error |
| `CHAT_DAILY_TOKEN_BUDGET` | 0 (off) | Provider tokens one identity may spend on chat per UTC day. Cached answers stay free once capped. |
| `MAX_CONCURRENT_INDEX_JOBS` | 2 | Indexing jobs across all collections |
| `COLLECTION_STORAGE_LIMIT_BYTES` | 5 GiB | Most a single collection may hold, as the sum of its source files. Every ingest path (upload, staged upload, local/repo index jobs, `write_document` over MCP) refuses a file that would cross it with a **413** carrying the numbers; the Sources panel and the collections overview show usage. `0` = unlimited. |
| `RATE_LIMIT_REGISTER_PER_MINUTE` | 5 | Per-IP submissions to the public `/api/register` form |
| `SEARCH_HISTORY_RETENTION_DAYS` | 30 | Search log (stores result snippets) |
| `USAGE_RETENTION_DAYS` | 180 | Per-turn chat usage rows |

Identity for the limits is the verified Cloudflare Access email when
present, else the client IP (`CF-Connecting-IP` behind the tunnel). A
**429 response** means "you, specifically, are over a limit — wait the
`Retry-After` seconds," not that the server is down; the UI says so. With
`ADMIN_EMAILS` set, the Admin tab shows per-person spend, live process
stats, and the rate-limit counters; the same data is at
`/api/admin/usage` and `/api/admin/stats`.

## Embedding model on first start

The built-in embedding model runs in-process through **fastembed** (ONNX on
onnxruntime, part of the core install) and, when `requirements-torch.txt`
is installed, through **sentence-transformers** for models fastembed has no
export of; `LOCAL_EMBEDDING_BACKEND` (`auto` | `fastembed` |
`sentence-transformers`) picks, and both produce the same vectors, so
switching never forces a re-index. The Docker image bakes the default model
for both backends. A bare-metal install downloads it on first start: the
warm-up runs in the background and `GET /api/embedding/status` (and the
first-run screen) shows `downloading` with a byte count, then `ready`. To
pre-seed a host that cannot reach huggingface.co, run on a connected
machine `python -c "from fastembed import TextEmbedding;
TextEmbedding('sentence-transformers/all-MiniLM-L6-v2',
cache_dir='data/models/fastembed')"` and copy `data/models/fastembed/`
across; the fastembed cache lives under the data directory so it survives
container recreation with the volume.

## Backups

Everything lives in the data directory (`./data` by default, `/app/data` in
Docker): uploaded documents, FAISS indexes, the SQLite metadata and config
databases. Back up that directory and you can restore the whole deployment.

Stop the container first, or snapshot the volume, so SQLite is not mid-write.

## Checklist before you hand out the URL

- [ ] Direct access to the app's port fails from another machine — only the
      proxy answers
- [ ] `AUTH_PASSWORD` set, or the proxy is provably the only route in
- [ ] TLS terminated at the proxy or platform (`https://`, not `http://`)
- [ ] `CORS_ALLOW_ORIGINS` empty, or an explicit origin list — never `*` on a
      deployment others can reach
- [ ] The people who can log in are all cleared to see **every document** in
      every collection — or `PRIVATE_COLLECTIONS=true` is on behind Cloudflare
      Access and the team tier holds nothing sensitive
- [ ] `ADMIN_EMAILS` names whoever reviews flagged content, and
      `CONTENT_POLICY_ACTION` / `AUP_REQUIRED` match what you told people
      the rules are (see [Content governance](#content-governance))
- [ ] The data directory is backed up
