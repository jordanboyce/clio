# Clio

**Ask your sources. Check the evidence. Connect your AI tools.**

Clio is a self-hosted knowledge base for people and for the AI agents they
use. Add documents to a collection, ask questions that are answered with
citations, find the exact passage you need, and hand the same knowledge to
Claude, ChatGPT, Grok, Hermes Agent, Claude Code, Codex, VS Code or any other
MCP client. Indexing, search and reranking run locally; answers come from
whatever model you trust, hosted or on your own hardware. Nothing in the
product depends on a cloud service, there are no accounts to create, and the
default install needs no configuration at all.

The name is Clio, the Muse of history: the record remembers.

[Deployment](docs/DEPLOYMENT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Research workspace](docs/RESEARCH.md) · [Agentic research](docs/AGENTIC_RESEARCH.md) ·
[On-prem](docs/ONPREM.md) · [Roadmap](docs/ROADMAP.md)

---

## Table of Contents

- [Quick start](#quick-start)
- [Connect an AI client](#connect-an-ai-client)
- [If others can reach it](#if-others-can-reach-it)
- [Where people run it](#where-people-run-it)
- [What people do with it](#what-people-do-with-it)
- [More ways to deploy](#more-ways-to-deploy)
- [Configuration](#configuration)
- [API](#api)
- [Troubleshooting](#troubleshooting)
- [Advanced topics](#advanced-topics)
- [Development](#development)
- [FAQ](#faq)

---

## Quick start

Pick one. Either way you get http://localhost:8473, reachable from this
machine only, with nothing to configure and no sign-in.

**Docker**

```bash
docker compose up -d
```

The image builds the frontend, bakes the embedding model and the search
reranker in (nothing downloads at start), bundles OCR for scanned PDFs, and
keeps documents and indexes in `./data`. After pulling new code, rebuild with
`docker compose up -d --build`. For a lean image without PyTorch, OCR or audio
transcription (1.6 GB instead of 3.9 GB, everything an agent-facing knowledge
base needs), build with `SLIM=1 docker compose build`.

**Python 3.12 or newer**

```bash
pip install -r requirements.txt
python main.py
```

The search model (about 90 MB) and the reranker download in the background
the first time, with progress shown in the app; search works as soon as they
are in. Optional extras, each a large ML runtime, so install only what you
use:

```bash
pip install -r requirements-ocr.txt       # OCR for scanned PDFs (Tesseract)
pip install -r requirements-audio.txt     # audio transcription (Whisper)
pip install -r requirements-torch.txt     # PyTorch backend: wider local embedding model catalog
pip install -r requirements-postgres.txt  # PostgreSQL for the app database
```

**Then, in the browser**

1. **Add sources** — files, a folder, a git repository, or links. PDF, Office,
   Markdown, text, HTML, JSON, CSV/XLSX, sixty-plus programming languages,
   audio (transcribed) and images (described by a vision model). A folder or
   repository can be kept in sync.
2. **Ask** — pick a model in the top bar and ask; answers cite their sources
   and say when the sources do not establish something. **Find** searches
   passages and needs no model at all.
3. **Connect** — give the same knowledge to the AI client you already use
   (next section).
4. **Research** — for papers and books: screen them against a
   question, rename downloads descriptively, follow their references, map who
   cites whom, and prepare reviews overnight ([guide](docs/RESEARCH.md)).

For a model that everyone on the deployment shares, set it once in `.env`
(see [Configuration](#configuration)); otherwise each person picks a provider
in Settings. The Anthropic, OpenAI, Google, OpenRouter, xAI and Ollama Cloud
APIs, and anything you run yourself (Ollama, vLLM, LM Studio, llama.cpp, any
OpenAI-compatible endpoint), all work.

## Connect an AI client

Every MCP client talks to the same endpoint:

```
http://localhost:8473/mcp
```

It is a standard MCP streamable HTTP server (stateless, one request per
call), so many agents can be connected at once. From the **Connect** tab:

1. **Generate a personal token.** Tokens are read-only by default, can expire,
   and can be limited to specific collections; revoke one from the same
   screen when a machine is retired. Tick *Allow adding and updating sources*
   to let that agent save notes and summaries back into a collection.
2. **Pick your client and take what you need.** Every connection fact
   (server URL, legacy SSE URL, header, collection id) can be copied on its
   own, and the recipes are shaped by your choices: which collections to
   scope to (none, one, or one server entry per collection), whether the
   token is written into the file, read from an environment variable in the
   client's own syntax, or left out for an open server or OAuth sign-in, the
   transport, and the server name. Each client then offers its documented
   formats (config file, CLI command, install link or secret prompt where
   one exists), as a whole file or as just the server entry to merge into a
   file you already have. Coding agents and IDEs: Claude Code, Codex, Pi,
   Gemini CLI, Qwen Code, Cursor, VS Code, Copilot CLI, Windsurf, Zed,
   Cline, Roo Code, Kiro, OpenCode, Amp, Factory Droid, Crush, Mistral Vibe,
   JetBrains, Continue. Desktop and chat apps: Claude Desktop, claude.ai,
   ChatGPT, Grok, Hermes Agent, Goose, AnythingLLM, LibreChat, LM Studio,
   Warp, Open WebUI. Automation and APIs: n8n, LangChain, the Python and
   TypeScript MCP SDKs, the Claude Agent SDK, the OpenAI Agents SDK, the
   Vercel AI SDK, Pydantic AI, Google ADK, the OpenAI Responses API, the
   Anthropic MCP connector, the xAI API, and a generic entry (connection
   facts plus an mcp-remote bridge) for everything else. Clients that only
   speak the older HTTP+SSE transport use `/mcp/sse` with the same header.
3. Hosted assistants — claude.ai, ChatGPT, Grok — add the URL as a custom
   connector from their own settings. They connect from the vendor's servers,
   so the endpoint must be reachable from the internet; with an identity
   provider configured they sign in there instead of pasting a token
   ([docs/IDENTITY.md](docs/IDENTITY.md#oauth-for-mcp-clients)).

What the agent gets, scoped server-side to the collections its credential
may see:

| Tool | Use it for |
|---|---|
| `health_check`, `list_collections`, `get_collection_info` | Orientation: what is here, which embedding model, whether reranking is active |
| `search_collection`, `search_all_collections` | One quick lookup; retrieval strategy is chosen per query |
| `research_documents` | A multi-part question: focused subqueries, literal checks, diverse cited passages, coverage gaps |
| `find_in_documents`, `get_document_context` | Exact strings and identifiers; the surrounding text before quoting |
| `list_tables`, `get_table_schema`, `query_table`, `aggregate_table` | Counts, sums, filters and rankings over CSV/XLSX data, never guessed from passages |
| `ask_documents` | A finished, cited answer in one call — present when the deployment runs a model of its own (`AI_PROVIDER`) |
| `write_document`, `reindex_document`, `update_document_metadata` | Write-enabled tokens: save sources back, re-index one, set a sensitivity label |
| `search`, `fetch` | The two tools a ChatGPT custom connector requires, over the same scoping |

*What agents can do here* on the Connect tab lists the live catalog
(`GET /api/mcp/catalog`). To see how many agents a host can serve, run
`python scripts/mcp_load.py --url http://localhost:8473/mcp --sessions 32`
([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#capacity--scaling)).

**Agent skill and CLI.** `skills/clio/` is a ready-to-install
[Agent Skills](https://agentskills.io) folder: `SKILL.md` teaches an agent to
search, verify, cite and index safely, and `skills/clio/scripts/clio_cli.py`
(standard library only) creates collections, uploads a folder, waits for
indexing and searches, using `CLIO_URL` and `CLIO_TOKEN` from the environment.

## If others can reach it

Clio has no accounts: anyone who can open the port sees the whole corpus.
That is right for a machine only you use, and it is the default — no
password, reachable from this machine only. If other people can reach it (a
LAN, a server, a VPN), choose a shared password. One question, written to
`.env`:

```bash
python scripts/setup.py              # asks: only me, or others on my network?
```

(`--password` and `--open` answer it without asking; `run.sh` and `run.bat`
ask on first start.) By hand, the same thing is two lines in `.env`:

```bash
AUTH_PASSWORD=choose-a-long-passphrase
BIND_ADDRESS=0.0.0.0        # Docker; `python main.py` uses HOST=0.0.0.0
```

Browsers prompt for the password natively (any username); API clients send
`Authorization: Bearer <password>`; agents should use a personal MCP token
instead. Beyond a trusted LAN, put a TLS reverse proxy such as Caddy in front
or reach it over Tailscale or WireGuard — [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
has the complete Caddy example. An organisation that already runs single
sign-on can use it (`IDENTITY_PROVIDER=oidc` or `trusted_header`), which also
enables per-person collections and sharing ([docs/IDENTITY.md](docs/IDENTITY.md));
a personal or small-team install does not need this.

For a locked-down host, `docker-compose.hardened.yml` runs the container
read-only, unprivileged and without Linux capabilities
([DEPLOYMENT.md](docs/DEPLOYMENT.md#hardened-container)).

## Where people run it

| Situation | Run it as | Who gets in | Read |
|---|---|---|---|
| **Just you, on your machine** | `python main.py` or `docker compose up -d` | Loopback only; no sign-in, nothing to configure | [Quick start](#quick-start) |
| **A few people on your LAN** | Docker with `BIND_ADDRESS=0.0.0.0` and `AUTH_PASSWORD` set | Everyone who knows the shared password; one shared corpus | [DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **A team over HTTPS** | Docker behind a TLS reverse proxy (Caddy, nginx, Traefik), or on a Tailscale / WireGuard network | The password, plus the proxy or private network as the only route in | [DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **Agents, lots of them** | The same image (or `SLIM=1`), personal tokens per agent, `WORKER_THREADS` sized to the host | Each token sees the collections it was given; `scripts/mcp_load.py` tells you the host's envelope | [Connect an AI client](#connect-an-ai-client) |
| **An organisation that already runs sign-in** | The portable image, your own IdP (`IDENTITY_PROVIDER=oidc` or an authenticating proxy), your own models (`AI_PROVIDER`) | Identities from Keycloak, Entra ID, Okta, mTLS or Kerberos; optional private collections; OAuth sign-in for connector clients; a full audit trail | [ONPREM.md](docs/ONPREM.md), [IDENTITY.md](docs/IDENTITY.md), [AGENCY_PILOT.md](docs/AGENCY_PILOT.md) |
| **Fully air-gapped** | The offline bundle with `CLIO_PROFILE=secure` | As above, with cloud providers and model downloads disabled, refusal to start when unsafely exposed, and a `scripts/preflight.py` report of exactly where data can go | [AIRGAP.md](docs/AIRGAP.md), [SECURE_PROFILE.md](docs/SECURE_PROFILE.md) |
| **A hosted PaaS (Railway, Render, Fly.io, Coolify)** | The Dockerfile, a volume at `/app/data`, `AUTH_PASSWORD` | The password, or an SSO proxy in front | [Hosting on a PaaS](#hosting-on-a-paas-railway-render-flyio-coolify-) |

Every shape uses the same image and the same code path for authorization:
whether the request comes from the browser, the API, or an MCP client, it
goes through the app's access checks.

## What people do with it

| You want to… | Where | How it works |
|---|---|---|
| **Ask a question and see the evidence** | Ask | Grounded answers cite `[Source N]`; open the passage, the page, and the original file. The assistant says when the sources do not establish something. |
| **Find the exact passage or identifier** | Find | Automatic hybrid search (meaning + keywords, balanced per query: codes, names and quotes lean on exact words) without any answer model, then a local cross-encoder reranks the best candidates — on by default, nothing leaves the machine; exact strings, numbers, and negation stay intact. |
| **Research a multi-part question** | Ask, MCP | `research_documents` runs focused subqueries, reports coverage gaps and weak evidence, and spans collections. [Guide](docs/AGENTIC_RESEARCH.md). |
| **Get a finished answer in one agent call** | MCP | `ask_documents` returns the grounded, cited answer the in-app chat would give, when the deployment runs a model of its own (`AI_PROVIDER`). For agents whose own model is small or busy, or automations that just need the answer. |
| **Query spreadsheets, not just prose** | Ask, Find, MCP | CSV/XLSX become typed tables; counts, sums, filters, and rankings run as SQL, never as guessed passage search. Find returns the rows that contain every word you search for (a customer, an invoice number, a SKU), with no model needed. |
| **Triage, organize and review papers and books** | Research | Screens sources against your question with verdicts backed by verified quotes, renames downloads to *year – authors – title*, downloads what a source cites, maps citations between your sources, and prepares reviews overnight; works on the sources you select. Metadata comes from the source or a catalogue (OpenAlex, Crossref, arXiv, Open Library), never a model. [Guide](docs/RESEARCH.md). |
| **Turn a collection into a document** | Reports | Source-grounded briefings, summaries, and reports with citations to review before sharing. |
| **Teach it your house style or domain** | Saved instructions | Expertise packs add instructions and terminology per collection, versioned alongside the corpus. |
| **Give your AI assistant a private knowledge source** | Connect | Claude Desktop, claude.ai, ChatGPT, Grok and Claude Code sign in through your identity provider; Codex, VS Code, Cursor, Windsurf, AnythingLLM, Hermes Agent, Claude Code and any MCP SDK can also use a personal token. Agents can write notes and summaries back when a token allows it. |
| **Index scanned pages, photos, audio, code** | Add sources | OCR (local Tesseract/Docling or a vision model), Whisper transcription, code-aware chunking, image description. |
| **Index a whole source tree** | Add sources | Sixty-plus languages and config formats chunk by symbol (functions, classes, SQL objects, Terraform resources, shell functions, Vue/Svelte scripts), with `Makefile`, `Dockerfile` and friends recognised by name. Parsing is lenient: syntax errors, odd encodings and minified files still index; binaries, lockfiles, build output and vendored dependencies are skipped. Every passage keeps its symbol name and line range. |
| **Keep a folder or repository in sync** | Add sources | Point Clio at a folder or a git repository once; every later sync hashes the tree, skips unchanged files, re-indexes changed ones, and can prune documents whose file was deleted. *Sync* on a remote repository fetches first. The Sources panel says when a source is out of date and why (new commits, edited files, the remote ahead, an indexing run that did not finish); *Watch* re-syncs on an interval and every real change lands in the Updates feed (and an optional signed webhook). |
| **Share an indexed collection with another Clio** | Collections | *Export* writes one `.clio.zip` (extracted text, pages, tables, vectors, and which embedding model made them); *Import* previews it and reuses the vectors when the model matches. |
| **Arrange it your way** | Everywhere | A ribbon down the left edge holds every destination and action, grouped by purpose; hide, show or reorder items, show names beside the icons, or drag them (Settings → Layout, or right-click it). The Sources panel is a folder tree that follows where sources came from — a synced folder or repository keeps its structure, with labels and review flags on every row — and ticking a folder selects everything in it. `⌘K` / `Ctrl+K` opens a command palette for every destination, collection, recent chat, theme and action; `?` shows the shortcuts. |
| **Keep a shared corpus accountable** | Administration | Attribution on every document, an append-only audit trail (including agent reads if enabled), content-policy scanning with flag/quarantine/reject, sensitivity labels, blocklists, an acceptable-use gate. |

**Privacy.** Clio is self-hosted and open source (MIT). Documents and indexes
live in the data directory on your machine; local embeddings, keyword search
and reranking never leave it. External AI is optional: a hosted model you
configure receives only the passages it is asked about, and `OFFLINE_MODE=1`
disables cloud providers and model downloads entirely.

## More ways to deploy

### On-premises with your own models

The image is portable by design: build it once, carry it in, configure it
there. Nothing site-specific is baked in.

```bash
# on a connected machine
./scripts/package_image.sh --tag 1.0.0 --out /media/transfer
#   Windows:  .\scripts\package_image.ps1 -Tag 1.0.0 -Out D:\transfer

# on the target host
docker load < clio-1.0.0.tar.gz
cp .env.onprem.example .env      # model endpoint, auth, data path
docker compose -f docker-compose.onprem.yml up -d
```

Point the whole deployment at a model you run — vLLM, Ollama, llama.cpp,
LM Studio, TGI, LiteLLM, or any OpenAI-compatible gateway:

```bash
AI_PROVIDER=openai_compatible
AI_BASE_URL=http://vllm.internal:8000/v1
AI_MODEL=llama-3.3-70b-instruct
AI_PROVIDER_LABEL=Acme Internal LLM
```

Then nobody configures a model in their browser: chat, report generation,
OCR, the content-policy reviewer and the MCP `ask_documents` tool all use that
endpoint. A per-request key still overrides it, so an individual can bring
their own provider. Endpoints with a private CA are handled by dropping the
`.crt` into `certs/ca/` — trusted at container start, no rebuild. Registry
users can `docker pull ghcr.io/jordanboyce/clio` instead of carrying a file.
Full guide: [docs/ONPREM.md](docs/ONPREM.md).

### Air-gapped / offline deployment

Clio runs fully disconnected: build with `--build-arg OFFLINE_BUNDLE=1` to
bake the remaining runtime models (Whisper, Docling OCR) into the image next
to the embedding model and reranker, transfer it with `docker save` /
`docker load`, and run with `OFFLINE_MODE=1`, which disables cloud AI
providers and every model download. `CLIO_PROFILE=secure` refuses to start in
a weaker shape, and `scripts/preflight.py` reports exactly where data can go.
Full walkthrough: [docs/AIRGAP.md](docs/AIRGAP.md), [docs/SECURE_PROFILE.md](docs/SECURE_PROFILE.md).

### Hosting on a PaaS (Railway, Render, Fly.io, Coolify, …)

The image deploys anywhere that builds from a Dockerfile. Three things to configure:

1. **Port** — the container listens on the platform's injected `PORT` (falls back to `8473`). Platforms that ask for an internal port instead (Fly, Coolify): use `8473`.
2. **Persistent volume** — mount one at `/app/data`. Everything stateful lives under that single path.
3. **Auth** — set `AUTH_PASSWORD=<secret>` before exposing a public URL. `/health` stays open for platform health checks. A shared password gives no per-person revocation or audit trail — for a standing team deployment prefer an SSO proxy ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

The models are baked into the image, so cold starts download nothing and the
container works on ephemeral filesystems as long as `/app/data` is a volume.
TLS is the platform's job — leave `SSL_CERTFILE` / `SSL_KEYFILE` unset.

**Railway:** the repo ships a [railway.json](railway.json) (Dockerfile builder,
`/health` healthcheck, restart on failure). *New Project → Deploy from GitHub
repo*, attach a volume at `/app/data`, set `AUTH_PASSWORD`, then *Settings →
Networking → Generate Domain*. Expect a long first build; `SLIM=1` as a build
argument shortens it.

### Corporate SSL configuration

Behind a TLS-intercepting proxy or a private CA, drop the `.crt` files into
`certs/ca/`:

- **Docker, building yourself:** `docker compose up -d --build` installs them
  into the image's trust store at both the dependency and runtime stages.
- **Docker, prebuilt or air-gapped image:** the entrypoint installs whatever is
  mounted at `/certs/ca` on every start — into the system store *and* certifi's
  bundle, which is what the Python HTTP clients use. `docker-compose.onprem.yml`
  mounts `certs/ca` already; `docker compose -f docker-compose.onprem.yml restart clio`.
- **Python:** set `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` to the certificate path.

Use `certs/ca/` specifically, not `certs/`: the parent directory holds the dev
self-signed pair and is excluded from the build context so its private key
never lands in an image. Certificate contents are gitignored in both.

## Configuration

Everything is a setting in `.env`; [.env.example](.env.example) documents all
of them. The ones that matter most:

| Setting | What it decides |
|---|---|
| `AUTH_PASSWORD`, `BIND_ADDRESS` / `HOST` | Whether others can reach it, and whether they need a password ([If others can reach it](#if-others-can-reach-it)) |
| `AI_PROVIDER`, `AI_BASE_URL`, `AI_MODEL`, `AI_API_KEY` | The model the whole deployment uses; leave empty and each person picks one in Settings → AI Providers |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | Where the search index runs: the built-in local model (default), an Ollama you run, or a hosted embedding API. Changing it means re-indexing |
| `ENABLE_RERANKER`, `RERANKER_MODEL`, `RERANK_MAX_CONCURRENT` | The local cross-encoder that reorders the best candidates of every search; automatic by default |
| `WORKER_THREADS`, `RATE_LIMIT_*`, `RESEARCH_MAX_CONCURRENT` | How much concurrent work one host takes before callers queue ([Capacity](docs/DEPLOYMENT.md#capacity--scaling)) |
| `RESEARCH_LOOKUP_ENABLED`, `RESEARCH_CONTACT_EMAIL` | Whether the Research workspace may look up printed DOIs, arXiv ids and ISBNs in OpenAlex, Crossref, arXiv and Open Library ([Research](docs/RESEARCH.md#settings)) |
| `COLLECTION_STORAGE_LIMIT_BYTES` | Most a single collection may hold (5 GiB default) |
| `OFFLINE_MODE`, `CLIO_PROFILE` | No cloud providers, no downloads, no web fetching; refuse to start when unsafely exposed |
| `IDENTITY_PROVIDER`, `PRIVATE_COLLECTIONS` | Your own sign-in, and per-person collections enforced against it |
| `DATA_DIR` | Where documents, indexes and the app database live (`./data`) |

Provider API keys entered in the browser stay in that browser; a team key
saved under Settings → AI Providers is stored server-side for everyone on the
deployment.

---

## API

Everything the app does is an HTTP call; http://localhost:8473/docs is the
full, interactive reference. Endpoints that work on documents take a
`collection_id` query parameter (`default` when omitted). With
`AUTH_PASSWORD` set, add `-H "Authorization: Bearer <password>"` to each call.

**Add documents** — indexed before the call returns; for big batches use the
background jobs in the docs (`/documents/upload-staged`, `/documents/index-links`, …).

```bash
curl -X POST "http://localhost:8473/documents/upload?collection_id=default" \
  -F "files=@report.pdf" -F "files=@notes.md" -F "files=@sales.xlsx"
```

```json
{
  "message": "Successfully indexed 3 document(s) to collection 'default'",
  "documents_processed": 3,
  "total_pages": 48,
  "total_chunks": 131,
  "document_ids": ["4a3a2a313e0382fa", "32283fa951905db5", "4e57ef28fa1388f9"]
}
```

**Find passages** — no model involved:

```bash
curl -X POST "http://localhost:8473/search?collection_id=default" \
  -H "Content-Type: application/json" \
  -d '{"query": "return policy for field teams", "top_k": 5}'
```

Each result carries `filename`, `page_number`, `text_snippet`,
`similarity_score`, `document_id`, `chunk_id` and a `page_url` that opens the
original file at that page; `retrieval` says how the query was searched
(the hybrid balance chosen, whether it was reranked).

**Ask** — a grounded answer with its sources. `provider` names the model
provider and `X-AI-Key` carries your key; leave the header out when a team key
is saved in Settings, or name the deployment's own provider (`AI_PROVIDER`):

```bash
curl -X POST "http://localhost:8473/api/chat?collection_id=default" \
  -H "Content-Type: application/json" \
  -H "X-AI-Key: $ANTHROPIC_API_KEY" \
  -d '{"provider": "anthropic", "depth": "quick",
       "messages": [{"role": "user", "content": "When must field teams return equipment?"}]}'
```

The reply has `message.content` (with `[Source N]` citations), `sources`, and
`ai_usage`. `POST /api/chat/stream` streams the same turn as server-sent
events.

**Manage documents**

```bash
curl "http://localhost:8473/documents?collection_id=default"                    # list
curl "http://localhost:8473/documents/4a3a2a313e0382fa/pdf" -o original.pdf     # the original file
curl "http://localhost:8473/documents/4a3a2a313e0382fa/chunks"                  # exactly what was indexed
curl -X DELETE "http://localhost:8473/documents/4a3a2a313e0382fa?collection_id=default"
```

Deleting removes the document's chunks and vectors and, for an upload, its
stored file (a file indexed in place from your own folder is left alone). It
cannot be undone.

**Research workspace** — `/api/research/*` (analysis, renaming, references,
the citation graph, screening and review jobs) is listed in
[docs/RESEARCH.md](docs/RESEARCH.md#api). **Agents** should use the MCP
endpoint rather than these routes ([Connect an AI client](#connect-an-ai-client)).

A minimal Python client:

```python
import requests

BASE = "http://localhost:8473"
PARAMS = {"collection_id": "default"}

with open("report.pdf", "rb") as f:
    print(requests.post(f"{BASE}/documents/upload", params=PARAMS, files={"files": f}).json())

hits = requests.post(f"{BASE}/search", params=PARAMS,
                     json={"query": "equipment return period", "top_k": 5}).json()["results"]
for hit in hits:
    print(f"{hit['filename']} p.{hit['page_number']}: {hit['text_snippet'][:80]}")
```

---

## Troubleshooting

**Port 8473 is already in use.** Running Python, set `PORT=8001` in `.env`.
In Docker, change the host side of the mapping in `docker-compose.yml`
(`"${BIND_ADDRESS:-127.0.0.1}:8001:8473"`).

**The search model downloads slowly or not at all.** The first run fetches
about 90 MB from Hugging Face (the Docker image has it baked in). Behind a
firewall, copy the cache from a machine that has it —
`<DATA_DIR>/models/fastembed/` for the default ONNX backend, or
`~/.cache/huggingface/hub/` for the PyTorch backend — or pre-fetch it with
`scripts/prefetch_offline_models.py` ([docs/AIRGAP.md](docs/AIRGAP.md)). The
app shows **Download now** once the network works, and Settings → Indexing →
Embedding can switch to an Ollama you run or a hosted provider instead.

**`SSL: CERTIFICATE_VERIFY_FAILED`.** A TLS-intercepting proxy is in the way:
install its CA as described in
[Corporate SSL configuration](#corporate-ssl-configuration).

**A document fails to index.** The job's entry in the activity drawer names
the file and the reason. Common causes are an encrypted or corrupted PDF
(`pdfinfo file.pdf` tells you), a spreadsheet with an unusual encoding, or a
file type Clio does not read.

**A scanned PDF indexes with 0 chunks.** It has no text layer. Turn on OCR in
Settings → OCR — *Vision AI* (a vision-capable model from a provider you
already use, best on difficult scans) or *Local* (Tesseract:
`pip install -r requirements-ocr.txt`; on Windows without Docker also install
Tesseract and Poppler on `PATH`; Docling, `requirements-docling.txt` or
`--build-arg WITH_DOCLING=1`, is the higher-quality local option) — then
re-index the document.

**Search returns nothing.** Check that the collection has documents
(`curl "http://localhost:8473/documents?collection_id=…"`) and that the app is
healthy (`curl http://localhost:8473/health`); `python scripts/preflight.py`
(also shown in the About dialog) runs a full readiness check. Very specific wording can miss: try fewer words,
or Find with an exact phrase in quotes.

**The container runs out of memory or restarts.** `docker compose logs -f clio`
shows why. Large PDFs and big batches need memory during extraction and
embedding; give the container more (`mem_limit: 4g` under the service in
`docker-compose.yml`) or index in smaller batches.

**`Permission denied` on the data folder.** The container runs as an
unprivileged user (uid 10001). Give it the folder rather than opening it to
everyone: `sudo chown -R 10001:10001 ./data`.

**`Cannot connect to the Docker daemon`.** Start Docker Desktop (Windows,
macOS) or the service (`sudo systemctl start docker`).

**Python errors on install or start.** Clio needs Python 3.12 or newer (the
image uses 3.13). Use a virtual environment (`python -m venv venv`, then
`source venv/bin/activate` or `venv\Scripts\activate` on Windows) and
reinstall with `pip install -r requirements.txt`.

---

## Advanced topics

### Performance & scalability

One process serves everything, with a FAISS flat index, a keyword index and
SQLite per collection. Hundreds to a few thousand documents per collection
are routine. Indexing jobs queue per collection, research and reranking passes
have their own concurrency limits, and `scripts/mcp_load.py` measures what a
host serves concurrently ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#capacity--scaling));
`scripts/retrieval_bench.py` measures search quality and latency on a
versioned fixture corpus.

### Choosing where embeddings run

Every document chunk and every search query is turned into a vector by an
embedding model. By default that model runs inside the app on the server's
CPU through a small ONNX runtime — free and private, and light enough for a
laptop. First-run setup asks where the index should run and shows the
download as it happens; **Settings → Indexing → Embedding** changes it later
(changing the model re-embeds the collection). Good local choices:

| Model | Size | Backend | Notes |
|---|---|---|---|
| `all-MiniLM-L6-v2` (default) | 90 MB | ONNX or PyTorch | Fast on any CPU, English |
| `BAAI/bge-small-en-v1.5` | 130 MB | ONNX or PyTorch | Same size class, noticeably better retrieval |
| `nomic-ai/nomic-embed-text-v1.5` | 270 MB | ONNX or PyTorch | 8k context, strong on code and long passages |
| `jinaai/jina-embeddings-v2-base-code` | 320 MB | ONNX or PyTorch | Built for source code |
| `paraphrase-multilingual-MiniLM-L12-v2` | 470 MB | ONNX or PyTorch | 50+ languages |
| `intfloat/multilingual-e5-small` | 470 MB | PyTorch only | 100 languages (`pip install -r requirements-torch.txt`) |

With the PyTorch backend installed, any
[sentence-transformers](https://www.sbert.net/docs/pretrained_models.html)
model name works as `EMBEDDING_MODEL`. Or move the work to an Ollama you
already run, or to a hosted API:

| Option | Cost | Key | Notes |
|---|---|---|---|
| Built in (`local`) | Free | none | Runs on this server. Model downloads once (~90 MB default). |
| Google Gemini (`google`) | Free tier | AI Studio key, reused from AI Providers | Good multilingual quality. |
| Mistral (`mistral`) | Free tier | own key | EU-hosted. |
| Voyage AI (`voyage`) | Free tier | own key | Retrieval-focused models. |
| Jina AI (`jina`) | Free tier | own key | Free credits without a card. |
| OpenRouter (`openrouter`) | Free tier | reused from AI Providers | Dozens of models behind one key. The `:free` ones allow 50 requests a day (1,000 after buying $10 of credit); paid ones cost cents per million tokens. |
| OpenAI (`openai`) | Paid, low cost | reused from AI Providers | Cents per thousand pages. |
| Ollama on your own server (`ollama`) | Self-hosted | none | Point `OLLAMA_BASE_URL` at a machine that has pulled an embedding model. |
| Custom endpoint (`openai_compatible`) | Depends | optional | Any OpenAI-style `/embeddings` API: LM Studio, vLLM, LiteLLM, Together, … |

Hosted options send document text to that vendor at index time and query
text at search time; `OFFLINE_MODE=true` refuses them. Ollama Cloud and xAI
(Grok) are not offered: as of September 2026 Ollama Cloud serves chat models
only and rejects embedding calls, and xAI documents an embeddings endpoint
but lists no embedding model for it.

Use **Test connection** in Settings before saving — it embeds one short
string with the unsaved values and reports the vector size or the exact
error. Changing the provider or model takes effect for re-indexes and newly
opened collections immediately (no restart), while collections already open
keep searching their existing index until you re-index them.

### Security considerations

**In the default shared-appliance mode, everyone admitted can access the team
corpus.** Enable private collections for verified per-person access; otherwise
network admission is the main trust boundary. These settings control exposure:

| Setting | Default | Meaning |
|---|---|---|
| `HOST` | `127.0.0.1` | This machine only. Docker sets `0.0.0.0` itself, where the published port controls exposure. |
| `BIND_ADDRESS` | `127.0.0.1` | Published host interface for Docker Compose. Set explicitly for a protected network deployment. |
| `AUTH_PASSWORD` | empty | No auth. Required for anything reachable beyond loopback unless an identity proxy is the exclusive ingress. |

The defaults are safe together: a loopback-only bind needs no password. Change
one and you must change the other — the app warns loudly at startup if it is
bound to the network with no password set.

**For a shared deployment, set `AUTH_PASSWORD` and put TLS in front.** Run a
reverse proxy such as Caddy or nginx, or a private network such as Tailscale,
so the proxy or network is the only route in. If your organisation already has
single sign-on, an authenticating proxy or OIDC provider works too. Full
walkthrough, including how to verify the bypass is closed:
**[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

By default everyone who gets in sees the whole corpus; Clio has no accounts
of its own. Organisations that already run sign-in — their own OIDC provider
or an authenticating proxy ([docs/IDENTITY.md](docs/IDENTITY.md)) — can set
`PRIVATE_COLLECTIONS=true` for per-person collections: each collection is
visible only to its owner until shared (read or readwrite share links),
enforcement covers every entry point — search, documents, chat, research and
the `/mcp` tools — and everything created before the flag stays in a team
tier everyone sees. The mode refuses to start without an identity source it
can enforce. (The old `ENABLE_MULTI_USER` flag also refuses to start: it
filtered the collection list without enforcing anything.) Details:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**MCP clients are the same people.** A personal token only ever works on
`/mcp`; under an identity provider it carries the identity of whoever minted
it. An OAuth sign-in from Claude Desktop, claude.ai, ChatGPT or Claude Code is
verified by the same rules as a browser session, so an agent sees exactly the
collections its person can, and `MCP_AUDIT_TOOL_CALLS=true` records which
credential read which document.

**Content governance.** Once several people can add sources, every document
records who added it, every upload / deletion / share / token / admin action
lands in an audit trail, anyone can report a document, and admins can remove
one and block its hash from being re-added. Documents are scanned at ingest
by local rule packs (abuse-material indicators, attack planning, weapons and
drug trade, explicit content, credential dumps, secrets); `CONTENT_POLICY_ACTION`
decides whether a finding flags, quarantines (hides until approved) or rejects
the document. A separate wording scan badges possible prompt injection;
`INJECTION_SCAN_ACTION` chooses whether that also queues an admin task
(`review`, the default), only badges the source (`warn`), or is off.
Collections carry a sensitivity label — `restricted` ones cannot
be shared and are invisible to MCP clients without an explicitly scoped token —
and `AUP_REQUIRED=true` makes each identity accept an acceptable-use policy
before adding sources. Everything runs locally; the optional LLM second
opinion is off by default. Full description in
[docs/DEPLOYMENT.md → Content governance](docs/DEPLOYMENT.md#content-governance).

**Also worth setting:**

1. **HTTPS** (`SSL_CERTFILE`/`SSL_KEYFILE`, or terminate at the proxy)
2. **Leave `CORS_ALLOW_ORIGINS` empty** unless a separate web app needs it. Empty
   sends no CORS headers, so other sites cannot read responses; `*` lets any page
   your browser visits read this API. Non-browser clients (MCP, curl, SDKs) are
   unaffected either way. Credentialed cross-origin requests are never allowed
   for the wildcard.
3. **Rate limits and budgets** are built in — per-identity request limits for
   chat and search are on by default, an MCP class and a daily
   provider-token budget per person are opt-in, and each collection has a
   storage cap (`RATE_LIMIT_*`, `CHAT_DAILY_TOKEN_BUDGET`,
   `COLLECTION_STORAGE_LIMIT_BYTES`). Tune them for your model's capacity; a
   proxy in front can add coarser protection.
4. **Air-gapped environments** — set `OFFLINE_MODE=1` to disable cloud AI
   providers, web fetching and model downloads entirely (see
   [docs/AIRGAP.md](docs/AIRGAP.md)).

Stored credentials (the OCR vision key) are masked in `GET /api/config` and
never returned to a client.

### Backup & recovery

Everything stateful lives in the data directory (`DATA_DIR`: `./data`, or the
`/app/data` volume in Docker):

```
data/app.db                                 collections, settings, tokens, audit trail
data/answer_cache.db                        cached answers (safe to drop)
data/collections/<id>/documents/            the stored source files
data/collections/<id>/indexes/metadata.db   chunks, document records, tables, research records
data/collections/<id>/indexes/bm25.db       keyword index
data/collections/<id>/indexes/*.index       vectors (FAISS)
data/models/                                downloaded models (re-downloadable)
```

`scripts/backup.py` takes consistent snapshots of the SQLite databases even
while the server runs, writes a SHA-256 manifest, and verifies before it
restores:

```bash
python scripts/backup.py backup  --data ./data --out clio-backup.tar.gz
python scripts/backup.py verify  clio-backup.tar.gz
python scripts/backup.py restore clio-backup.tar.gz --data ./data-restored
```

Vector indexes are plain files, so for a backup you must be able to restore,
stop the server first (`docker compose stop`) or take a volume snapshot. A
single collection can also be exported as a `.clio.zip` from the Collections
view. [DEPLOYMENT.md](docs/DEPLOYMENT.md) shows the same commands run against
a Docker volume.

---

## Development

```bash
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest                              # backend tests
cd frontend && npm install && npm run dev     # http://localhost:5173, proxies to the backend on :8473
cd frontend && npm test && npm run lint       # frontend tests and lint
cd frontend && npm run build                  # production build into frontend/dist (served by FastAPI)
```

The backend is FastAPI; the frontend Vue 3 + Vite + Pinia + Tailwind/DaisyUI,
built into `frontend/dist` (gitignored) and served with immutable cache
headers for hashed assets. Docker builds it in a dedicated stage, and
`run.sh` / `run.bat` build it on first run. How the pieces fit — retrieval,
chat, MCP, governance, the research workspace — is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

```
clio/
├── main.py              App assembly: middleware, lifespan, routers, frontend serving
├── config.py            Every setting, loaded from .env
├── api/                 HTTP routers, one per domain (documents, search, chat, collections,
│                        research, mcp, sharing, admin, governance, …)
├── services/            Business logic: extraction, chunking, embeddings, vector and keyword
│                        indexes, retrieval and reranking, chat tools, MCP server, governance
│   ├── indexing/        The indexing pipeline
│   └── research/        The research workspace (papers and books)
├── models/schemas.py    Request and response models
├── frontend/            Vue 3 app (src/components, src/stores, src/utils)
├── skills/clio/         Agent skill and a dependency-free CLI
├── scripts/             Setup, backup, preflight, image packaging, load and retrieval benchmarks
├── tests/               Backend test suite (pytest)
├── docs/                Deployment, architecture, identity, on-prem, air-gap, research guides
├── Dockerfile           Multi-stage image: frontend build, dependencies, slim runtime
└── docker-compose*.yml  Standard, on-prem and hardened deployments
```

---

## FAQ

**Does it need a cloud AI provider?**
No. Without any model or API key you can add sources, search them with
**Find** (passages and spreadsheet rows, with links to the page or row), use
most of the Research workspace (analysis, renaming, references, the citation
map), and connect AI clients over MCP, whose own model then does the
answering. Indexing, search and reranking run locally; only written answers
(**Ask**, **Reports**, research verdicts and reviews, suggested questions)
and describing pictures need a model, and asking a question in Ask with none
connected runs it in Find instead. For answers, use any provider you trust —
Anthropic, OpenAI, Gemini, OpenRouter, Ollama Cloud — or a model you run
yourself (Ollama, vLLM, LM Studio, llama.cpp, any OpenAI-compatible
endpoint), set once for the whole deployment with `AI_PROVIDER`
([docs/ONPREM.md](docs/ONPREM.md)).

**Can I use it offline?**
Yes. Search and embeddings are local after the first run (the model
downloads once; the Docker image has it built in). For a deployment that must
never reach the internet, build the offline bundle and run with
`OFFLINE_MODE=1` ([docs/AIRGAP.md](docs/AIRGAP.md)); answers then come from a
model you host.

**Which file types does it read?**
PDF, Word (DOCX), PowerPoint (PPTX), Markdown, text, HTML, JSON/JSONL,
CSV/XLSX (as queryable tables), sixty-plus programming and configuration
languages, audio (transcribed), and images (described by a vision model).
Scanned PDFs are read with OCR when it is enabled, and Settings → Custom file
types teaches it a format of your own.

**How do I know an answer is right?**
Every answer cites numbered sources you can open at the passage and page, and
says when the sources do not establish something. Find shows the passages
themselves with no model in between. In the Research workspace a model's
statement is kept only if its quote is found in the source.

**Can several people use it?**
Yes, but Clio has no accounts. Set a shared password (`AUTH_PASSWORD`) and
everyone who knows it sees the same corpus. An organisation that already runs
single sign-on can use its own OIDC provider or an authenticating proxy for
per-person collections, sharing and an audit trail
([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/IDENTITY.md](docs/IDENTITY.md)).

**Can Claude Desktop, claude.ai or ChatGPT use it?**
Yes, when the deployment has an identity provider. They add the `/mcp` URL as
a custom connector and sign in there; the app is the OAuth resource server and
your IdP does the login. Claude Code, Codex, VS Code and AnythingLLM can do
the same or use a personal token from Connect
([docs/IDENTITY.md](docs/IDENTITY.md#oauth-for-mcp-clients)).

**Can an agent add to a collection, not just read it?**
Yes, with a personal token minted with *Allow adding and updating sources*:
the `write_document` tool creates, replaces or appends text sources, indexed
and audited like an upload. There is deliberately no delete tool.

**Does it run on Windows?**
Yes: Docker Desktop, or Python with `run.bat`.

**Can I delete the embedding model cache?**
Yes. The default ONNX backend keeps it in `<DATA_DIR>/models/fastembed/` (or
`/opt/clio/models/fastembed/` inside the Docker image); the PyTorch backend
uses the Hugging Face cache under `~/.cache/huggingface/hub/`. It downloads
again on the next start, with progress shown in the app.

---

## License

Licensed under the [MIT License](LICENSE). You're free to use, modify, and
deploy this anywhere — including commercially and inside internal systems —
provided the copyright notice and the license text travel with the software.

## Contributing

Issues and pull requests are welcome. Run the backend and frontend tests
([Development](#development)) before opening a pull request, and keep the
docs in step with the change.

---

**Clio** — The record remembers.
