# Roadmap

Forward-looking work for the general-purpose (`master`) Clio tool. Shipped items are not listed — check the git log. Financial-analysis roadmap items live on the `fintech` branch.

## Guiding frame

Clio owns the trustworthy data layer; the intelligence layer lives upstream in whatever LLM the user trusts. Our job: make a user's own documents and tabular data faithfully agent-queryable, and never be silently wrong. Reached through Clio's own chat UI by default, or an external MCP client when the user prefers one.

## Library — ingestion & context

- **Git repository indexing** — local trees shipped 2026-10-08: sixty-plus languages chunked by symbol with lenient parsing (syntax errors, unbalanced braces, odd encodings and minified files still index), name-based detection for Makefile/Dockerfile-style files, binary/lockfile/build-output skipping, and symbol name + line range kept on every chunk. Remaining: remote clone, and the relative path as first-class metadata (today it survives only in the flattened filename).
- **Watch-folder sync** — incremental folder sync shipped 2026-10-08: re-syncing a folder hashes every file, skips the unchanged ones, replaces changed ones, and optionally prunes documents whose file is gone (`POST /documents/sync-folder`, remembered per collection with *Sync*/*Prune* in the Sources panel). Remaining: a server-side watcher that runs the sync automatically when files land.
- **Richer spreadsheet handling** — multi-sheet workbooks and more complex table structures beyond flat CSVs.

## Notebook — synthesis

- **Multi-document synthesis** — answers that explicitly cross-reference several sources rather than retrieving isolated chunks.
- **Side-by-side source attribution** — clicking a citation scrolls the original document to the exact highlighted passage.
- **Thematic collections** — group documents into notebooks with their own shared Expertise (research frameworks).

## Toolbox — extensibility

- **MCP expansion** — let external agents trigger re-indexing and metadata updates, not just read documents (`write_document` shipped 2026-09-11; cross-collection `research_documents`, `corpus_version` freshness tokens and MCP prompts shipped 2026-09-17; `reindex_document`, `update_document_metadata`, `list_index_jobs`/`get_index_job`, ChatGPT-shape `search`/`fetch`, schema limits and titles on every tool, and a live `GET /api/mcp/catalog` shipped 2026-10-08). Progress notifications during long research calls need the streamable transport switched from JSON responses to SSE, which is a deployment-affecting change to make deliberately.
- **PWA** — a web manifest and theme-color meta shipped 2026-09-12 (installable from the browser menu); an offline service worker is the remaining step.

## Access & limits

- **Online registration** — shipped 2026-09-12: `/register` (approval or open mode, domain allowlist, seat cap). Expiring, collection-limited personal MCP tokens shipped 2026-09-17. Pluggable identity shipped 2026-09-17: `IDENTITY_PROVIDER=oidc` (your own IdP) or `trusted_header` (an authenticating proxy) alongside Cloudflare Access, plus `MCP_AUDIT_TOOL_CALLS` for auditing agent reads. OAuth 2.1 on `/mcp` shipped 2026-09-17: RFC 9728 metadata and the Bearer challenge delegate to the site's IdP, so Claude Desktop, claude.ai, ChatGPT and `claude mcp login` attach with the endpoint URL alone (`MCP_PUBLIC_URL`, optional `MCP_OAUTH_SCOPE`, or `MCP_OAUTH_ISSUER` for a Cloudflare deployment). ChatGPT `search`/`fetch` aliases shipped 2026-10-08. Next: per-tool token scopes, group-derived collection access, and an admin token fleet view.
- **Per-collection storage cap** — shipped 2026-09-12 (5 GiB default, `COLLECTION_STORAGE_LIMIT_BYTES`). Next: per-user totals across collections, and an admin view of storage by collection.

## From the September 2026 product assessment (docs/PRODUCT_ASSESSMENT.md)

Shipped 2026-09-12: the answer cache keys on the whole request (corpus version, guide, attached instructions, provider/model, retrieval settings, source selection) and the source selection is enforced end-to-end (retrieval, tool calls, table queries, overview, citations, reports, cache). Still open, in priority order:

- **Retrieval benchmark** — a versioned corpus with answerable, unanswerable, exact-identifier, negation, date, table and cross-user cases; report recall@k, citation correctness, abstention and latency.
- **Privacy and authorization review** — exercise every API/tool/resource as owner, reader, outsider, revoked token and anonymous; confirm egress for embeddings, OCR, reranking and chat.
- **One dependable connector** — watched folder or SharePoint, with incremental sync, deletion propagation, resumable jobs and visible failure state.
- **Reproducible deployment** — locked dependencies, validated container + Postgres path, backup/restore and upgrade drills.

## Always-on constraints

- Raw data passes through by default; semantic layers exist only where Clio itself must act deterministically (aggregations, type coercion).
- When inference fails, degrade to raw-data tools and let the LLM handle semantics — never guess and pretend.
