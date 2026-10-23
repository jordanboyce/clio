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

Clio is part of the DOE **Prometheus** ecosystem: Prometheus brings the fire
(compute, models); Clio keeps the record (grounded, citable knowledge). The
name — Clio, Muse of history, daughter of Mnemosyne — leaves room for a future
family of related tools under the Mnemosyne umbrella.

[Deployment](docs/DEPLOYMENT.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Roadmap](docs/ROADMAP.md) · [Agentic research guide](docs/AGENTIC_RESEARCH.md) ·
[Product assessment](docs/PRODUCT_ASSESSMENT.md)

---

## Table of Contents

- [Quick start](#quick-start)
- [Connect an AI client](#connect-an-ai-client)
- [If others can reach it](#if-others-can-reach-it)
- [Where people run it](#where-people-run-it)
- [What people do with it](#what-people-do-with-it)
- [More ways to deploy](#more-ways-to-deploy)
- [Configuration](#configuration)
- [Frontend development](#frontend-development)
- [API Usage](#api-usage)
- [Troubleshooting](#troubleshooting)
- [Advanced Topics](#advanced-topics)
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
2. **Pick your client and copy the snippet.** Coding agents and IDEs:
   Claude Code, Codex, Pi, Gemini CLI, Cursor, VS Code, Copilot CLI,
   Windsurf, Zed, Cline, Roo Code, Kiro, OpenCode. Desktop and chat apps: Claude
   Desktop, claude.ai, ChatGPT, Grok, Hermes Agent, Goose, AnythingLLM,
   LibreChat, Open WebUI. Automation and APIs: n8n, LangChain, the Python
   and TypeScript MCP SDKs, the OpenAI Responses API, the Anthropic MCP
   connector, the xAI API, and a generic entry (URL, bearer header, first
   call to make) for everything else. Clients that only speak the older
   HTTP+SSE transport use `/mcp/sse` with the same header.
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
| **Query spreadsheets, not just prose** | Ask, MCP | CSV/XLSX become typed tables; counts, sums, filters, and rankings run as SQL, never as guessed passage search. |
| **Turn a collection into a document** | Generate (under More) | Source-grounded briefings, summaries, and reports with citations to review before sharing. |
| **Teach it your house style or domain** | Expertise (under More) | Expertise packs add instructions and terminology per collection, versioned alongside the corpus. |
| **Give your AI assistant a private knowledge source** | Connect | Claude Desktop, claude.ai, ChatGPT, Grok and Claude Code sign in through your identity provider; Codex, VS Code, Cursor, Windsurf, AnythingLLM, Hermes Agent, Claude Code and any MCP SDK can also use a personal token. Agents can write notes and summaries back when a token allows it. |
| **Index scanned paper, photos, audio, code** | Add sources | OCR (local Tesseract/Docling or a vision model), Whisper transcription, code-aware chunking, image description. |
| **Index a whole source tree** | Add sources | Sixty-plus languages and config formats chunk by symbol (functions, classes, SQL objects, Terraform resources, shell functions, Vue/Svelte scripts), with `Makefile`, `Dockerfile` and friends recognised by name. Parsing is lenient: syntax errors, odd encodings and minified files still index; binaries, lockfiles, build output and vendored dependencies are skipped. Every passage keeps its symbol name and line range. |
| **Keep a folder or repository in sync** | Add sources | Point Clio at a folder or a git repository once; every later sync hashes the tree, skips unchanged files, re-indexes changed ones, and can prune documents whose file was deleted. *Watch* re-syncs on an interval and every real change lands in the Updates feed (and an optional signed webhook). |
| **Share an indexed collection with another Clio** | Collections | *Export* writes one `.clio.zip` (extracted text, pages, tables, vectors, and which embedding model made them); *Import* previews it and reuses the vectors when the model matches. |
| **Move fast with the keyboard** | Everywhere | `⌘K` / `Ctrl+K` opens a command palette for every destination, collection, recent chat, theme and action; `?` shows the shortcuts. |
| **Keep a shared corpus accountable** | More → Admin | Attribution on every document, an append-only audit trail (including agent reads if enabled), content-policy scanning with flag/quarantine/reject, sensitivity labels, blocklists, an acceptable-use gate. |

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
| `COLLECTION_STORAGE_LIMIT_BYTES` | Most a single collection may hold (5 GiB default) |
| `OFFLINE_MODE`, `CLIO_PROFILE` | No cloud providers, no downloads; refuse to start when unsafely exposed |
| `IDENTITY_PROVIDER`, `PRIVATE_COLLECTIONS` | Your own sign-in, and per-person collections enforced against it |
| `DATA_DIR` | Where documents, indexes and the app database live (`./data`) |

Provider API keys entered in the browser stay in that browser; a team key
saved under Settings → AI Providers is stored server-side for everyone on the
deployment.

## Frontend development

```bash
cd frontend && npm install && npm run dev    # http://localhost:5173, proxies to the backend on :8473
cd frontend && npm run build                 # production build into frontend/dist (gitignored; served by FastAPI)
```

Vue 3 + Vite + Pinia + Tailwind/DaisyUI. The backend serves `frontend/dist`
with immutable cache headers for hashed assets; Docker builds it in a
dedicated stage and `run.sh` / `run.bat` build it on first run.

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
- If behind corporate firewall, pre-seed the model from a connected machine and copy the
  cache folder over: `<DATA_DIR>/models/fastembed/` for the default ONNX backend, or the
  Hugging Face cache (`~/.cache/huggingface/hub/`) for the PyTorch backend. See
  [docs/AIRGAP.md](docs/AIRGAP.md). The app also shows a **Download now** button once the
  network is fixed, and Settings → Indexing → Embedding can switch to Ollama or a hosted
  provider instead.

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

**Current capacity:** one process, FAISS flat indexes and SQLite per
collection; hundreds to a few thousand documents per collection are routine,
and `scripts/mcp_load.py` measures what a given host serves concurrently
([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#capacity--scaling)).

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

| Model | Size | Backend | Notes |
|---|---|---|---|
| `all-MiniLM-L6-v2` (default) | 90 MB | ONNX or PyTorch | Fast on any CPU, English |
| `BAAI/bge-small-en-v1.5` | 130 MB | ONNX or PyTorch | Same size class, noticeably better retrieval |
| `nomic-ai/nomic-embed-text-v1.5` | 270 MB | ONNX or PyTorch | 8k context, strong on code and long passages |
| `jinaai/jina-embeddings-v2-base-code` | 320 MB | ONNX or PyTorch | Built for source code |
| `paraphrase-multilingual-MiniLM-L12-v2` | 470 MB | ONNX or PyTorch | 50+ languages |
| `intfloat/multilingual-e5-small` | 470 MB | PyTorch only | 100 languages (`pip install -r requirements-torch.txt`) |

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
enforcement covers every entry point — search, documents, chat, and the
`/mcp` tools — and everything created before the flag stays in a team tier
everyone sees. The mode refuses to start without an identity source it can
enforce. (The old `ENABLE_MULTI_USER` flag also refuses to start: it filtered
the collection list without enforcing anything.) Details:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

**MCP clients are the same people.** A personal token only ever works on
`/mcp`; under an identity provider it carries the identity of whoever minted it. An OAuth sign-in from Claude
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
   chat and search are on by default, an MCP class and a daily
   provider-token budget per person are opt-in, and each collection has a
   storage cap (`RATE_LIMIT_*`, `CHAT_DAILY_TOKEN_BUDGET`,
   `COLLECTION_STORAGE_LIMIT_BYTES`). Tune them for your model's capacity; a
   proxy in front can add coarser protection.
4. **Air-gapped environments** — set `OFFLINE_MODE=1` to disable cloud AI
   providers and model downloads entirely (see [docs/AIRGAP.md](docs/AIRGAP.md))

Stored credentials (the OCR vision key) are masked in `GET /api/config` and
never returned to a client.

### Backup & Recovery

**What to back up:** the whole data directory (`DATA_DIR`, `./data` by
default, the `/app/data` volume in Docker). It holds everything stateful:

```bash
data/app.db                 # collections, settings, tokens, audit trail
data/answer_cache.db        # cached answers (safe to drop)
data/collections/<id>/      # one folder per collection: source files + indexes
data/collections/<id>/indexes/metadata.db   # chunks and metadata (SQLite)
data/collections/<id>/indexes/bm25.db       # keyword index (FTS5)
data/collections/<id>/indexes/*.index       # vectors (FAISS)
data/models/                # downloaded embedding/reranker models (re-downloadable)
```

Stop the server (or use `docker compose stop`) before copying: SQLite
write-ahead logs (`*-wal`) belong to the running process. A collection can
also be exported on its own as a `.clio.zip` from the Collections view.

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
│   ├── ai_service.py         # AI provider abstraction (Anthropic, OpenAI, Google, OpenRouter, Ollama, OpenAI-compatible)
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
A: The default ONNX backend keeps it in `<DATA_DIR>/models/fastembed/` (or `/opt/clio/models/fastembed/` inside the Docker image); the PyTorch backend uses the Hugging Face cache under `~/.cache/huggingface/hub/`. You can delete either; it re-downloads on the next start, with progress shown in the app.

**Q: What's the largest PDF it can handle?**
A: Tested up to 1000+ pages. Limited by RAM.

**Q: Can multiple users access it?**
A: Yes, but Clio has no accounts. Set a shared password (`AUTH_PASSWORD`) and everyone who knows it sees the same corpus. An organisation that already runs single sign-on can use its own OIDC provider or an authenticating proxy for per-person collections, sharing and an audit trail ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), [docs/IDENTITY.md](docs/IDENTITY.md)).

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
