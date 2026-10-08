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
  Docling models load from the local cache only; a missing model fails fast
  with instructions instead of hanging on network retries.
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

`OFFLINE_BUNDLE=1` bakes every runtime model into the image via the prefetch
script, including the default embedding model (`all-MiniLM-L6-v2`) and the
reranker (both are required; the run fails if either cannot be fetched). The
embedding model is also gated separately by `BAKE_EMBEDDING`, but that arg
only matters for the *default* image — it is already covered here, so you do
not need to pass it with `OFFLINE_BUNDLE=1`:

| Model | Purpose | Approx. size |
|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | Search reranker | ~90 MB |
| `faster-whisper base` | Audio transcription | ~150 MB |
| Docling layout + TableFormer | Local OCR of scanned PDFs | ~500 MB |

To bake a different Whisper size: `--build-arg WHISPER_MODEL=small`.

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
carry across.

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
   - `~/.cache/docling/` (if using local OCR)

3. Inside the air gap, set in `.env`:

   ```bash
   OFFLINE_MODE=true
   AUTH_PASSWORD=<a-strong-shared-secret>
   ```

   and start with `python main.py` (do not re-run the bootstrap scripts — they
   try to reinstall dependencies).

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

- [ ] `OFFLINE_MODE=1` set (verify: `curl http://<host>:8473/health` reports
      `"offline_mode": true`)
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
| Reranking | ✅ | With baked/pre-seeded model |
| OCR of scanned PDFs | ✅ | Docling/Tesseract locally; vision-LLM OCR only via local Ollama |
| Audio transcription | ✅ | Local Whisper, with baked/pre-seeded model |
| Grounded chat | ✅ | Via local Ollama or self-hosted OpenAI-compatible server |
| MCP endpoint for internal agents | ✅ | Embedded, in-process |
| Cloud AI providers | 🚫 | Deliberately disabled in offline mode |
