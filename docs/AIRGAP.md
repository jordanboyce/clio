# Air-Gapped Deployment Guide

> Running on-prem against your own model endpoint, with or without a network?
> Start with **[ONPREM.md](ONPREM.md)** — it covers packaging the image,
> pointing the deployment at a private endpoint, and private CAs. This guide
> is the disconnected-network specialisation of it.

Clio is designed to run entirely inside a disconnected network: documents
are indexed, searched, and chatted with locally, and no data ever leaves the
environment. This guide covers preparing a deployment on a connected machine,
transferring it across the air gap, and verifying that nothing phones home.

## What OFFLINE_MODE guarantees

Setting `OFFLINE_MODE=1` enforces, at runtime:

- **Cloud AI providers are disabled.** Anthropic, OpenAI, Grok, Google, GitHub
  Models, OpenRouter, Ollama Cloud, and AWS Bedrock cannot be instantiated —
  every AI surface (chat, artifacts, vision OCR, schema inference, connection
  tests) goes through one factory, and it rejects them with a clear error.
  Only **local Ollama** and **self-hosted OpenAI-compatible endpoints** (vLLM,
  LM Studio, llama.cpp server, TGI) remain available.
- **No HuggingFace downloads.** `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`
  are set before any ML library loads. Embedding, reranker, Whisper, and
  Docling models load from the local cache only, and a missing model never
  hangs on network retries or stops the rest of the app
  ([what each one costs](#when-a-model-or-provider-is-out-of-reach)).
  The embedding model has two in-process backends — fastembed (ONNX, the
  default, cache under `DATA_DIR/models/fastembed`, or `/opt/clio/models/fastembed`
  in the image) and sentence-transformers (PyTorch, HF cache). Both read the
  same weights, so pre-seeding either is enough; `GET /api/embedding/status`
  reports `missing` with `can_download: false` when neither has the model.

Everything else in the stack is local by construction: FAISS + BM25 search,
SQLite storage, the embedded MCP server, and a frontend with zero CDN
resources, zero telemetry, and zero analytics.

## Option A — Docker (recommended)

### 1. Build the offline bundle on a connected machine

```bash
docker compose build --build-arg OFFLINE_BUNDLE=1
```

The default full image already holds the embedding model, the reranker and the
Whisper `base` speech model, so it needs no network at run time for any of them.
`OFFLINE_BUNDLE=1` adds what is left via the prefetch script, and unlike the
default build it fails if a required model cannot be fetched, so a build behind
a firewall cannot quietly produce an image without them:

| Model | Purpose | Approx. size | In the default full image |
|---|---|---|---|
| `all-MiniLM-L6-v2` | Search by meaning | ~90 MB | Yes |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Search reranker (fastembed ONNX export `Xenova/ms-marco-MiniLM-L-6-v2`, plus the PyTorch copy when sentence-transformers is installed) | ~90 MB each | Yes |
| `faster-whisper base` | Transcribing audio and video recordings | ~145 MB | Yes |
| Docling layout + TableFormer | Local OCR of scanned PDFs, beside the Tesseract already included | ~500 MB | No |

To bake a different Whisper size: `--build-arg WHISPER_MODEL=small`. To bake a
different embedding model, for example `BAAI/bge-small-en-v1.5` for a
[website export](STATIC_SEARCH.md):
`--build-arg EMBEDDING_MODEL=BAAI/bge-small-en-v1.5`, which also makes it the
image's default (see [Changing the embedding model](#changing-the-embedding-model)).

### 2. Export, transfer, load

```bash
docker save clio-clio | gzip > clio-image.tar.gz
# transfer via approved media, then inside the air gap:
docker load < clio-image.tar.gz
```

(Check the image name with `docker images` — compose names it
`<project-dir>-clio`.)

### 3. Run inside the air gap

Use `docker-compose.onprem.yml`, which never builds — it runs the image you
loaded — and copy `.env.onprem.example` to `.env`:

```bash
CLIO_IMAGE=clio:local
OFFLINE_MODE=1
AUTH_PASSWORD=<a-strong-shared-secret>
AI_PROVIDER=openai_compatible          # your model server, see below
AI_BASE_URL=http://vllm.internal:8000/v1
AI_MODEL=llama-3.3-70b-instruct
```

Then `docker compose -f docker-compose.onprem.yml up -d` and open
`http://<host>:8473`.

`scripts/package_image.sh --offline-bundle` does steps 1–2 and assembles the
compose file, the example `.env` and these guides into one directory to
carry across. Add `--embedding-model <id>` (`-EmbeddingModel` in
`package_image.ps1`) for an image built around another embedding model; the
bundle's `README.txt` and the image's `dev.clio.embedding-model` label record
which one it holds.

## Option B — Bare metal

1. On a connected machine with the same OS/Python, create the venv and build
   the frontend once (`./run.sh` or `run.bat` does both), then pre-fetch all
   models:

   ```bash
   python scripts/prefetch_offline_models.py
   ```

2. Transfer the whole project directory **including** `venv/`,
   `frontend/dist/`, and the model caches:
   - `<DATA_DIR>/models/fastembed/` — the default embedding backend
     (fastembed, ONNX). Seed it on the connected machine with
     `python -c "from fastembed import TextEmbedding; TextEmbedding('sentence-transformers/all-MiniLM-L6-v2', cache_dir='data/models/fastembed')"`
     (use the fastembed id of whichever `EMBEDDING_MODEL` you run; the
     directory is complete once it holds a `files_metadata.json`).
   - `~/.cache/huggingface/` (or wherever `HF_HOME` points) — the
     sentence-transformers backend and the reranker, only when
     `requirements-torch.txt` is installed
   - `<DATA_DIR>/models/whisper/` (if transcribing recordings). Seed it with
     `python -c "from faster_whisper.utils import download_model; download_model('base', cache_dir='data/models/whisper')"`,
     or set `WHISPER_MODEL` to a folder holding a model.
   - `~/.cache/docling/` (if using local OCR)

3. Inside the air gap, set in `.env`:

   ```bash
   OFFLINE_MODE=true
   AUTH_PASSWORD=<a-strong-shared-secret>
   ```

   and start with `python main.py` (do not re-run the bootstrap scripts — they
   try to reinstall dependencies).

## Changing the embedding model

### An image built around another model

The stock image bakes `all-MiniLM-L6-v2`. Every release since 1.4.1 also
publishes the same two images built around `BAAI/bge-small-en-v1.5` (the other
model a [website export](STATIC_SEARCH.md) has a browser build for):

```bash
docker pull ghcr.io/jordanboyce/clio:bge          # or :<version>-bge
docker pull ghcr.io/jordanboyce/clio:bge-slim     # or :<version>-bge-slim
```

A machine that can reach the registry can pull one and carry it across with
`docker save`. The published images bake the search model and reranker but not
the rest of the offline bundle (Whisper, OCR models). For those, or for another
model, build the bundle yourself:

```bash
./scripts/package_image.sh --embedding-model BAAI/bge-small-en-v1.5 --offline-bundle --tag 1.4.0-bge
./scripts/package_image.sh --embedding-model BAAI/bge-small-en-v1.5 --offline-bundle --slim --tag 1.4.0-bge-slim
#   Windows:  .\scripts\package_image.ps1 -EmbeddingModel BAAI/bge-small-en-v1.5 -OfflineBundle -Tag 1.4.0-bge
```

What that image does differently:

- It bakes the fastembed copy of the model into `/opt/clio/models/fastembed`
  (the full image also bakes the sentence-transformers copy), and the build
  checks that the app finds it there. A build that names its model fails if the
  model cannot be fetched, instead of producing an image without it.
- It sets `EMBEDDING_MODEL` to that model, so a container uses it with nothing
  in `.env`. Leave `EMBEDDING_MODEL` out of `.env` (`.env.onprem.example`
  leaves it commented out); `docker-compose.onprem.yml` passes it through only
  when you set it. A value you set still wins, but in an air gap only the
  baked model can load.
- It does **not** hold `all-MiniLM-L6-v2`. See which model an image holds with
  `docker inspect -f '{{ index .Config.Labels "dev.clio.embedding-model" }}' clio:1.4.0-bge`.

Images built without `--embedding-model` behave exactly as before.

### What happens to existing collections

Each collection records the model it was created with, and indexing and search
for it always use that model, never the server's default. Starting a bge image
on a data volume made with MiniLM therefore re-embeds nothing and never
searches MiniLM vectors with bge queries (both are 384-dimensional, so nothing
would catch the mix-up). The default collection follows a new default only
while it holds no documents.

New collections take the server's model, bge-small on a bge image.

On a bge image in an air gap, a collection still recorded as MiniLM cannot load
its model, so search by meaning for it answers 503 with a message naming the
missing model. Keyword search, and the default `auto` mode, which falls back to
keywords, keep working. Move it to bge as below, or keep it on an image that
holds MiniLM.

### Moving a collection to bge-small

Both ways re-embed from the collection's source files, so they need those files
(uploads are kept by default; a folder or git source must still be reachable).

**Clone (recommended).** The copy is built with the new model while the
original keeps answering with its own:

```bash
curl -X POST http://<host>:8473/api/collections/<id>/clone \
  -H "Authorization: Bearer <password>" -H "Content-Type: application/json" \
  -d '{"name": "Handbook (bge)", "embedding_model": "BAAI/bge-small-en-v1.5"}'
```

The answer carries the new collection and a `job_id`; follow it in Background
work. The guide, the sensitivity label and any classification marking travel
with the copy; publishing and shares do not. When the copy is ready, delete the
original if it is no longer needed.

**Re-index in place.** Change the collection's model, then rebuild it:

```bash
curl -X PUT http://<host>:8473/api/collections/<id> \
  -H "Authorization: Bearer <password>" -H "Content-Type: application/json" \
  -d '{"embedding_model": "BAAI/bge-small-en-v1.5"}'
curl -X POST http://<host>:8473/api/collections/<id>/reindex -H "Authorization: Bearer <password>"
```

Run the two together and do not rely on the collection's search until the job
finishes. In between it holds MiniLM vectors under a bge label: a server that
already had the collection open keeps searching with MiniLM, but one that opens
it afresh (after a restart, say) embeds queries with bge against those vectors
and returns poor results without any error. While the job runs the collection is
only partly searchable. If a re-index was interrupted, start it again. Clone
has none of these gaps.

Either way, check the result before exporting it for a website:
`GET /api/collections/<id>/export/static/preview` should list no blockers and
name `Xenova/bge-small-en-v1.5` as `browser_model`.

## Local LLM for chat

Grounded chat needs an LLM; search, indexing and the MCP tools do not. Any
OpenAI-compatible server on your network works — vLLM, llama.cpp,
LM Studio, TGI, a gateway — configured deployment-wide with `AI_PROVIDER` /
`AI_BASE_URL` / `AI_MODEL` so nobody sets it up per browser
([ONPREM.md](ONPREM.md)).

With Ollama specifically:

1. On a connected machine: `ollama pull llama3.1:8b` (or your chosen model,
   plus `nomic-embed-text` if you want Ollama-served embeddings).
2. Copy the Ollama model store across the gap (`~/.ollama/models` on
   Linux/macOS, `%USERPROFILE%\.ollama\models` on Windows).
3. Point Clio at it via `OLLAMA_BASE_URL` (the compose file defaults to
   the Docker host's Ollama at `host.docker.internal:11434`).

Search, indexing, and the MCP tools work with **no LLM at all** — chat is the
only feature that requires one.

## Hardening checklist

Set `CLIO_PROFILE=secure` and run `python scripts/preflight.py --probe`
([SECURE_PROFILE.md](SECURE_PROFILE.md)): it checks the items below, confirms
every model is already on the machine, and lists each outbound path the
configuration allows. The profile also makes Clio refuse to start when the
first three boxes are not true.

- [ ] `OFFLINE_MODE=1` set (verify: `curl -H "Authorization: Bearer <password>"
      http://<host>:8473/health` reports `"offline_mode": true`)
- [ ] `AUTH_PASSWORD` set — without it every endpoint, including MCP, is open
      to anyone who can reach the port
- [ ] `CORS_ALLOW_ORIGINS` left empty unless another web origin needs the API;
      list explicit origins rather than using `*`
- [ ] TLS enabled if traffic crosses a shared network segment
      (`SSL_CERTFILE`/`SSL_KEYFILE`, or terminate at a reverse proxy)
- [ ] Everyone who can reach the port can read the whole corpus — there is no
      per-user isolation (see [DEPLOYMENT.md](DEPLOYMENT.md)), so confirm the
      reachable set matches who is cleared for the material
- [ ] Verify zero egress: run a packet capture (`tcpdump`/firewall logs) while
      indexing a document, running a search, and chatting — the only traffic
      should be client↔Clio and Clio↔Ollama

## What works offline

| Feature | Offline? | Notes |
|---|---|---|
| Document indexing (PDF, DOCX, TXT, MD, CSV/XLSX, code) | ✅ | Fully local |
| Semantic + keyword + hybrid search | ✅ | FAISS/BM25, local embeddings |
| Reranking | ✅ | Automatic: on whenever the baked/pre-seeded model is present (`ENABLE_RERANKER` unset); the default image bakes it, and nothing downloads under `OFFLINE_MODE` |
| OCR of scanned PDFs | ✅ | Tesseract in the full image, on by default; Docling if baked; vision-LLM OCR only via local Ollama |
| Transcribing audio and video recordings | ✅ | Local Whisper; the full image includes the `base` model. Speech only: what appears on screen is not read |
| Grounded chat | ✅ | Via local Ollama or self-hosted OpenAI-compatible server |
| MCP endpoint for internal agents | ✅ | Embedded, in-process |
| Search on a static website | ✅ | Export it as a whole site (**Include the model**): it carries the model Clio embeds with and transformers.js, so neither the site nor its visitors need Hugging Face or a CDN ([STATIC_SEARCH.md](STATIC_SEARCH.md#a-site-that-carries-its-own-model)) |
| Cloud AI providers | 🚫 | Deliberately disabled in offline mode |

## When a model or provider is out of reach

A network can block a model hub or provider with or without `OFFLINE_MODE` being
set. Losing one takes away only what depends on it. The rest keeps working, and
`GET /api/system/readiness` reports what is missing.

| Missing | What still works | What stops | How it comes back |
|---|---|---|---|
| Embedding model or server | Sources still index, collection bundles still import, and search works by keyword | Search by meaning. A search that asks for `semantic` or `hybrid` explicitly answers 503 | When the model is there, the next search or upload in a collection starts adding the missing vectors from the stored text. Nothing is uploaded again |
| Reranker | Everything; results come back in retrieval order | Slightly better ordering of the top results | On when the model is on disk |
| Whisper model | Every other kind of source, including others in the same upload | Audio and video recordings are refused, with a message saying what to do | When the model is on disk, the next recording uses it (within a minute) |
| AI provider (chat model) | Indexing, search, citations and the MCP search tools | Written answers, AI-written artifacts, vision OCR | Connect local Ollama or an OpenAI-compatible server on your network |
| Vision or Docling OCR | Tesseract reads scanned pages in the full image | Figure descriptions and Docling's layout analysis | Connect a vision model, or bake Docling |

The status bar at the bottom of the app shows each of these as on, limited, off
or unavailable; click it for the reason and the fix. `GET /api/features` returns
the same list. It reads settings and the local disk only and contacts nothing.

**A custom embedding model.** All of this holds for a model no catalog knows,
and for your own embedding server being down. Clio does not guess how large that
model's vectors are. A collection writes no vector index until the model first
answers, then takes the model's size. From then on the vectors on disk fix the
size: a later model of another size is refused until you re-index, and the
vectors stay as they were.

**A network that blocks huggingface.co but has a mirror of it** (an Artifactory
or Nexus remote repository for Hugging Face) needs no copying: set `HF_ENDPOINT`
to the mirror's address and the search model, reranker and Whisper download from
there. The setup screen, the failure message and `GET /api/system/readiness` all
name the mirror instead of huggingface.co, so you can see which one is in use.
This is not offline mode: with `OFFLINE_MODE` set, nothing downloads from
anywhere.

**A firewall that drops packets** can take minutes to fail, where a refused
connection takes a moment. Nothing waits for it: a model that is not on disk is
fetched in the background, one attempt at a time, while the collection works by
keyword.

**Importing a collection bundle.** When a bundle's vectors can be reused, no
model is needed at all. When they cannot and no model can be reached, the import
still completes: its job reports `vectors: pending`, the collection is searchable
by keyword, and the vectors arrive the same way as for any other source.

The full image turns OCR on unless `ENABLE_OCR` is set or OCR was switched off
in Settings. Elsewhere it is off until turned on. While it is off, a PDF that is
only scanned pages is refused, with a message saying so. OCR takes a few seconds
per page, and `OCR_MAX_PAGES` and `OCR_MAX_FILE_MB` cap the cost.

A firewall that silently drops traffic is slower than `OFFLINE_MODE`: the first
attempt to reach a missing model waits for a timeout (10 to 20 seconds). On a
network that can never reach the hub, set `OFFLINE_MODE=1` so nothing tries.
