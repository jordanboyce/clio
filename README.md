# Clio

**Ask your sources. Check the evidence. Connect your AI tools.**

Clio is a self-hosted knowledge workspace for individuals, teams, and
organisations that want to keep their documents on their own infrastructure.
Add documents to a collection, ask questions with citations, or find the exact
passage you need. Expose the same knowledge to the AI clients you already use —
Claude Desktop, claude.ai, ChatGPT, Claude Code, Codex, VS Code — over MCP.
Local embeddings work out of the box; answer generation uses a model you
configure, from a hosted API to a model running on your own hardware. Hosted
providers receive the text they process.

Clio is part of the DOE **Prometheus** ecosystem: Prometheus brings the fire
(compute, models); Clio keeps the record (grounded, citable knowledge). The
name — Clio, Muse of history, daughter of Mnemosyne — leaves room for a future
family of related tools under the Mnemosyne umbrella.

The everyday workspace has three destinations:

- **Ask** — converse with your collection and open supporting sources.
- **Find** — search passages without requiring an answer-generation model.
- **Connect** — attach your AI client: sign in from the client, or mint a
  personal MCP token.

Reports, saved instructions, and administration are under **More**. Notes and
tools open on demand. Existing collections, saved conversations, and API routes
remain available.

Start with a curated knowledge collection: engineering runbooks, product docs,
research, or internal procedures. This project does not yet provide the connector
breadth or source permission syncing of an enterprise search platform.

[Deployment](docs/DEPLOYMENT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Product assessment and priorities](docs/PRODUCT_ASSESSMENT.md) · [Roadmap](docs/ROADMAP.md)

[Agentic research guide](docs/AGENTIC_RESEARCH.md): investigate multiple
subquestions, find exact wording, inspect evidence coverage, and use the same
research tools in chat and MCP clients.

---

## Use cases

### What people do with it

| You want to… | Where | How it works |
|---|---|---|
| **Ask a question and see the evidence** | Ask | Grounded answers cite `[Source N]`; open the passage, the page, and the original file. The assistant says when the sources do not establish something. |
| **Find the exact passage or identifier** | Find | Hybrid search (semantic + keyword) without any answer model; exact strings, numbers, and negation stay intact. |
| **Research a multi-part question** | Ask, MCP | `research_documents` runs focused subqueries, reports coverage gaps and weak evidence, and spans collections. [Guide](docs/AGENTIC_RESEARCH.md). |
| **Query spreadsheets, not just prose** | Ask, MCP | CSV/XLSX become typed tables; counts, sums, filters, and rankings run as SQL, never as guessed passage search. |
| **Turn a collection into a document** | Generate (under More) | Source-grounded briefings, summaries, and reports with citations to review before sharing. |
| **Teach it your house style or domain** | Expertise (under More) | Expertise packs add instructions and terminology per collection, versioned alongside the corpus. |
| **Give your AI assistant a private knowledge source** | Connect | Claude Desktop, claude.ai, ChatGPT and Claude Code sign in through your identity provider; Codex, VS Code, AnythingLLM and Claude Code can also use a personal token. Agents can write notes and summaries back when a token allows it. |
| **Index scanned paper, photos, audio, code** | Add sources | OCR (local Tesseract/Docling or a vision model), Whisper transcription, code-aware chunking, image description. |
| **Index a whole source tree** | Add sources | Sixty-plus languages and config formats chunk by symbol (functions, classes, SQL objects, Terraform resources, shell functions, Vue/Svelte scripts), with `Makefile`, `Dockerfile` and friends recognised by name. Parsing is lenient by design: syntax errors, unbalanced braces, unterminated strings, mixed tabs and spaces, odd encodings and minified files still index; binaries, lockfiles, build output and vendored dependencies are skipped. Every passage keeps its symbol name and line range, so Find, Ask and MCP results say *which function* they came from. The Sources panel shows a type tile per file and chips (Code · Docs · Data · Media) that filter the list. |
| **Keep a folder in sync** | Add sources | Point Clio at a folder once; every later sync hashes the tree, skips unchanged files, re-indexes changed ones, and can prune documents whose file was deleted. Remembered folders show *Sync* and *Prune* in the Sources panel (`POST /documents/sync-folder`). |
| **Move fast with the keyboard** | Everywhere | `⌘K` / `Ctrl+K` opens a command palette for every destination, collection, recent chat, theme and action; `?` shows the shortcuts; `g` then a letter jumps between Ask, Find, Connect, Collections and Settings. |
| **Keep a shared corpus accountable** | More → Admin | Attribution on every document, an append-only audit trail (including agent reads if enabled), content-policy scanning with flag/quarantine/reject, sensitivity labels, blocklists, an acceptable-use gate. |

### Where people run it

