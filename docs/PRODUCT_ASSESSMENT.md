# Product assessment — September 12, 2026

> Historical (Sept 2026). Cloudflare-based access and online registration described here were later removed; see IDENTITY.md.

## Judgment

Clio is useful as a **self-hosted knowledge workspace with an MCP server**.
Its strongest audience is developers and small teams working with curated,
sensitive documents. Its useful differentiation is deployment control, local
models, document citations, and access to the same knowledge from external AI
clients. Position it around those jobs.

The source is more capable than its opening documentation suggested: hybrid
retrieval, reranking, multiple extraction paths, SQLite/Postgres backends,
streamed chat, reports, structured-table queries, collection sharing, governance,
and Streamable HTTP MCP are already implemented. Most of the immediate product
work is making these capabilities coherent and trustworthy.

This is a source review and regression-tested improvement, **not an enterprise
certification or a measured RAG-quality benchmark**. No production corpus,
live model server, real identity provider, or Postgres instance was supplied.
The product is suitable for a controlled pilot; broad enterprise readiness needs
the validation and remaining work below.

## Product boundary

The primary workflow is **add sources → ask or find → verify a passage**.
MCP exposes that knowledge to an AI client the user chooses. Three destinations:
Ask, Find, Connect. A collection is the unit of organization and sharing.

Reports and reusable instructions support this workflow. They do not need to
compete with it in primary navigation. Notes open on demand. Administration
and model/retrieval configuration are secondary operator tasks.

Avoid expanding this into a general agent platform, financial terminal, or media
production suite. An MCP client marketplace, audio/video overviews, and dozens
of connectors would substantially increase maintenance before proving the core
private-knowledge workflow.

## Competitive comparison

Comparison uses official documentation retrieved on the assessment date. Feature
availability depends on edition and deployment; documentation is not a benchmark.

| Capability | Clio in this branch | Reference / remaining gap |
|---|---|---|
| Source-grounded Q&A and citations | Implemented; local or hosted generation | NotebookLM grounds answers in notebook sources. Measure citation correctness and abstention on your own corpus. |
| Curated source workspaces | Collections, source lists, chat history, sharing | NotebookLM makes the notebook the main workspace. This branch simplifies navigation around that pattern. |
| Enterprise retrieval | Semantic, keyword and hybrid search, filters, optional reranking | Onyx offers connected search across workplace systems. Retrieval quality and large-corpus latency here remain unmeasured. |
| Persistent connectors | File/folder/repository ingestion paths | No equivalent to Onyx's broad recurring connector sync, deletion pruning, sync health, and source ACL syncing. |
| Permissions | Opt-in private collections, verified Access identity, read/readwrite shares, admin gates | Collection permissions are not source-system ACL synchronization. Onyx documents permission sync for selected connectors as an Enterprise feature. |
| Private deployment | Self-hosting, local embeddings/models, offline configuration | Control of deployment is useful, but hosted embeddings/chat still send text externally. Enforce egress at the network layer when required. |
| MCP server | Streamable HTTP, search/context/table tools, resources, personal tokens, optional writes | Onyx also exposes private search over MCP. This branch adds read/write tool annotations and fixes explicit-token handling on open deployments. |
| MCP client | External clients connect to Clio; configuration export includes AnythingLLM | Onyx and AnythingLLM can invoke external MCP servers. Clio can supply knowledge to those clients; hosting arbitrary external tools here is deferred. |
| Workspace and conversation sources | Persistent collections and document ingestion | AnythingLLM distinguishes thread attachments from embedded workspace documents. End-to-end selected sources, temporary attachments, and context-budget feedback remain gaps here. |
| Answer modes | Retrieval-backed chat and a separate passage search | AnythingLLM separates document-only Query, general Chat, and Agent modes. Clio should make evidence scope and missing evidence clear before adding another mode selector. |
| Research outputs | Existing summaries, FAQs, timelines, briefings, study guides, Markdown export | NotebookLM additionally offers media and other Studio outputs. They are not implemented here. |
| Evaluation and operations | Regression tests; usage, governance and audit code | Needs a repeatable retrieval benchmark, connector operations, load tests, restore drills, and deployment-level security review. |

### What to take from AnythingLLM

AnythingLLM is the closest reference for approachable self-hosted workspaces and
provider choice. Adopt its clear distinction between persistent workspace sources
and temporary conversation attachments, and make connection failures actionable.
Its MCP client also makes it a potential companion: use AnythingLLM's agent with
Clio's knowledge server. This branch supplies the corresponding config export.

Prioritize explicit source scope, evidence inspection, and dependable ingestion.
Avoid duplicating its broad agent flows and tool catalog in Clio's primary UX.
AnythingLLM's live-document sync is documented as a beta preview with different
Desktop and Docker capabilities; it is a useful direction, not evidence that
production-grade connector operations are trivial. Workspace model overrides are
another option to revisit when pilot users need different privacy or cost policies.

## Changes made

- Replaced six competing primary destinations with **Ask, Find, Connect**.
  Reports, saved instructions, administration, and notes remain accessible under
  More. Desktop and mobile use the same navigation component.
- Defaulted notes/tools to closed and removed source/notes panels from utility
  pages. Existing saved panel preferences remain respected.
- Made onboarding's search-only path useful and persistent. Clarified where
  sources are stored and when hosted providers receive text.
- Reordered MCP setup around the connection task; moved the tool inventory to a
  collapsed reference. Corrected the Claude project config filename to `.mcp.json`.
  Clarified that a URL's collection selection is a default, not an access limit.
- Added an AnythingLLM configuration export with its documented `streamable`
  transport, credential handling, merge instructions, and container URL guidance.
- Required operator authorization for global MCP configuration in private mode.
  Token write permission now requires an actual JSON boolean; `"false"` can no
  longer become a write-enabled token through Python truthiness.
