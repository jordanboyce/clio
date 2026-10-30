# Self-hosting Clio

**It just works. There is nothing to set up.** Start it, open the browser,
add sources, ask questions:

```bash
docker compose up -d      # or: python main.py
# open http://127.0.0.1:8473
```

There is no sign-in, no account to create and no configuration to write.
Clio is built to run on hardware you control: a laptop, a home server, a
machine under a desk, a VM at work. Nothing in the product depends on a cloud
service, a hosted identity service or an outbound connection. You run one
process, point a browser at it, and decide who else can reach it.

Clio has **no accounts**. By default it is an **open appliance**: everyone who
can reach it sees the whole corpus - every collection, every document, every
table, and the same `/mcp` knowledge surface (subject to restricted-source
handling). That is the right default for a machine only you use, and it is why
the app listens on loopback until you say otherwise. If others can reach the
port, [set `AUTH_PASSWORD`](#b-others-can-reach-it-set-a-password).

## The security model in one paragraph

Out of the box nothing but the network protects the app, so the network
default is closed: Python binds `127.0.0.1` and Docker Compose publishes
`127.0.0.1:8473`. To let other people in you make two decisions: how they are
**let in** (almost always a shared `AUTH_PASSWORD`; an organisation with its
own sign-in can use [an identity provider](IDENTITY.md) instead), and how the
connection is **protected** (TLS from a reverse proxy, or a private network
such as Tailscale). The app warns at startup if it is reachable from the
network with no authentication, and refuses to start on an identity
configuration it cannot enforce.

| Setting | Default | What it controls |
|---|---|---|
| `HOST` | `127.0.0.1` | Which interfaces accept connections. Docker sets `0.0.0.0` inside the container; there the published port is the boundary. |
| `BIND_ADDRESS` | `127.0.0.1` | Host interface published by Docker Compose. Set `0.0.0.0` to accept connections from other machines. |
| `AUTH_PASSWORD` | empty | A shared secret required on every request except `/health`. Browsers prompt for it natively (HTTP Basic, any username); API and MCP clients send `Authorization: Bearer <password>`. |
| `CORS_ALLOW_ORIGINS` | empty | Which *other* web origins may read responses in a browser. Empty is correct unless a separate web app calls the API. |
| `PRIVATE_COLLECTIONS` | `false` | Per-person collections. Only for organisations that already run sign-in; requires `IDENTITY_PROVIDER`. See [IDENTITY.md](IDENTITY.md). |

The legacy `ENABLE_MULTI_USER` flag always refuses to start; see
[Private collections](#private-collections) for the supported replacement.

## Choose a deployment shape

### A. Just you, on your machine

```bash
python main.py            # http://127.0.0.1:8473
# or
docker compose up -d      # http://127.0.0.1:8473
```

Neither default exposes the app to your LAN, and neither needs any
configuration. `./run.sh` / `run.bat` bootstrap a virtual environment and
build the frontend on first run. Add `AUTH_PASSWORD` later if this changes;
nothing about your data changes when you do.

### B. Others can reach it: set a password

For a few people on your LAN or at one site, or a household. Let the machine
accept connections from the network and put a shared password in front:

```bash
# .env
BIND_ADDRESS=0.0.0.0          # Docker; for python main.py use HOST=0.0.0.0
AUTH_PASSWORD=choose-a-long-passphrase
```

Then `docker compose up -d` (or restart). Browsers prompt for the password
natively (any username); API and MCP clients send
`Authorization: Bearer <password>`. Everyone who knows it sees the same
corpus, so this fits a group that is cleared to see everything. It cannot
revoke one person without changing the password for all, and it keeps no record
of who did what. Agents should use [personal MCP tokens](#mcp-clients) rather
than the shared password.

Over plain `http://` on a trusted LAN this works, but the password crosses the
wire in the clear. Prefer shape C, or a private network (shape D), as soon as
anyone is not sitting in the same room.

### C. A team, over HTTPS, behind a reverse proxy

Put a TLS-terminating proxy (Caddy, nginx, Traefik) in front, keep Clio off
the public network, and let the proxy be the only thing that talks to it. Keep
`AUTH_PASSWORD` set so the proxy is not the only gate.

**1. Bind Clio to loopback or the internal network only.** With Compose the
default `127.0.0.1:8473` is already right when the proxy runs on the same
host. If the proxy is another container, drop the `ports:` mapping and share
a Docker network instead:

```yaml
services:
  clio:
    # ports: ["127.0.0.1:8473:8473"]   # removed: only the proxy can reach it
    expose:
      - "8473"
    networks: [web]
  caddy:
    image: caddy:2
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy-data:/data
    networks: [web]
networks:
  web: {}
volumes:
  caddy-data: {}
```

**2. Point the proxy at Clio.** A complete Caddyfile (automatic HTTPS, with
a real hostname that resolves to the machine):

```
clio.example.org {
    encode zstd gzip
    reverse_proxy clio:8473 {
        flush_interval -1          # do not buffer streamed chat answers
    }
}
```

Use `reverse_proxy 127.0.0.1:8473` when Caddy runs on the host rather than
in a container. For nginx the equivalent is `proxy_pass`, with
`proxy_buffering off;` and the usual `X-Forwarded-*` headers.

**3. Tell Clio to trust the proxy's forwarding headers, from the proxy
only.** Rate limiting needs the real client address, not the proxy's, and
uvicorn only honours `X-Forwarded-For` from peers it is told to trust:

```bash
# .env - the proxy's address as Clio sees it
FORWARDED_ALLOW_IPS=172.18.0.0/16      # the Docker network, or the proxy's IP
```

Do not set it to `*` unless the proxy is genuinely the only thing that can
open a connection to Clio: any peer in that list can claim to be any client.

**4. Verify the bypass is closed.** From another machine, try the app
directly, not through the proxy:

```bash
curl -sS --max-time 5 http://<host>:8473/health && echo "REACHABLE - fix this"
```

That must fail. Then check `https://clio.example.org/` loads (and prompts for
the password, if you set one).

Any TLS-terminating proxy works the same way. You can also let Clio serve
HTTPS itself with `SSL_CERTFILE` and `SSL_KEYFILE` (`certs/generate-cert.sh`
makes a self-signed pair for testing), which is fine for a single box but
leaves certificate renewal to you.

### D. A team, over a private network

Tailscale, WireGuard, or a corporate VPN. The network is the boundary:
members of the network can reach Clio, nobody else can, and there is no
public address to attack. Bind to the private interface (or publish the port
only on it); add `AUTH_PASSWORD` too if the network is wider than the people
who should use Clio.

With Tailscale the shortest path is `tailscale serve`, which gives you
HTTPS on your tailnet name with a managed certificate and no proxy to run:

```bash
tailscale serve --bg 8473      # https://<machine>.<tailnet>.ts.net
```

Keep Clio on `127.0.0.1` (the default) and let `tailscale serve` proxy to it;
set `FORWARDED_ALLOW_IPS=127.0.0.1`, which is already uvicorn's default.

### E. On-premises appliance, own models

Shape B, C or D plus a model you run: the image is loaded from a file or an
internal registry, `AI_PROVIDER` / `AI_BASE_URL` / `AI_MODEL` point the whole
deployment at your endpoint, and nobody configures a provider in a browser.
Combine with `OFFLINE_MODE=1` when the deployment must not reach the
internet at all. Use `docker-compose.onprem.yml`; the walkthrough is
[ONPREM.md](ONPREM.md), and the disconnected specialisation is
[AIRGAP.md](AIRGAP.md).

If the site already has an identity provider and wants per-person collections,
use it: `IDENTITY_PROVIDER=oidc` against your IdP, or `trusted_header` behind
the proxy that already authenticates ([IDENTITY.md](IDENTITY.md)). Events then
get a real actor, and `MCP_AUDIT_TOOL_CALLS=true` extends that to what agents
read. This is optional and only for organisations that already run sign-in;
Clio does not provide accounts itself.

## MCP clients

`/mcp` is part of the same app, so it sits behind the same door - with one
wrinkle: **MCP clients authenticate with a token, not by signing in.**

- **Personal access tokens (recommended for people).** Settings -> MCP ->
  Personal access tokens lets anyone who can already use the app mint their
  own bearer token
  for Claude Code, Codex, or GitHub Copilot without touching the server's
  `.env`. The tab hands back ready-to-paste config
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
  never - the form defaults to 90 days; an expired token is refused exactly
  like a revoked one) and **Limit this token to specific collections**,
  which makes the ticked collections the *only* ones the agent can see over
  MCP, whatever their sensitivity - the right shape for a client that works
  on one project. Without that limit a token sees everything its owner can,
  plus any restricted collections it was explicitly granted. The table shows
  each token's expiry, last use and call count, so idle or runaway agents are
  easy to spot and revoke.
- With `AUTH_PASSWORD` alone (no personal token), clients send
  `Authorization: Bearer <password>` and work normally, but anonymously -
  see below.
- **Any other MCP client.** `/mcp` is a standard MCP *streamable HTTP*
  endpoint (stateless, JSON responses, served at `/mcp` with no redirect),
  so anything that speaks the protocol attaches with the URL and a bearer
  header. The Connect tab writes the exact configuration for each of:
  Claude Code, Codex, Pi, Gemini CLI, Cursor, VS Code, Copilot CLI,
  Windsurf, Zed, Cline, Roo Code, Kiro, OpenCode (coding agents and IDEs); Claude
  Desktop, claude.ai, ChatGPT, Grok, Hermes Agent, Goose, AnythingLLM,
  LibreChat, Open WebUI (desktop and chat apps); n8n, LangChain, the Python
  and TypeScript MCP SDKs, the OpenAI Responses API, the Anthropic Messages
  API MCP connector and the xAI API (automation, frameworks and APIs), plus
  a generic entry with the three facts every other client needs. Hosted
  assistants (claude.ai, ChatGPT, Grok) connect from the vendor's servers,
  so the endpoint must be reachable from the internet and they sign in
  with OAuth (the shape above) rather than a pasted token. Clients that
  still only speak the older HTTP+SSE transport use `/mcp/sse` (same
  header, same scoping; the server announces its message endpoint on
  connect) - prefer streamable HTTP whenever the client offers it, since
  SSE sessions live in process memory for the life of the stream. Whatever
  the client, the first call to make is `health_check` (it reports the
  collections, the embedding model and whether reranking is active), then
  `list_collections`.
- **A finished answer in one call.** When the deployment runs a model of
  its own (`AI_PROVIDER`, see shape **E**), the tool list gains
  `ask_documents`: the client sends a question and receives the grounded,
  cited answer the in-app chat would give, with the numbered sources. It is
  the right call for a client whose own model is small or busy, or for an
  automation that just needs the answer; everything else stays
  evidence-first (`research_documents`, `search_collection`), because the
  intelligence belongs to the client's model. It does not exist without a
  deployment model, so a client never plans around it in vain.
- **Sign in from the client (OAuth).** Claude Desktop, claude.ai, ChatGPT
  and Claude Code (`claude mcp login`) can attach with just the endpoint
  URL and a browser sign-in at your identity provider - no token to paste
  or revoke. It needs an authorization server for `/mcp`: an `oidc`
  deployment has one already (set `MCP_PUBLIC_URL`); otherwise name one with
  `MCP_OAUTH_ISSUER` + `MCP_OAUTH_AUDIENCE`. Without one, use personal
  tokens. The token resolves to the same person a browser session would.
  Hosted clients (claude.ai, ChatGPT)
  connect from the vendor's servers, so the endpoint must be reachable from
  the internet for them; clients that run on your machine do not need that.
  Details, per-IdP client registration and the optional required scope:
  [IDENTITY.md](IDENTITY.md#oauth-for-mcp-clients).

Whatever the client, it gets the same full-corpus access as everyone else -
unless [private collections](#private-collections) are on, where a personal
token is an identity that collections can be shared to, and a
password-authenticated client reaches team collections only.

## Private collections

`PRIVATE_COLLECTIONS=true` turns per-person ownership on. It exists because
its predecessor — an `ENABLE_MULTI_USER` flag that filtered only the
collection *list* while search, documents, chat, and every `/mcp` tool acted
on any `collection_id` unchecked — advertised an isolation boundary without
being one. That flag still refuses to start; this mode is the real version of
what it pretended to be.

**It is for organisations that already run sign-in, and requires a verified
identity source.** Clio has no accounts of its own. Ownership enforced against
an identity anyone can forge is worthless, so the app refuses to start with the
flag on and no source it can enforce. Set `IDENTITY_PROVIDER` to one of:

- `oidc` — a bearer JWT from your own identity provider (Keycloak, Entra ID,
  Okta, PingFederate). The answer for shapes **C** and **E** when the site
  already has single sign-on.
- `trusted_header` — an authenticating reverse proxy (mTLS terminator,
  Kerberos front end, site SSO proxy) has already established who the caller
  is and passes it in a header.

Full configuration, and the guards each one needs, in
[IDENTITY.md](IDENTITY.md). For a personal or small-team install leave
`PRIVATE_COLLECTIONS=false`; everyone then shares one corpus behind
`AUTH_PASSWORD`.

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
  optional expiry) from the collection's ⋮ menu, or shares directly with an
  email address; the recipient accepts in the same dialog. Shares are
  revocable, and deletion stays owner-only.
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
  dialog. From then on every person the identity provider admits can search it, ask about it
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
  no separate name to share collections to. A client authenticating with
  `AUTH_PASSWORD` alone has no identity and sees team collections only, as
  does any password-authenticated browser session.

Two deliberate limits, so the boundary stays honest: password callers cannot
own or accept anything (no identity), and if you need a harder wall than
application-level checks — different compliance regimes, different tenants —
run a second instance. Separate instances remain a stronger boundary than any
in-app flag.

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

**Prompt-injection wording** (`INJECTION_SCAN_ACTION`, default `review`)

A separate, local scan looks for text that tries to steer an AI reading the
document (instruction overrides, chat-format tokens, exfiltration). It badges
the source; it never hides or rejects it. `review` also queues a medium or
high verdict for an admin. `warn` keeps the badge and skips the queue — the
right setting when ordinary prose is filling the list. `off` skips the scan.
Low-confidence matches stay on the source and are not a task. Approving a
document (one row, or **Approve all shown**) dismisses the warning; re-indexing
the same file does not put it back.

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
MCP token it holds. Suspension leaves the person's documents in place; removal
is a separate, reviewable decision.

## Old versions and duplicate text

Policy sets accumulate v2 next to v3, and the same paragraph in several files.
Clio handles both at search time and never hides anything.

**Replaced by.** A collection owner marks a document as replaced by another one
in the same collection (Sources → the *Replaced by…* button on a row, or
`POST /documents/{id}/supersede` with `{"replaced_by": "<document_id>", "note": "…"}`;
`DELETE` on the same path clears it; `clio_cli.py supersede mark|clear`). Search
then ranks the older document `SUPERSEDED_PENALTY` (default `0.15`, on the same
0–1 scale as the reranker probability) below an equally relevant current
passage. It still appears when it is the only match, and every result from it
carries `superseded_by: {document_id, filename}`. Chat, research and MCP tell the
model to prefer the current version and to say so when it cites the older one.
For "what did v2 say" questions a search passes `filters.include_superseded: true`
(MCP/chat tool argument, `/search` body, `clio_cli.py search --include-superseded`):
the penalty is skipped and the label stays.

Rules: both documents must be in the collection, a document cannot replace itself
or close a loop, and a quarantined document cannot be named as the replacement
(its name would show in results). Deleting the replacement clears the mark on the
older document. Re-indexing the older document, or a full or per-extension
reindex, keeps the mark. Each change is in the audit log
(`document.supersede`, `document.supersede_cleared`) and drops cached chat answers.
The column is added by an automatic migration (schema 3.8); older databases open
unchanged.

**Suggestions.** `GET /documents/supersede-suggestions?collection_id=…` (read
access) lists likely pairs: file names that differ by a version, year or
revision token (`v2`/`v3`, `2025`/`2026`, `rev B`), `(1)` / `- Copy` duplicates,
and different files with identical opening text. Nothing is applied; owners accept
or dismiss each one in the Sources panel (dismissals are remembered in that
browser only).

**Duplicate text.** After ranking, a result whose text is the same as a
higher-ranked one after normalising case, whitespace and punctuation, or whose
word-3-gram Jaccard similarity is at least `DUPLICATE_SIMILARITY_THRESHOLD`
(default `0.95`), is folded into it. The surviving result carries
`duplicates: [{document_id, filename, page}]` (at most 5) and `duplicate_count`, so
a citation can say "also in …". Passages that differ in a number, or in a word like
*may*/*must*/*not*, are never merged. `top_k` is refilled from the next-ranked
candidates. A document the user selected as a source always keeps its own result.
Turn it off with `COLLAPSE_DUPLICATES=false`. The one cost: when copies were
found and the reranker is off, that search runs a second, wider retrieval.

## Capacity & scaling

Clio is **one process by design**: the FAISS indexes, background-job
registry, and SSE progress queues all live in the process's memory, so
`uvicorn --workers N` or multiple replicas would silently diverge. Scale
**vertically** (more CPU/RAM on one host), and run a **second independent
instance** when you need a harder wall — different tenants, different
compliance regimes. `DB_BACKEND=postgresql` moves only the app metadata
database; vectors and per-collection stores stay on local disk, so it is
neither HA nor a path to replicas.

The comfortable envelope is a team appliance: **roughly 10–50 people** on
an always-on host, plus the agents they connect — an agent turn fans out
several MCP calls at once, so plan for calls, not seats. Two shared
chokepoints define it — the worker-thread pool (`WORKER_THREADS`, 64 by
default: every MCP tool call, chat turn and upload runs in one; a chat turn
holds its slot for the whole agent loop, up to ~11 provider round-trips) and
the CPU-bound inference every query passes through (one embedding lock, and
at most `RERANK_MAX_CONCURRENT` cross-encoder passes). The event loop itself
only accepts connections and relays results, so a slow query never freezes
the UI or other clients. There is no seat limit; the host is the limit.

`scripts/mcp_load.py` is the way to find that limit for your host and
corpus: it opens N MCP sessions, fires a realistic mix of tool calls for a
fixed time, and reports per-tool latency percentiles plus the latency of a
`/health` probe that runs alongside (the event loop's responsiveness, which
is what every *other* client feels). Run it against a staging copy before
handing out the URL, and again after changing `WORKER_THREADS` or the
reranker settings:

```bash
python scripts/mcp_load.py --url https://clio.example.org/mcp --token <personal token> --sessions 32 --duration 60
```

What protects the deployment when many people share it:

| Knob | Default | What it does |
|---|---|---|
| `RATE_LIMIT_CHAT_PER_MINUTE` | 6 | Per-identity chat requests/min |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | 30 | Per-identity searches/min |
| `RATE_LIMIT_DEFAULT_PER_MINUTE` | 120 | Everything else under `/api` |
| `RATE_LIMIT_MCP_PER_MINUTE` | 0 | `/mcp` agent calls, keyed per personal token; 0 = unlimited |
| `RESEARCH_MAX_CONCURRENT` | 3 | Parallel `research_documents` calls; extra callers queue |
| `RESEARCH_QUEUE_TIMEOUT_SECONDS` | 60 | How long a queued research call waits before a retryable "busy" error |
| `WORKER_THREADS` | 64 | Worker threads shared by every MCP tool call, chat turn, upload and document read; callers queue beyond it |
| `RERANK_MAX_CONCURRENT` | 2 | Cross-encoder reranking passes in flight at once (each uses every core); `0` = unbounded |
| `RERANK_QUEUE_TIMEOUT_SECONDS` | 1 | A search that waits longer than this for a reranking slot answers in retrieval order and says so (`retrieval.reranked: false`) |
| `CHAT_DAILY_TOKEN_BUDGET` | 0 (off) | Provider tokens one identity may spend on chat per UTC day. Cached answers stay free once capped. |
| `MAX_CONCURRENT_INDEX_JOBS` | 2 | Indexing jobs across all collections |
| `COLLECTION_STORAGE_LIMIT_BYTES` | 5 GiB | Most a single collection may hold, as the sum of its source files. Every ingest path (upload, staged upload, local/repo index jobs, `write_document` over MCP) refuses a file that would cross it with a **413** carrying the numbers; the Sources panel and the collections overview show usage. `0` = unlimited. |
| `SEARCH_HISTORY_RETENTION_DAYS` | 30 | Search log (stores result snippets) |
| `USAGE_RETENTION_DAYS` | 180 | Per-turn chat usage rows |

Identity for the limits is the verified identity when there is one, else the
client IP — which behind a reverse proxy is the address uvicorn reports, so
set `FORWARDED_ALLOW_IPS` to the proxy (see shape **C**). A
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

### When the model and the index disagree

Changing the embedding model after documents are indexed leaves the collection
with vectors of one size and a model that makes another. Search over what is
already indexed keeps working, but nothing new can be added, and a job that
tries now says why instead of finishing "with errors" and no sources:

- the job fails up front (no links are fetched, no files extracted) with the
  message *"The loaded model 'X' produces N-dimensional vectors but this
  collection's index expects M"* and `error_code: embedding_mismatch`;
- the web UI shows a warning above the sources list and under the failed job
  with a **Re-index collection** button (owners only; it asks to confirm);
- from a shell, `clio_cli.py reindex -c NAME`; over HTTP,
  `GET /api/collections/{id}/embedding-health` reports the state and
  `POST /api/collections/{id}/reindex` fixes it. Switching the model back to
  the one that built the index also clears it.

Every failed file is logged as `Failed to index <name> (job N, collection
'<id>', <ExceptionClass>): <message>`, so one grep on the job number shows the
whole story.

## Data directory and backups

Everything lives in one data directory (`./data` by default, `/app/data` in
Docker, `DATA_DIR` to move it): uploaded documents, FAISS indexes, the SQLite
metadata and config databases. Back up that directory and you can restore the
whole deployment. (With `DB_BACKEND=postgres` the app metadata database lives in
Postgres, so `pg_dump` it too.)

`scripts/backup.py` does this with checks built in. It snapshots every SQLite
database with SQLite's online backup API (consistent even while the server
writes), leaves out the re-downloadable model caches, writes a SHA-256
manifest into the archive, and refuses to restore anything that fails
verification:

```bash
docker compose stop                       # needed for a restorable backup
docker run --rm -v clio_clio-data:/app/data -v "$PWD:/out" clio:local     python scripts/backup.py backup --data /app/data --out /out/clio-$(date +%F).tar.gz
docker compose start

python scripts/backup.py verify clio-2026-10-27.tar.gz     # hashes + integrity_check
python scripts/backup.py restore clio-2026-10-27.tar.gz --data ./data-restored
```

Stop the server first when the backup must be restorable: vector indexes are
plain files and one being saved at that instant can be captured half-written.
The databases are always consistent; the tool says this on every run. The
archive is written `0600` because it holds your documents; encrypt it before
it leaves the host. Run a restore drill (restore to a scratch directory,
start a second instance on it, search) before you need it, and after each
upgrade.

The shipped `docker-compose.yml` bind-mounts `./data`; on Windows hosts, or
wherever SQLite misbehaves on a bind mount, use a named volume and take the
backup from a container as above, or `docker cp clio:/app/data ./copy`.

## Hardened container

The image runs the server as an unprivileged user (`clio`, uid 10001). The
entrypoint starts as root only to install private CA certificates and fix
ownership of the data directory, then drops every capability and privilege
before starting the app.

For an enterprise host, add the hardening overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.hardened.yml up -d
# or, from a prebuilt image:
docker compose -f docker-compose.onprem.yml -f docker-compose.hardened.yml up -d
```

It runs as uid 10001 from the first instruction, drops all capabilities,
sets `no-new-privileges`, mounts the root filesystem read-only (only the data
volume and a tmpfs are writable), and caps the process count. Started this
way the entrypoint cannot install CA certificates from `/certs/ca`; if your
model endpoint uses a private CA, bake it into a derived image:

```dockerfile
FROM clio:local
USER root
COPY ca/*.crt /usr/local/share/ca-certificates/
RUN update-ca-certificates  && cat /usr/local/share/ca-certificates/*.crt >> "$(python -c 'import certifi;print(certifi.where())')"
USER 10001
```

Check it took effect: `docker exec clio id` prints `uid=10001(clio)`.

## Giving agents access

Agents need two things: a token and the skill.

1. **Mint a token per agent or per person** (Settings -> MCP -> Personal access
   tokens): read-only unless the agent must write, limited to the collections
   it needs, with an expiry. Revoke from the same screen. Put it in the agent
   runtime's secret store as `CLIO_TOKEN`, not in a prompt or repository.
2. **Install the skill.** `skills/clio/` is an Agent Skills folder
   (`SKILL.md` plus a dependency-free CLI). Copy it into the agent's skills
   directory (for Claude Code, `.claude/skills/clio/` or `~/.claude/skills/clio/`).
   It tells the agent how to search, verify passages, cite, treat document text
   as data rather than instructions, and index files. Agents that speak MCP
   connect to `$CLIO_URL/mcp` with the same token; the skill still applies.
3. **Audit.** Set `MCP_AUDIT_TOOL_CALLS=true` to record agent reads.

For batch indexing without a browser, the CLI does the whole path:
`CLIO_URL=https://clio.example.internal CLIO_TOKEN=... python skills/clio/scripts/clio_cli.py upload ./handbook --collection Policies`.

To answer "how many documents, how big, which files" exactly, use
`get_collection_stats` and `list_documents` (MCP and in-app chat), or from a
shell `clio_cli.py stats -c Policies` and `clio_cli.py files -c Policies --csv
all-files.csv`. The same data is at `GET /documents/stats` and
`GET /documents/export?format=csv|jsonl` (filters `q`, `file_type`, `sort`);
the export streams page by page, so a collection of tens of thousands of files
costs flat memory, and each export is written to the audit trail.

### Retrieval coverage

Borrowed from how Context7 scores indexed libraries: each collection can be
asked "how findable is my content?" before anyone relies on it. **Settings ->
Indexing -> Retrieval coverage** (or `clio_cli.py coverage -c Policies`) shows
the latest report; the collection's owner runs it.

- **What a run does.** It samples up to 100 passages (cap 300; a fixed seed, so
  two runs on an unchanged collection ask the same questions; spread evenly
  across documents so a large file does not dominate), turns each into a
  search a person might type, and sends it through the same search that the
  app, API and MCP use (automatic mode, with the re-ranker if it is on). It
  records whether the passage and its document came back in the top 5 and top
  10. No model is involved by default: a query is the passage's heading, or
  its few most distinctive words and names, never the passage pasted back.
  A passage with no searchable words (a table of numbers) is reported as
  unprobed, not as a miss.
- **What the report shows.** Sources found in the top 5, passages in the top 10,
  MRR, the hardest-to-find documents with the searches that missed, passages
  that never came back, the probe method, the embedding model and the corpus
  version at run time, and how long it took. **It measures retrieval
  findability, not answer correctness**: a high score means the right source
  is returned for questions drawn from its own text, not that an answer built
  from it is right. Real users ask differently from their own documents.
- **Your own questions** (owners; Settings, or `POST .../coverage/questions`)
  pair a question with the file that should answer it. They are scored
  separately and are the most trustworthy signal: they come from what people
  actually ask. Each add and remove is in the audit trail.
- **Optional model-written questions.** `COVERAGE_LLM_QUESTIONS=true` asks the
  default chat provider to phrase the questions, which sends the sampled
  passages to that provider. It is off by default; with `OFFLINE_MODE=true`
  only a local provider is used, and any model failure falls back to the
  heuristic probes with a note in the report.
- **Staleness and cost.** A report is marked stale when documents are added,
  replaced, removed or quarantined, or the embedding model changes; the
  numbers are kept so you can see the trend, but treat them as old. A run is a
  background thread (a few seconds to about two minutes, hard-capped at 3
  minutes, partial results reported as such), one per collection (a second
  request gets a 409) and two per deployment. Quarantined documents are never
  read. The latest report is stored in the collection's own `metadata.db`, so
  it travels with backups and collection exports.
- **For agents.** MCP `list_collections` adds `newest_upload` and, once a report
  exists, `quality: {coverage_hit_at_5, report_age, stale}` (plus
  `owner_question_hit_at_5` when owners wrote questions) so an agent can prefer
  the collection that is fresh and findable. Treat a stale or low score as a
  reason to double-check, not as a verdict.

### Custom file types

JSON-like files with an extension only one product uses (`.acme`, `.cfgx`, ...)
can be indexed by registering the extension. A *profile* says how to read it:
where the records are, which fields matter and what to call them. A profile is
plain data, never code, and built-in types (`.json`, `.pdf`, source files, ...)
cannot be redefined.

The parser accepts strict JSON, JSON with `//` and `/* */` comments and trailing
commas, and JSON Lines / concatenated values. Files up to 100 MB. Profiles live in
`<data dir>/custom_file_types.json`, so a normal backup carries them, and changes
apply without a restart. Changing or removing a profile does not touch files
already indexed. To apply a changed profile to them, re-index just that
extension (below); the rest of the collection is left exactly as it is.

**Web UI** (admin only): Settings → Custom file types. The panel is hidden for
non-admins, and also hides itself if the server answers 403.
- List all registered types with extension, label, kind, source (manual/heuristic/AI), and who updated it.
- Add/edit: pick a sample file → Suggest profile (toggle AI on/off; the toggle is off, with the reason, when no chat model is configured) → live preview → Save. The saved source is `ai` or `heuristic` while the profile is still what was suggested, and `manual` once you edit it. A message says when the AI pass fell back to rules and why. A 400 from the server (for example a record path that is not in the sample) is shown in the dialog.
- After a save that changes how files are read (not a new label or description), the panel says "N indexed files use .acme", per collection, how many were indexed with an older profile, and offers **Re-index N files** for each collection you own. For a collection owned by someone else it says its owner must do it. The **Files** button on a row opens the same list later.
- Delete: counts the indexed files first, per collection. They stay searchable. They cannot be re-indexed afterwards (see below), so no re-index is offered; register the extension again to re-index them.
- Registered types are drawn with their `kind` (data, docs, code) in the source list and stats, not as "other", and are accepted by uploads, the native file picker and folder scans.

All changes are admin-only (`ADMIN_EMAILS`) and audited. Reading the list is open.

```bash
# 1. Ask for a proposal from a sample file (nothing is saved)
curl -H "Authorization: Bearer $CLIO_TOKEN" -F extension=.acme -F "file=@sample.acme" \
     "$CLIO_URL/api/custom-file-types/suggest"
# add -F use_ai=false to keep the sample off any model

# 2. Check what a (possibly edited) profile would index
curl -H "Authorization: Bearer $CLIO_TOKEN" -H 'Content-Type: application/json' \
     -d '{"extension":".acme","sample":"...","profile":{"record_path":"pumps","title_field":"name","exclude":["id"]}}' \
     "$CLIO_URL/api/custom-file-types/preview"

# 3. Save it; uploads, folder sync and re-index now accept .acme.
#    The response says whether the save changes how files are read and, per
#    collection you can see, how many indexed files use .acme.
curl -X PUT -H "Authorization: Bearer $CLIO_TOKEN" -H 'Content-Type: application/json' \
     -d '{"label":"Acme export","record_path":"pumps","title_field":"name","exclude":["id"]}' \
     "$CLIO_URL/api/custom-file-types/.acme"

# 4. How many indexed files use it, per collection (admin; works after a delete too)
curl -H "Authorization: Bearer $CLIO_TOKEN" "$CLIO_URL/api/custom-file-types/.acme/usage"

# 5. Re-index only .acme files in one collection (collection owner)
curl -X POST -H "Authorization: Bearer $CLIO_TOKEN" -H 'Content-Type: application/json' \
     -d '{"extensions":[".acme"]}' "$CLIO_URL/api/collections/COLLECTION_ID/reindex-extensions"
```

`PUT` replaces the whole profile: send every field you want to keep. The answer
is `{profile, reading_changed, affected_documents, usage}`; `usage` lists each
collection with its `documents`, `out_of_date` and whether you can re-index it.
"Out of date" means indexed before the profile last changed how files are read
(`format`, `record_path`, `title_field`, `include`, `exclude`, `labels`,
`records_per_section`, `max_value_chars`, `enabled`); it uses the index time
already stored on each document, so nothing is added to existing indexes.

**Scoped re-index.** `POST /api/collections/{id}/reindex-extensions` takes
`{"extensions": [".acme"]}` (case and the leading dot are optional), is owner
only and audited, and runs as the usual background job (one re-index at a time:
409 while another runs). Only documents with those extensions are re-read; every
other document keeps its chunks, ids and vectors. The job uses the collection's
live index, so uploads, deletes and search keep working and nothing is lost on
save. Each file is read and embedded before its old entry is swapped out, so a
file that no longer parses keeps its previous entry and the job ends as failed
with the file names; a missing source file fails the job before anything
changes. Review decisions (quarantine, approval, sensitivity, injection
review) carry over. The request is refused with **400** for an empty list, a
malformed extension, spreadsheets (those need a full re-index of the
collection), and any extension the server does not currently read, including a
deleted or switched-off custom type: re-indexing would extract again and drop
the files. Indexed files of a deleted type stay searchable as they are.

Profile fields: `label`, `description`, `format` (`auto`, `json`, `jsonl`, `text`),
`kind` (`data`, `docs`, `code`; how the file is grouped in stats), `record_path`
(dotted path to the list or object of records), `title_field`, `include` /
`exclude` (dotted field names, `*` wildcard, list positions ignored), `labels`
(field name to display name), `records_per_section` (default 10),
`max_value_chars`, `enabled`.

**The model's part.** `suggest` first builds a rule-based proposal (finds the
records, picks a title, drops UUIDs, hashes and encoded blobs). With `use_ai` it
also sends a bounded excerpt (lists shortened to three items, long strings cut)
to the deployment's default chat provider and asks for a label, field selection
and clearer field names. The answer is treated as untrusted: only seven keys are
read, each is checked against the sample, and any failure falls back to the
rule-based proposal. It never enables a type, changes `kind`, or saves anything.
In air-gapped mode only a local provider can be used. Extracted text is scanned
for prompt injection and content-policy hits like any other source.

**CLI** (`skills/clio/scripts/clio_cli.py`):
- `clio_cli.py types` — list registered types
- `clio_cli.py type-suggest .acme sample.acme [--use-ai]` — propose a profile
- `clio_cli.py type-save .acme '{"record_path":"pumps","title_field":"name"}'` — save a profile (the answer includes the per-collection usage)
- `clio_cli.py reindex -c Plant --ext .acme` — re-index only those files in a collection (repeat `--ext` for several)

### Link indexing

"Add link" fetches pages from the server, so it identifies itself with a
User-Agent that carries the project URL (Wikimedia refuses agents without a
contact URL). Set `LINK_USER_AGENT` to name your own organisation instead. A
site that blocks crawlers (HTTP 403) cannot be indexed from a link; the error
says so and suggests uploading the saved page.

## Upgrading

```bash
git pull
docker compose up -d --build      # `up -d` alone does not rebuild
```

If `up` fails with `The container name "/clio" is already in use`, a container
from an older install (or another checkout) holds the name. Remove it with
`docker rm -f clio` and re-run; its data is not deleted with it. An install
from before the project was called Clio may hold the name `asymptote` instead
(`docker ps -a` shows it); remove that one the same way. `./data` and named
volumes are kept either way.

The data directory is untouched by an upgrade, and schema changes are
applied at start. Take a backup first if the data matters. Downgrading to an
older version is not supported once a newer one has migrated the data.

## Checklist before you hand out the URL

Only needed if anyone other than you can reach the app.

- [ ] If you use a reverse proxy: direct access to the app's port fails from
      another machine — only the proxy answers
- [ ] People are let in by something: `AUTH_PASSWORD`, your identity
      provider, or a network only they can reach
- [ ] TLS in front of anything beyond a trusted LAN (`https://`, not
      `http://`), with `FORWARDED_ALLOW_IPS` limited to the proxy
- [ ] `CORS_ALLOW_ORIGINS` empty, or an explicit origin list — never `*` on a
      deployment others can reach
- [ ] Everyone who can reach it is cleared to see **every document** in every
      collection (or you run with an identity provider and
      `PRIVATE_COLLECTIONS=true`, and the team tier holds nothing sensitive)
- [ ] If you want an administrator separate from ordinary users, `ADMIN_EMAILS`
      names them (this needs an identity provider; on an open appliance the
      Admin tab is open to everyone). They review flagged content, and
      `CONTENT_POLICY_ACTION` / `AUP_REQUIRED` should match what you told
      people the rules are (see [Content governance](#content-governance))
- [ ] The data directory is backed up with `scripts/backup.py`, and a restore
      drill has been run on a scratch directory
- [ ] Each agent has its own expiring, collection-limited token, and the
      container runs with `docker-compose.hardened.yml`

### Behind a firewall that blocks huggingface.co

Neither the image build nor the first start depends on the download succeeding.
`docker compose build` prints a warning and continues when the bake cannot reach
the hub; at runtime the app opens, lists existing sources and serves settings,
while search and indexing return HTTP 503 with the reason until a model is
available. From the readiness bar (or Settings → Indexing → Embedding) choose
one of: retry the download once the proxy allows it, pre-seed the cache from a
connected machine (docs/AIRGAP.md), point `EMBEDDING_PROVIDER` at an Ollama you
run, or at a hosted API you trust.