| Situation | Run it as | Who gets in | Read |
|---|---|---|---|
| **Just you, on your machine** | `python main.py` or `docker compose up -d` | Loopback only; no password needed | [Quick Start](#quick-start) |
| **A team on an internal network** | Docker with `BIND_ADDRESS` and `AUTH_PASSWORD` | Everyone with the password sees the shared corpus | [DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **A team reachable from anywhere** | Docker + Cloudflare Tunnel + Access, no inbound ports | Email PIN for an explicit allowlist; Jordan-only Clio administration; `/request-access` contact page | [REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md) |
| **An organisation hosting it internally** | The portable image, your own IdP (`IDENTITY_PROVIDER=oidc` or an authenticating proxy), your own models (`AI_PROVIDER`) | Identities from Keycloak, Entra ID, Okta, mTLS or Kerberos; private collections; OAuth sign-in for connector clients; a full audit trail | [ONPREM.md](docs/ONPREM.md), [IDENTITY.md](docs/IDENTITY.md), [AGENCY_PILOT.md](docs/AGENCY_PILOT.md) |
| **Fully air-gapped** | The offline bundle with `OFFLINE_MODE=1` | As above, with cloud providers and model downloads disabled | [AIRGAP.md](docs/AIRGAP.md) |
| **A hosted PaaS (Railway, Render, Fly.io, Coolify)** | The Dockerfile, a volume at `/app/data`, `AUTH_PASSWORD` | The password, or an SSO proxy in front | [Hosting on a PaaS](#hosting-on-a-paas-railway-render-flyio-coolify-) |

Every shape uses the same image and the same code path for authorization: a
collection is reachable only through the app's access checks, whether the
request comes from the browser, the API, or an MCP client.

---

## Table of Contents

- [Use cases](#use-cases)
- [Quick Start](#quick-start)
- [What It Does](#what-it-does)
- [Technology Stack](#technology-stack)
- [Installation](#installation)
- [Configuration](#configuration)
- [API Usage](#api-usage)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)

---

## Quick Start

### Python (Recommended)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the server
python main.py

# 3. Open your browser
open http://localhost:8473
```

Optional feature sets (each pulls in large ML runtimes — install only what you use):

```bash
pip install -r requirements-ocr.txt       # OCR for scanned PDFs
pip install -r requirements-audio.txt     # audio transcription (Whisper)
pip install -r requirements-postgres.txt  # optional PostgreSQL backend (multi-user mode)
```

### Docker (recommended for internal-network deployment)

```bash
docker compose up -d
open http://localhost:8473
```

That's the whole setup — the image builds the frontend, bundles OCR (Tesseract + Poppler), bakes the embedding model into the image, and persists documents/indexes in `./data`. No `.env` is required to start; add one to override defaults. For corporate CA certificates, drop `.crt` files into `certs/ca/` before building (see [Corporate SSL Configuration](#corporate-ssl-configuration)).

`docker compose up -d` publishes port 8473 to **127.0.0.1 only**. To expose it on a trusted network, explicitly set `BIND_ADDRESS` and configure authentication first. Read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) before granting team access.

### Sharing it with a team

Choose an access model deliberately. In the default shared-appliance mode, everyone admitted can access the team corpus. `PRIVATE_COLLECTIONS=true` enables collection ownership and read/readwrite sharing, enforced against a verified identity: Cloudflare Access at the edge, your own OIDC provider (`IDENTITY_PROVIDER=oidc` — Keycloak, Entra ID, Okta, PingFederate), or an authenticating reverse proxy (`trusted_header` — mTLS, Kerberos, a site SSO proxy). Configure `ADMIN_EMAILS` for operators. The app refuses to start with private collections on and no identity source it can enforce. Setup and verification: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/IDENTITY.md](docs/IDENTITY.md).

To reach a home or lab box from anywhere without opening a port, put it behind Cloudflare Tunnel + Access; `scripts/provision_cloudflare.py` creates the tunnel, the Access apps and the DNS record in one run. Set `CLIO_ALLOW_EMAILS` to the exact addresses to admit; people sign in with Cloudflare's emailed one-time PIN, with no app password or public registration. Recipe: [docs/REMOTE-ACCESS.md](docs/REMOTE-ACCESS.md).

People can also ask for access themselves: `REGISTRATION_MODE=approval` (queue for an admin) or `open` (admit matching addresses at once) turns on a public `/register` page, with an optional email-domain allowlist and a seat cap. Each collection holds up to 5 GiB of sources by default (`COLLECTION_STORAGE_LIMIT_BYTES`); usage is shown in the Sources panel.

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
OCR and the content-policy reviewer all use that endpoint, and the UI shows
it as connected. A per-request key still overrides it, so an individual can
bring their own provider. Endpoints with a private CA are handled by
dropping the `.crt` into `certs/ca/` — trusted at container start, no
rebuild. Registry users can `docker pull ghcr.io/jordanboyce/clio`
instead of carrying a file. Full guide: [docs/ONPREM.md](docs/ONPREM.md).

### Air-gapped / offline deployment

Clio runs fully disconnected: build with `--build-arg OFFLINE_BUNDLE=1` to bake every runtime model (reranker, Whisper, Docling OCR) into the image, transfer it with `docker save`/`docker load`, and run with `OFFLINE_MODE=1` — which disables cloud AI providers and all HuggingFace downloads, limiting supported provider paths to the local or self-hosted endpoints you configure. Enforce a network egress policy for a verifiable no-egress deployment. Full walkthrough: [docs/AIRGAP.md](docs/AIRGAP.md).

### Hosting on a PaaS (Railway, Render, Fly.io, Coolify, …)

The image is designed to deploy anywhere that builds from a Dockerfile. Three things to configure:

1. **Port** — the container listens on the platform's injected `PORT` (falls back to `8473`). Platforms that ask for an internal port instead (Fly, Coolify): use `8473`.
2. **Persistent volume** — mount one at `/app/data`. Everything stateful (documents, vector indexes, app database) lives under that single path. Without a volume the app still runs, but data is lost on redeploy.
3. **Auth** — the app has no login of its own, so before exposing a public URL set `AUTH_PASSWORD=<secret>` in the environment. Browsers prompt for it natively (HTTP Basic, any username); API and MCP clients send `Authorization: Bearer <secret>`. `/health` stays open for platform health checks. A shared password gives no per-person revocation or audit trail — for a standing team deployment prefer an SSO proxy ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

The embedding model is baked into the image at build time, so cold starts don't download anything and the container works on fully ephemeral filesystems (as long as `/app/data` is a volume). TLS is the platform's job — leave `SSL_CERTFILE`/`SSL_KEYFILE` unset and let the platform terminate HTTPS.

#### Railway

The repo ships a [railway.json](railway.json) (Dockerfile builder, `/health` healthcheck, restart-on-failure), so deploying is:

1. **New Project → Deploy from GitHub repo** — Railway detects the Dockerfile and builds it (the image is large: CPU torch + OCR + Whisper; expect a long first build).
2. **Attach a volume** to the service (right-click the service → *Attach Volume*) with mount path `/app/data`.
3. **Set the `AUTH_PASSWORD` variable** on the service before generating a public domain — the app is unauthenticated without it.
4. **Settings → Networking → Generate Domain.** Railway injects `PORT` and terminates HTTPS at the edge automatically; when prompted for a target port, any value works since the app listens on `$PORT`.

API and MCP clients then authenticate with `Authorization: Bearer <AUTH_PASSWORD>` against `https://<your-app>.up.railway.app`.

### What You Get

- **Web Interface**: http://localhost:8473 - Simple UI for searching, uploading, and managing documents
- **API Docs**: http://localhost:8473/docs - Interactive OpenAPI documentation
- **API Endpoint**: http://localhost:8473/api - REST API for programmatic access
- **MCP Endpoint**: http://localhost:8473/mcp - Streamable-HTTP MCP server for external agents

**First time setup**: the search model (about 90 MB) downloads in the background the first
time the server starts, with progress shown in the app. Setup also asks where you want the
search index to run: on this machine, in an Ollama you already run, or at a hosted provider
you trust.

**SSL/TLS Support**: For corporate environments with custom CA certificates, see the [Corporate SSL Configuration](#corporate-ssl-configuration) section below.

---

## What It Does

Clio lets you:

1. **Upload Documents** - Drop in PDFs, Office docs, spreadsheets, code, audio, or images (books, papers, manuals, data, photos of whiteboards)
2. **Hybrid Search** - Combine semantic understanding with keyword precision in one retrieval workflow
3. **Privacy-Aware AI Options** (Optional) - Use private/local or external providers for reranking and answer synthesis based on data sensitivity
4. **Get Results** - Find relevant passages with page numbers and direct document links
5. **Scale Up** - Handle hundreds to thousands of documents locally

**Supported file types:** PDF, TXT, DOCX, CSV/XLSX, Markdown, HTML, JSON/JSONL, source code, audio (transcribed), images (PNG/JPG/WEBP/TIFF — described and transcribed by a vision model)

**Links too:** paste URLs into *Add sources → Link* and the server fetches each one — a web page is saved as HTML with its navigation and footer stripped, a link straight to a PDF or file is saved as that file — and indexes it like an upload. Only public hosts are fetched by default (`LINK_ALLOW_PRIVATE_NETWORKS=1` opens intranet links); `OFFLINE_MODE` turns the feature off.

**Example:**
- Query: *"How do I optimize database queries?"*
- Result: Points you to page 47 of your database textbook with a direct link

---

## Technology Stack

### Core Dependencies

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **fastapi** | 0.115.5 | Web framework that powers the REST API | `main.py` |
| **uvicorn** | 0.32.1 | ASGI server that runs FastAPI | `main.py` |
| **python-multipart** | 0.0.18 | Handles file uploads (PDFs) | `main.py` |

### Document Processing

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pypdf** | 5.1.0 | Extracts text from standard PDFs | `services/document_extractor.py` |
| **pdfplumber** | 0.11.4 | Extracts text from complex PDF layouts (columns, tables) | `services/document_extractor.py` |
| **python-docx** | 1.1.2 | Extracts text from DOCX files | `services/document_extractor.py` |
| **pandas** | 2.2.3 | Extracts data from CSV files | `services/document_extractor.py` |

**Why two PDF libraries?** We try pdfplumber first (handles complex layouts better), then fall back to pypdf if needed.

**Supported formats:** PDF, TXT, DOCX, CSV/XLSX, Markdown, HTML, JSON/JSONL, code, audio, images

### Search & Embeddings

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **sentence-transformers** | 3.3.1 | Converts text to semantic embeddings (vectors) | `services/embedder.py` |
| **faiss-cpu** | 1.9.0 | Fast similarity search over millions of vectors | `services/vector_store.py` |

**What are embeddings?** They convert text to numbers that capture meaning. Similar meanings = similar numbers, enabling semantic search.

### AI Integration (Optional)

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **anthropic** | 0.42.0 | Anthropic Claude API client for AI reranking and synthesis | `services/ai_service.py` |
| **openai** | 1.59.5 | OpenAI GPT API client for AI reranking and synthesis | `services/ai_service.py` |

**AI Features** (optional, requires API keys stored in browser localStorage):
- **Result Reranking**: Uses AI to re-order search results by relevance
- **Answer Synthesis**: Generates natural language answers from retrieved documents
- **Multi-Provider**: Run both Claude and OpenAI simultaneously to compare responses

### Configuration & Data

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pydantic** | 2.10.3 | Data validation for API requests/responses | `models/schemas.py` |
| **pydantic-settings** | 2.6.1 | Loads configuration from `.env` files | `config.py` |
| **python-dotenv** | 1.0.1 | Reads `.env` files | `config.py` |

### Frontend (Web Interface)

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **vue** | 3.5.27 | Progressive JavaScript framework for building UIs | `frontend/src/*.vue` |
| **vite** | 7.3.1 | Fast build tool and dev server | `vite.config.js` |
| **tailwindcss** | 4.0.0 | Utility-first CSS framework | `frontend/src/style.css` |
| **daisyui** | 5.5.17 | Tailwind CSS component library | UI components |
| **axios** | 1.13.4 | HTTP client for API requests | `frontend/src/components/*.vue` |

**Modern UI Stack:** Built with Vue 3 Composition API, styled with Tailwind CSS 4 and DaisyUI 5 components, bundled with Vite for lightning-fast development and production builds.

**Frontend Features:**
- Dark/light theme with persistent preference
- Sticky navigation tabs
- Search history with caching (localStorage-based)
- Real-time stats display (documents, pages, chunks)
- Multi-provider AI selection (Anthropic, OpenAI, INL HPC, and Ollama)
- Responsive design for mobile and desktop

### Development & Testing

| Package | Version | Purpose | Used In |
|---------|---------|---------|---------|
| **pytest** | 8.3.4 | Testing framework | `tests/` |
| **httpx** | 0.28.1 | HTTP client for testing APIs | `tests/` |

---

## Installation

### Prerequisites

- **Python 3.13** (highly recommended) or **Python 3.8+** (check: `python --version`)
- **4GB RAM minimum** (8GB recommended for large collections)
- **500MB disk space** (more for storing PDFs and indexes)

### Step 1: Install Dependencies

```bash
pip install -r requirements.txt
```

**What happens:** Downloads and installs the core packages. Takes 2-5 minutes.

Scanned-PDF OCR, audio transcription, and the PostgreSQL backend are optional
extras — add `-r requirements-ocr.txt`, `-r requirements-audio.txt`, or
`-r requirements-postgres.txt` if you need them. The app runs fine without
them and tells you which extra to install if you enable a feature that needs it.

### Step 2: Configure (Optional)

```bash
cp .env.example .env
```

**Default settings work fine for most users.** Only edit `.env` if you need to:
- Change the port
- Use a different embedding model
- Adjust chunk sizes
- Choose metadata storage (JSON vs SQLite)

### Step 3: Run the Server

```bash
python main.py
```

**First run:** the search model downloads in the background (about 90 MB, one to two
minutes) and the app shows a progress bar until it is ready. The core install runs embeddings
on a small ONNX runtime (`fastembed`); the PyTorch-based `sentence-transformers` backend, which
unlocks the wider model catalog, is optional:

```bash
pip install -r requirements-torch.txt   # optional: PyTorch backend, wider model catalog
```

Set `LOCAL_EMBEDDING_BACKEND=fastembed|sentence-transformers` to pick one explicitly; `auto`
(the default) uses `fastembed` when it serves the chosen model and PyTorch otherwise.

**You'll see:**
```
INFO - Loading embedding model: all-MiniLM-L6-v2
INFO - Initializing vector store (metadata: json)
INFO - Clio API ready
INFO - Uvicorn running on http://127.0.0.1:8473
```

**Access the API:**
- Interactive docs: http://localhost:8473/docs
- Health check: http://localhost:8473/health

To reach it from another machine, set `HOST=0.0.0.0` — and read
[Security Considerations](#security-considerations) first, since the app is
unauthenticated until you set `AUTH_PASSWORD`.

### MCP Integration

Clio now exposes an embedded HTTP MCP endpoint at `http://localhost:8473/mcp` when `ENABLE_MCP=true` (default).

Use **Connect** in the UI to:
- Enable or disable the MCP server without restarting
- Choose the default collection and retrieval settings used by MCP
- Keep MCP in search-only mode (recommended) to avoid duplicate model usage
- Optionally enable Ollama-backed synthesis for MCP when you explicitly want server-side answering
- **Sign in from the client** — with an OIDC identity provider configured (`IDENTITY_PROVIDER=oidc`,
  `MCP_PUBLIC_URL`), Claude Desktop, claude.ai, ChatGPT and `claude mcp login` attach with the
  endpoint URL alone: the app is the OAuth 2.1 resource server and your IdP signs people in
  ([docs/IDENTITY.md](docs/IDENTITY.md#oauth-for-mcp-clients)).
- **Generate a personal access token** — a self-serve bearer credential scoped to `/mcp` only, so
  connecting a client never requires editing `.env` or provisioning anything in Cloudflare. Pick an
  expiry (90 days by default, or never), optionally limit the token to specific collections, and
  revoke it from the same screen when a machine is retired; the table shows last use and call counts.
- **Let agents write back** — tick *Allow adding and updating sources* on a token and the agent gets
  a `write_document` tool: it can save markdown notes, summaries, JSON, or CSV into a collection
  (`create`, `replace`, or `append`), indexed like any upload and subject to the same content policy,
  attribution, and audit trail. Tokens are read-only unless you opt in.
- **ChatGPT connectors and deep research** — the server also exposes the two tools OpenAI's
  connector spec requires, `search` and `fetch`, over the same auth and collection scoping as every
  other tool, so a ChatGPT custom connector (or any client that only speaks that shape) works
  without a bridge.
- **Let agents manage the index** — `list_index_jobs` and `get_index_job` show what is being
  indexed; write-enabled tokens also get `reindex_document` (re-run extraction and embedding for
  one source as a tracked, cancellable job) and `update_document_metadata` (set the sensitivity
  label). Every tool schema carries its numeric limits and a human title, and every resource is
  named and typed, so clients render them properly.
- **See exactly what agents can do** — *What agents can do here* on the Connect tab lists the
  tools, resources and prompts read live from the server (`GET /api/mcp/catalog`), grouped by
  task and marked where a tool writes.
- Copy or download ready-made snippets for Claude Code `.mcp.json`, Claude Desktop
  `claude_desktop_config.json` (via `mcp-remote`), Cursor `.cursor/mcp.json`, Codex `config.toml`,
  VS Code `.vscode/mcp.json`, Windsurf `mcp_config.json`, and AnythingLLM
  `anythingllm_mcp_servers.json`. Newly created tokens are filled in; existing tokens require
  pasting the credential you saved.

For AnythingLLM, merge the entry into its storage `plugins/anythingllm_mcp_servers.json`
and reload MCP servers in AnythingLLM. The export uses its `streamable` transport.
Use an Clio URL reachable from the AnythingLLM host or container; a container's
`localhost` points to itself. See [AnythingLLM's MCP setup](https://docs.anythingllm.com/mcp-compatibility/overview).

### Step 4: Verify Setup

```bash
# Check health
curl http://localhost:8473/health

# Expected response:
{"status":"healthy","indexed_chunks":0}
```

---

## Frontend Development

The web interface is built with Vue 3 + Vite and located in the `frontend/` directory. The production build is automatically served by the FastAPI backend.

### Development Mode

To work on the frontend with hot-reload:

```bash
# Navigate to frontend directory
cd frontend

# Install Node.js dependencies (one-time setup)
npm install

# Start the Vite dev server
npm run dev
```

This starts the dev server at http://localhost:5173 with hot-reload. The backend must be running separately at http://localhost:8473.

### Building for Production

```bash
# From the frontend directory
cd frontend
npm run build
```

This compiles the Vue app into `frontend/dist/` (gitignored), which the FastAPI backend serves automatically at http://localhost:8473 — hashed assets get long-lived immutable cache headers, so repeat loads are fast and deploys are picked up immediately. Docker images build the frontend in a dedicated stage; `run.sh`/`run.bat` build it on first run.

### Frontend Stack

- **Framework**: Vue 3 with Composition API + Pinia for state management
- **Build Tool**: Vite 7.3.1
- **Styling**: Tailwind CSS 4.0 + DaisyUI 5.5
- **HTTP Client**: Axios
- **Features**:
  - Hybrid semantic + keyword search with configurable weighting and keyword highlighting
  - AI-powered answer synthesis (optional, with Claude/GPT)
  - Search history with caching (localStorage-based, case-insensitive)
  - Multi-file document upload (PDF, TXT, DOCX, CSV) with progress tracking
  - Document management with bulk delete functionality
  - Dark/light theme toggle with persistent preference
  - Sticky navigation tabs
  - Real-time stats in header (documents, pages, chunks)
  - Responsive design for mobile and desktop

---

## Configuration

Create a `.env` file to customize settings:

```bash
# Data storage
DATA_DIR=./data                          # Where documents and indexes are stored

# Embeddings — see "Choosing where embeddings run" below
EMBEDDING_PROVIDER=local                # local | google | mistral | voyage | jina | openrouter | openai | ollama | openai_compatible
EMBEDDING_MODEL=all-MiniLM-L6-v2        # model for the local provider
LOCAL_EMBEDDING_BACKEND=auto            # auto | fastembed (ONNX, light) | sentence-transformers (PyTorch)
REMOTE_EMBEDDING_MODEL=                 # model for any other provider (blank = its recommended one)
EMBEDDING_API_KEY=                      # blank = reuse the matching AI Providers key

# The LLM the whole deployment uses (optional — leave empty and each person
# connects their own provider in Settings). See docs/ONPREM.md.
AI_PROVIDER=                            # openai_compatible | ollama | anthropic | openai | ...
AI_BASE_URL=                            # required for openai_compatible, e.g. http://vllm.internal:8000/v1
AI_MODEL=                               # e.g. llama-3.3-70b-instruct
AI_API_KEY=                             # optional; empty for an endpoint needing no auth
AI_PROVIDER_LABEL=                      # what the UI calls it

# Text chunking
CHUNK_SIZE=600                          # Characters per chunk
CHUNK_OVERLAP=100                       # Overlap between chunks

# Search
DEFAULT_TOP_K=10                        # Default number of results
MAX_TOP_K=50                            # Maximum results allowed

# Server
HOST=127.0.0.1                          # This machine only. See Security below
                                        # before changing to 0.0.0.0.
PORT=8473
```

### AI Features (Optional)

Clio supports optional AI integration for enhanced search results:

**Features:**
- **Result Reranking**: AI re-orders search results by semantic relevance
- **Answer Synthesis**: AI generates natural language answers from retrieved documents
- **Multi-Provider**: Use Anthropic Claude, OpenAI GPT, INL HPC AI, and Ollama

**Setup:**
1. Open the web interface at http://localhost:8473
2. Navigate to the **Settings** tab
3. Select your AI provider (Anthropic, OpenAI, INL HPC, or Ollama)
4. Enter your API key (stored securely in browser localStorage, never sent to server)
5. Enable desired features:
   - **Reranking**: Improves result ordering (~$0.0005/search)
   - **Synthesis**: Generates AI answers (~$0.015/search)

**Multi-Provider Mode:**
- If both Anthropic and OpenAI keys are configured, you can select which provider(s) to use per search
- Compare responses side-by-side from both models
- Selection preference persists between searches

**Security Note:** API keys are stored only in your browser's localStorage and are sent directly to the AI providers. The Clio server never sees or stores your API keys.

**Get API Keys:**
- **Anthropic**: https://console.anthropic.com/
- **OpenAI**: https://platform.openai.com/api-keys
- **INL HPC**: Use your INL HPC API key for `https://api.hpc.inl.gov/llm/v1`

---

### Corporate SSL Configuration

For organizations using custom SSL certificates or corporate proxies:

**Option 1: Docker (built in)**

Place your corporate CA certificate(s) — `.crt` files — into the `certs/ca/` directory and rebuild; the standard Dockerfile installs anything it finds there into the image's system trust store, at both dependency-install and runtime stages (a no-op when the directory is empty):

```bash
cp /path/to/your/cert.crt certs/ca/
docker compose up -d --build
```

**Option 1b: Docker, no rebuild (prebuilt or air-gapped images)**

An image you loaded from a file or pulled from a registry can't have your CA
baked in, so the entrypoint installs whatever is mounted at `/certs/ca` on
every start — into the system trust store *and* certifi's bundle, which is
what the Python HTTP clients actually use (a CA in the system store alone
still gives `CERTIFICATE_VERIFY_FAILED` from an endpoint `curl` reaches
fine). `docker-compose.onprem.yml` mounts `certs/ca` already:

```bash
cp /path/to/your/cert.crt certs/ca/
docker compose -f docker-compose.onprem.yml restart clio
```

**Important Notes:**
- Certificate files must have `.crt` extension
- Multiple certificates can be placed in `certs/ca/`
- Use `certs/ca/` specifically, **not** `certs/`. The parent `certs/` directory holds the dev self-signed pair from `generate-cert.sh`; it is excluded from the build context so its private key never lands in an image and its certificate is never trusted as a CA. Only `certs/ca/` is copied in.

**Option 2: Python Direct Configuration**

For Python deployments, set the SSL certificate path:

```bash
# Add to .env
SSL_CERT_FILE=/path/to/your/cert.crt
# Or set environment variable
export SSL_CERT_FILE=/path/to/your/cert.crt
export REQUESTS_CA_BUNDLE=/path/to/your/cert.crt
```

**What this solves:**
- Corporate proxy SSL interception
- Custom CA certificates
- Internal certificate authorities
- SSL verification errors when downloading models or making AI API calls

**Security Note:** Certificate contents are gitignored in both `certs/` and `certs/ca/` (only the `.gitkeep` markers are tracked), so certificates and private keys are never committed. `certs/` is additionally excluded from the Docker build context; `certs/ca/` is the sole exception, since installing those CAs is the point.

---

## API Usage

### Upload Documents

```bash
# Upload PDFs
curl -X POST "http://localhost:8473/documents/upload" \
  -F "files=@document1.pdf" \
  -F "files=@document2.pdf"

# Upload other file types (TXT, DOCX, CSV)
curl -X POST "http://localhost:8473/documents/upload" \
  -F "files=@notes.txt" \
  -F "files=@report.docx" \
  -F "files=@data.csv"
```

**Response:**
```json
{
  "message": "Successfully indexed 2 document(s)",
  "documents_processed": 2,
  "total_pages": 45,
  "total_chunks": 123,
  "document_ids": ["abc123", "def456"]
}
```

**What happens:**
1. Documents are saved to `data/documents/`
2. Text is extracted from each file (page-by-page for PDFs, section-by-section for others)
3. Text is split into overlapping chunks
4. Each chunk is converted to an embedding
5. Embeddings are indexed with FAISS
6. Metadata is saved (JSON or SQLite)

### Search Documents

**Basic search:**
```bash
curl -X POST "http://localhost:8473/search" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning algorithms",
    "top_k": 5
  }'
```

**Search with AI features:**
```bash
curl -X POST "http://localhost:8473/search" \
  -H "Content-Type: application/json" \
  -H "X-AI-Key: your-api-key-here" \
  -d '{
    "query": "machine learning algorithms",
    "top_k": 5,
    "ai": {
      "provider": "anthropic",
      "rerank": true,
      "synthesize": true
    }
  }'
```

**Response (basic search):**
```json
{
  "query": "machine learning algorithms",
  "results": [
    {
      "filename": "ml-textbook.pdf",
      "page_number": 42,
      "text_snippet": "Supervised learning algorithms...",
      "similarity_score": 0.87,
      "document_id": "abc123",
      "chunk_id": "abc123_p42_c0",
      "pdf_url": "http://localhost:8473/documents/abc123/pdf",
      "page_url": "http://localhost:8473/documents/abc123/pdf#page=42"
    }
  ],
  "total_results": 5
}
```

**Response (with AI synthesis):**
```json
{
  "query": "machine learning algorithms",
  "results": [...],
  "total_results": 5,
  "synthesis": "Machine learning algorithms can be categorized into supervised, unsupervised, and reinforcement learning...",
  "ai_usage": {
    "provider": "anthropic",
    "features_used": ["rerank", "synthesis"],
    "total_input_tokens": 1523,
    "total_output_tokens": 287
  }
}
```

**Open PDF at specific page:**
```bash
# In browser (opens at page 42)
open "http://localhost:8473/documents/abc123/pdf#page=42"
```

### List Documents

```bash
curl "http://localhost:8473/documents"
```

### Download Document

```bash
# Download any document type
curl "http://localhost:8473/documents/abc123/pdf" -o output.pdf
```

### Inspect Indexed Chunks (Including OCR Fields)

```bash
# View stored chunks for a document
curl "http://localhost:8473/documents/abc123/chunks?include_fields=true"
```

Use this to inspect exactly what was indexed for OCR PDFs.  
For form-like documents, the response includes heuristic `extracted_fields` key/value pairs per chunk.

### Delete Document

```bash
curl -X DELETE "http://localhost:8473/documents/abc123"
```

**Response:**
```json
{
  "message": "Deleted document abc123",
  "filename": "ml-textbook.pdf",
  "chunks_deleted": 45,
  "pdf_deleted": true
}
```

**What happens:**
1. Document metadata is removed from the index
2. All associated chunks are deleted from the vector store
3. The document file is removed from `data/documents/`
4. Changes are persisted to disk

**Note:** Deletion is permanent and cannot be undone. The document file will be completely removed from the filesystem.

### Interactive Documentation

Visit http://localhost:8473/docs for a full interactive API playground.

---

## Troubleshooting

### Common Issues

#### 1. "Port 8473 already in use"

**Solution:**
```bash
# Change port in .env
echo "PORT=8001" >> .env
python main.py
```

#### 2. "No module named 'faiss'"

**Solution:**
```bash
pip install -r requirements.txt --force-reinstall
```

**Still failing?**
```bash
# Try installing faiss separately
pip install faiss-cpu
```

#### 3. "Model download is slow"

**Why:** First run downloads ~90MB model from HuggingFace.

**Solutions:**
- Wait 1-2 minutes (one-time download)
- Check your internet connection
- If behind corporate firewall, download manually:
  1. Get model from https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2
  2. Place in `~/.cache/torch/sentence_transformers/`

#### 4. "SSL certificate error" downloading the model

**Why:** Corporate TLS interception can break the HuggingFace download of the embedding model.

**Error looks like:**
```
SSL: CERTIFICATE_VERIFY_FAILED
```

**Solutions:**

1. **Install your corporate CA** — see [Corporate SSL Configuration](#corporate-ssl-configuration) below.

2. **Use the model cache from another machine:**
   Copy the folder `~/.cache/huggingface/hub/models--sentence-transformers--all-MiniLM-L6-v2/` from a working machine to the same location on the problem machine.

3. **Run fully offline** — pre-fetch the models with `scripts/prefetch_offline_models.py` and set `OFFLINE_MODE=true`. See [docs/AIRGAP.md](docs/AIRGAP.md).

#### 5. "Document extraction failed"

**Possible causes:**
- **PDFs:** Corrupted file, scanned images (no text layer), or protected/encrypted
- **DOCX:** Corrupted file or unsupported Word version
- **CSV:** Encoding issues or malformed CSV
- **TXT:** Encoding issues

**Solutions:**
```bash
# For PDFs - check with another tool
pdfinfo yourfile.pdf

# For PDFs - try converting first
pdftk input.pdf output output.pdf

# For text files - check encoding
file -i yourfile.txt
```

#### 5a. "Scanned/image PDF indexes with 0 chunks"

This means text extraction returned empty content and OCR could not recover text.

1. Enable OCR in **Settings → OCR** and pick an engine:
   - **Vision AI** (recommended): uses a vision-capable model from an AI provider you've already configured — most accurate for complex layouts and degraded scans.
   - **Local**: free/offline via Tesseract (`pip install -r requirements-ocr.txt`; on bare-metal Windows also install **Tesseract OCR** and **Poppler** and add them to `PATH` — the Docker image ships both preinstalled). For the highest-quality local OCR, add Docling (`pip install -r requirements-docling.txt`, or build the image with `--build-arg WITH_DOCLING=1`) — it's the heavyweight optional and is not in the default hosted image.
2. Re-index the PDF after changing OCR settings.

#### 6. "Out of memory"

**For large PDFs or many documents:**

```bash
# Reduce chunk size (creates more, smaller chunks)
echo "CHUNK_SIZE=400" >> .env

# Use smaller embedding model
echo "EMBEDDING_MODEL=paraphrase-MiniLM-L3-v2" >> .env
```

#### 7. "Search returns no results"

**Checklist:**
1. Are documents uploaded? Check: `curl http://localhost:8473/documents`
2. Check health: `curl http://localhost:8473/health`
3. Try broader query: "database" instead of "postgresql query optimization"
4. Check logs for errors: Look at terminal output

#### 8. "Docker container crashes"

**Solution:**
```bash
# Check logs
docker-compose logs clio

# Increase memory limit in docker-compose.yml
services:
  clio:
    mem_limit: 4g
```

### Docker-Specific Issues

#### "Cannot connect to Docker daemon"

```bash
# Start Docker Desktop (Windows/Mac)
# Or start Docker service (Linux)
sudo systemctl start docker
```

#### "Permission denied accessing data folder"

```bash
# Fix permissions
chmod -R 777 ./data
```

#### "Container keeps restarting"

```bash
# Check what's wrong
docker-compose logs -f clio

# Common fix: Remove old containers
docker-compose down
docker-compose up -d
```

### Python-Specific Issues

#### "Python version too old"

```bash
# Check version
python --version  # Need 3.8+, Python 3.13 highly recommended

# Upgrade Python or use pyenv
pyenv install 3.13
pyenv local 3.13
```

#### "ModuleNotFoundError"

```bash
# Make sure virtual environment is activated
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Reinstall dependencies
pip install -r requirements.txt
```

#### "Permission denied on Windows"

**Run as administrator or:**
```bash
pip install --user -r requirements.txt
```

---

## Advanced Topics

### Performance & Scalability

**Current capacity:**
- 100-1,000 documents: Works great
- 1,000-10,000 documents: Recommend SQLite metadata storage
- 10,000+ documents: Consider FAISS IVF index or pgvector

**Memory usage estimates:**

| Documents | Chunks | RAM Required |
|-----------|--------|--------------|
| 100       | ~10K   | ~200 MB      |
| 1,000     | ~100K  | ~500 MB      |
| 10,000    | ~1M    | ~3 GB        |

**Speed up indexing:**
```bash
# Larger chunks = fewer chunks = faster
echo "CHUNK_SIZE=1000" >> .env
echo "CHUNK_OVERLAP=150" >> .env
```

### Choosing where embeddings run

Every document chunk and every search query is turned into a vector by an
embedding model. By default that model runs inside the app on the server's
CPU through a small ONNX runtime — free and private, and light enough for a
laptop. First-run setup asks where the index should run and shows the
download as it happens; **Settings → Indexing → Embedding** changes it later
(changing the model re-embeds the collection). Good local choices:

| Model | Size | Notes |
|---|---|---|
| `all-MiniLM-L6-v2` (default) | 90 MB | Fast on any CPU, English |
| `BAAI/bge-small-en-v1.5` | 130 MB | Same size class, noticeably better retrieval |
| `nomic-ai/nomic-embed-text-v1.5` | 270 MB | 8k context, strong on code and long passages |
| `jinaai/jina-embeddings-v2-base-code` | 320 MB | Built for source code |
| `intfloat/multilingual-e5-small` | 470 MB | 100 languages |

Or move the work to an Ollama you already run, or to a hosted API:

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

**Picking a local model** (any [sentence-transformers](https://www.sbert.net/docs/pretrained_models.html) name works):
```bash
echo "EMBEDDING_MODEL=all-mpnet-base-v2" >> .env        # better quality, slower
echo "EMBEDDING_MODEL=paraphrase-MiniLM-L3-v2" >> .env  # faster, less accurate
```

### Security Considerations

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

**For a team deployment, put an SSO proxy in front** (Cloudflare Access,
Tailscale, oauth2-proxy, Authelia) and bind the app so the proxy is the only
route in. Colleagues get in with the SSO they already have — no password to
type, share, or rotate — and you get per-person revocation and an access log
without writing any code. Full walkthrough, including how to verify the bypass
is closed: **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)**.

By default everyone who gets in sees the whole corpus. With a verified
identity source — Cloudflare Access, your own OIDC provider, or an
authenticating proxy ([docs/IDENTITY.md](docs/IDENTITY.md)) — set
`PRIVATE_COLLECTIONS=true` for per-person collections: each collection is
visible only to its owner until shared (read or readwrite share links),
enforcement covers every entry point — search, documents, chat, and the
`/mcp` tools — and everything created before the flag stays in a team tier
everyone sees. The mode refuses to start without an identity source it can
enforce. (The old `ENABLE_MULTI_USER` flag also refuses to start: it filtered
the collection list without enforcing anything.) Details:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**MCP clients are the same people.** A personal token carries the identity of
whoever minted it and only ever works on `/mcp`. An OAuth sign-in from Claude
Desktop, claude.ai, ChatGPT or Claude Code is verified by the same rules as a
browser session, so an agent sees exactly the collections its person can, and
`MCP_AUDIT_TOOL_CALLS=true` records which credential read which document.

**Content governance.** Once several people can add sources, every document
records who added it, every upload / deletion / share / token / admin action
lands in an audit trail, anyone can report a document, and admins can remove
one and block its hash from being re-added. Documents are scanned at ingest
by local rule packs (abuse-material indicators, attack planning, weapons and
drug trade, explicit content, credential dumps, secrets); `CONTENT_POLICY_ACTION`
decides whether a finding flags, quarantines (hides until approved) or rejects
the document. Collections carry a sensitivity label — `restricted` ones cannot
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
   chat, search and registration are on by default, an MCP class and a daily
   provider-token budget per person are opt-in, and each collection has a
   storage cap (`RATE_LIMIT_*`, `CHAT_DAILY_TOKEN_BUDGET`,
   `COLLECTION_STORAGE_LIMIT_BYTES`). Tune them for your model's capacity; a
   proxy in front can add coarser protection.
4. **Air-gapped environments** — set `OFFLINE_MODE=1` to disable cloud AI
   providers and model downloads entirely (see [docs/AIRGAP.md](docs/AIRGAP.md))

Stored credentials (the OCR vision key) are masked in `GET /api/config` and
never returned to a client.

### Backup & Recovery

**What to backup:**
```bash
# Documents (all file types)
data/documents/

# JSON metadata storage
data/indexes/json/faiss.index
data/indexes/json/metadata.json
data/indexes/json/embeddings.npy

# SQLite metadata storage
data/indexes/sqlite/faiss.index
data/indexes/sqlite/metadata.db
```

**Backup script:**
```bash
#!/bin/bash
tar -czf clio-backup-$(date +%Y%m%d).tar.gz data/
```

**Restore:**
```bash
tar -xzf clio-backup-20240203.tar.gz
python main.py
```

---

## Project Structure

```
clio/
├── main.py                    # App assembly: middleware, lifespan, routers, frontend serving
├── config.py                  # Settings from .env
├── requirements.txt           # Python dependencies
│
├── api/                       # HTTP endpoints — one router module per domain
│   ├── deps.py               # Shared helpers/state (get_indexer, expertise store)
│   ├── documents.py          # Upload, indexing, document management
│   ├── search.py             # Semantic search, facets, embeddings
│   ├── chat.py               # Chat, streaming chat, ask, provider config
│   ├── artifacts.py          # Source-grounded document generation
│   ├── collections.py        # Collection CRUD + re-indexing
│   ├── mcp.py                # MCP server configuration
│   ├── sharing.py            # Users + collection sharing
│   ├── expertise.py          # Expertise packs
│   └── system.py             # Health, capabilities, config, local file pickers
│
├── models/                    # Data models
│   └── schemas.py            # Pydantic request/response models
│
├── services/                  # Business logic
│   ├── document_extractor.py # Extracts text from documents (PDF, TXT, DOCX, CSV)
│   ├── chunker.py            # Splits text into chunks
│   ├── embedder.py           # Generates embeddings
│   ├── vector_store.py       # FAISS index with SQLite metadata
│   ├── metadata_store.py     # SQLite metadata storage
│   ├── ai_service.py         # AI provider abstraction (Anthropic, OpenAI, INL HPC, Ollama)
│   └── indexing/
│       └── indexer.py        # Orchestrates indexing pipeline
│
├── frontend/                  # Vue 3 web interface
│   ├── index.html            # HTML entry point
│   ├── package.json          # Node.js dependencies
│   ├── vite.config.js        # Vite build configuration
│   ├── tailwind.config.js    # Tailwind CSS configuration
│   └── src/
│       ├── main.js           # Vue app entry point
│       ├── App.vue           # Root Vue component with sticky tabs & theme toggle
│       ├── style.css         # Global styles (Tailwind imports)
│       ├── stores/           # Pinia stores (collections, chat, search, jobs, user)
│       ├── components/
│       │   ├── ChatTab.vue        # Grounded chat (primary surface)
│       │   ├── SourcesSidebar.vue # Source upload & management
│       │   ├── StudioSidebar.vue  # Studio panel (generate, tables, notes)
│       │   ├── SearchTab.vue      # Search interface with history
│       │   ├── MCPTab.vue         # MCP server status & setup
│       │   └── SettingsTab.vue    # AI API keys & settings
│       └── dist/             # Built frontend (gitignored; generated by npm run build)
│
├── tests/                     # Test suite
│   └── test_api.py           # API tests (placeholder)
│
├── data/                      # Runtime data (gitignored)
│   ├── documents/            # Uploaded documents (PDF, TXT, DOCX, CSV)
│   └── indexes/              # FAISS + metadata
│       ├── json/             # JSON metadata storage
│       └── sqlite/           # SQLite metadata storage
│
├── certs/                     # Dev self-signed TLS pair (gitignored, excluded from Docker builds)
│   └── ca/                    # Corporate CA certs (.crt) — auto-installed by Docker build
│
├── .env.example              # Configuration template
├── Dockerfile                # Multi-stage image: frontend build + deps build + slim runtime (OCR included)
└── docker-compose.yml        # One-command deployment (docker compose up -d)
```

---

## Example: Python Client

```python
import requests

BASE_URL = "http://localhost:8473"

# Upload documents (PDF, TXT, DOCX, CSV)
with open("document.pdf", "rb") as f:
    response = requests.post(
        f"{BASE_URL}/documents/upload",
        files={"files": f}
    )
    print(response.json())

# Upload multiple file types at once
files = [
    ("files", open("notes.txt", "rb")),
    ("files", open("report.docx", "rb")),
    ("files", open("data.csv", "rb"))
]
response = requests.post(f"{BASE_URL}/documents/upload", files=files)
print(response.json())

# Search
response = requests.post(
    f"{BASE_URL}/search",
    json={"query": "machine learning", "top_k": 5}
)
results = response.json()["results"]

# Open first result in browser
if results:
    import webbrowser
    webbrowser.open(results[0]["page_url"])
```

---

## FAQ

**Q: Can I use this on Windows?**
A: Yes! Python and Docker both work on Windows.

**Q: Does it support other file formats?**
A: PDF, TXT, DOCX, CSV/XLSX, Markdown, HTML, JSON/JSONL, source code, audio (transcribed), and images (described and transcribed by a vision model). Scanned PDFs are OCRed when OCR is enabled.

**Q: Can I use it offline?**
A: Yes. Search and embeddings are local after the first run (the model downloads once). For a deployment that must never reach the internet, build the offline bundle and run with `OFFLINE_MODE=1` ([docs/AIRGAP.md](docs/AIRGAP.md)); answers then come from a model you host.

**Q: How accurate is semantic search?**
A: Very good for finding concepts, not exact strings. ~80-90% accuracy for most queries.

**Q: Can I delete the embedding model cache?**
A: It's in `~/.cache/torch/sentence_transformers/`. You can delete it but it'll re-download.

**Q: What's the largest PDF it can handle?**
A: Tested up to 1000+ pages. Limited by RAM.

**Q: Can multiple users access it?**
A: Yes. The simplest shape is a shared password (`AUTH_PASSWORD`), where everyone sees the same corpus. For per-person collections, sharing, an audit trail and self-serve registration, give it a verified identity source — Cloudflare Access, your own OIDC provider, or an authenticating proxy — and set `PRIVATE_COLLECTIONS=true` ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/IDENTITY.md](docs/IDENTITY.md)).

**Q: Can Claude Desktop, claude.ai or ChatGPT use it?**
A: Yes, when the deployment has an identity provider. They add the `/mcp` URL as a custom connector and sign in there; the app is the OAuth resource server and your IdP does the login. Claude Code, Codex, VS Code and AnythingLLM can do the same or use a personal token from Connect ([docs/IDENTITY.md](docs/IDENTITY.md#oauth-for-mcp-clients)).

**Q: Can an agent add to a collection, not just read it?**
A: Yes, with a personal token minted with *Allow adding and updating sources*: the `write_document` tool creates, replaces or appends text sources, indexed and audited like an upload. There is deliberately no delete tool.

**Q: Does it need a cloud AI provider?**
A: No. Search works with no model at all. For answers, use any provider you trust — Anthropic, OpenAI, Gemini, OpenRouter, Ollama Cloud — or a model you run yourself (Ollama, vLLM, LM Studio, llama.cpp, any OpenAI-compatible endpoint), set once for the whole deployment with `AI_PROVIDER` ([docs/ONPREM.md](docs/ONPREM.md)).

---

## License

Licensed under the [MIT License](LICENSE). You're free to use, modify, and
deploy this anywhere — including commercially and inside internal systems —
provided the copyright notice and the license text travel with the software.

---

## Contributing

This is a reference implementation. Feel free to fork and adapt for your needs.

---

**Clio** — The record remembers.