- Honored explicit MCP credentials even on open deployments, where the previous
  conditional middleware registration ignored their read-only/revocation state.
  Tightened token routing to `/mcp` and its children, excluding lookalike paths.
  Open deployments still trust unauthenticated local callers; token handling
  does not turn an open deployment into an authenticated one.
- Added MCP tool annotations distinguishing read-only operations from the
  potentially destructive, non-idempotent document writer. Annotations are client
  hints; existing server-side write authorization remains authoritative.
- Reauthorized cached source reads, excluded quarantined documents, tracked content
  hashes and effective sensitivity, and rejected incomplete provenance records.
  Similarity alone no longer permits an answer to a different question to be reused.
  Removed cached question text from routine cache logs. Cache clearing now checks
  collection write access or operator privileges.
- Bound Docker Compose's published port to loopback by default. **Upgrade note:**
  existing LAN users must set `BIND_ADDRESS` explicitly and protect ingress.

## Follow-up (same day)

- **Caching reflects the entire request.** The cache key is now
  `<scope>#<request fingerprint>`, where the fingerprint covers provider,
  model and endpoint, search mode, `top_k`, rerank, the source selection,
  and — per collection in scope — a corpus version (document count, newest
  upload, chunk total, quarantined count) plus a digest of the collection
  guide and attached instruction packs. A new upload, a guide edit, an
  attached pack, a model switch or a different selection therefore misses
  the cache; the per-source fingerprint check is kept as a second guard.
  Clearing a collection's cache drops every fingerprint under it.
- **Selected sources are enforced end-to-end.** The sidebar checkboxes are
  the conversation's scope: the ids travel with every chat and report
  request, and the backend applies them to initial retrieval (document
  filter), the structured tables it inlines, the collection overview, and
  every tool the agent can call — search filters are intersected with the
  selection, `get_document_context` refuses other ids, table tools refuse
  tables from unselected sources, and `query_table` rejects SQL that names
  one. Citations follow from retrieval, so they stay inside the selection.
- **Online registration** at `/register` (approval or open), a
  **5 GiB per-collection storage cap**, an installable web manifest, and a
  typographic pass (self-hosted Inter, consistent radii and focus rings)
  shipped alongside; see `docs/DEPLOYMENT.md`.

## Remaining priorities

| Priority | Next outcome | Acceptance criteria |
|---|---|---|
| P0 | Measure trustworthy retrieval | A versioned corpus with answerable, unanswerable, exact-identifier, negation, date, table, and cross-user cases; report recall@k, citation correctness, abstention, and p50/p95 latency. No quality score is claimed without these measurements. |
| P0 | Finish privacy and authorization review | Exercise every API/tool/resource with owner, reader, outsider, revoked token, and anonymous identities. Validate cloud egress across embeddings, OCR, reranking, and chat; confirm collection labels match actual policy enforcement. |
| P1 | Ship one dependable connector | Choose from actual pilot demand (e.g. a watched folder or SharePoint). Incremental sync, deletion propagation, resumable jobs, visible failure state, and permission tests must ship together. |
| P1 | Make deployment reproducible | Lock supported dependencies, validate the full container and Postgres path, perform backup/restore and upgrade drills, document model prerequisites and hardware-tested limits. |
| P2 | Broaden identity and MCP authentication | Add standard OIDC and deliberately scoped/expiring service tokens if pilots require them. Existing token collection_scope only grants restricted collections; it is not an all-collection allowlist. |
| P2 | Add selected research conveniences | Evidence-linked saved answers and source comparison before media generation or a broad agent-builder interface. |

## Validation

- Initial baseline: 342 backend tests and 12 frontend tests. Three initial
  provider-construction failures were resolved by installing the test environment's
  SOCKS proxy dependency; application code was not changed to bypass the proxy.
- Updated suite: 358 backend tests and 17 frontend tests pass.
- Production frontend build passes. ESLint reports zero errors and five existing
  `v-html` warnings; these were not silently disabled.
- Browser visual verification was blocked by Chromium download failures in the
  review environment. Desktop/mobile navigation is covered by component tests;
  a human visual review remains required before release.
- AnythingLLM export is checked against its documented configuration schema and
  component-tested; a live AnythingLLM connection was not available to verify.
- Backend tests use temporary databases and mocked model/provider boundaries.
  They do not establish model quality, real SSO behavior, Postgres parity under
  load, or successful air-gap deployment.

## Official references

- [Onyx RAG and Search](https://docs.onyx.app/overview/core_features/internal_search)
- [Onyx connectors and access controls](https://docs.onyx.app/admins/connectors/overview)
- [Onyx MCP server](https://docs.onyx.app/deployment/configuration/mcp_server)
- [Onyx MCP client actions](https://docs.onyx.app/admins/actions/mcp)
- [NotebookLM notebook context and grounding](https://support.google.com/notebooklm/answer/17003757)
- [NotebookLM reports and learning features](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-student-features/)
- [Workspace NotebookLM data protections](https://workspaceupdates.googleblog.com/2025/02/notebooklm-and-notebooklm-plus-now-workspace-core-service.html)
- [AnythingLLM MCP configuration](https://docs.anythingllm.com/mcp-compatibility/overview)
- [AnythingLLM conversation attachments and workspace documents](https://docs.anythingllm.com/chatting-with-documents/introduction)
- [AnythingLLM chat modes](https://docs.anythingllm.com/features/chat-modes)
- [AnythingLLM workspace model configuration](https://docs.anythingllm.com/setup/llm-configuration/overview)
- [AnythingLLM live-document sync preview](https://docs.anythingllm.com/beta-preview/active-features/live-document-sync)
- [MCP Streamable HTTP requirements](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)
