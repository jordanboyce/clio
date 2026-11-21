# Roadmap

Forward-looking work for the general-purpose (`master`) Clio tool. Shipped items are not listed — check the git log. Financial-analysis roadmap items live on the `fintech` branch.

This version was rewritten on 2026-11-16 after a full pass over the product: the MCP surface, the Research tab, the web UI, and model selection. The plan comes first; the backlog that was already here is kept unchanged at the end.

On 2026-11-18 the hardening work order ([docs/prompts/HARDENING_PROMPT.md](prompts/HARDENING_PROMPT.md)) shipped R2, R3, R5, R6, R7, R8b, R11, P2, P4, P5, U2, U4 and U8, most of U5 and U10, the confirm part of U9 and the settings part of M10, each marked below. It also covered first-run model setup with hubs blocked, visible failures and an enterprise pass (audit, suspension, settings checks, upgrades, restarts, disk). The decisions that work left for the owner are listed under [Decisions waiting for the owner](#decisions-waiting-for-the-owner).

On 2026-11-19 the improvement work order ([docs/prompts/IMPROVEMENT_PROMPT.md](prompts/IMPROVEMENT_PROMPT.md); report in [docs/prompts/improvement-report/REPORT.md](prompts/improvement-report/REPORT.md)) shipped a retrieval benchmark that can tell configurations apart and an answer-level run (A1, A2), per-stage timings (A4), cross-collection merging on the reranker's score (B1), passages embedded with their source and heading under a per-collection indexing recipe (B2: recall@10 with the reranker 0.31 → 0.98 on the new benchmark), streaming answers with a working Stop (C1), Ask that survives tab switches and failures (C2, C3), Ask checking its own citations (C4), Find honouring the source selection (C7), a top-level error handler (C8), the collection in the URL (C9), MCP tool profiles with typed filters and writing instructions (D1, roadmap M2, M4, M6), and a release gated on tests and the smoke test (E1, E2). Later the same day: feedback capture with an admin export (A3), filtered keyword search inside the query (B5), upload progress, cancel and drag and drop (C6, U6), authorship and agent writes in the change feed (D2, roadmap B3 and part of B4), and tests that hold the settings and tool counts (E7). Rank fusion (B3) was measured and not adopted. New decisions are added under [Decisions waiting for the owner](#decisions-waiting-for-the-owner).

On 2026-11-20 the publishing work order ([docs/prompts/PUBLISH_PROMPT.md](prompts/PUBLISH_PROMPT.md); report in [docs/prompts/publish-report/REPORT.md](prompts/publish-report/REPORT.md)) extended the static-website export ([STATIC_SEARCH.md](STATIC_SEARCH.md)): one `include` option for what an export carries; a page to read for each source, which results fall back to, with a sitemap and canonical links once the site's address is set (and that address and the base address for files now travel in a bundle); the same pages as Markdown; components for related passages, answers written in advance (approved, checked, matched by a measured threshold), quotations, tables and transcripts; an A to Z index; `llms.txt`; a skill an agent can load without MCP; passages and vectors for other tools; a changes page and Atom feed; a site that works offline; a reference list with `citation_*` tags; one page to print and an EPUB; and, for a report, one HTML file that opens with no network, study cards and slides. The components are now tested in a real browser. All eleven acceptance steps pass. Stopped: a timeline (the report holds no events), an index in pieces (keyword search is the owner's call; the measurement shows the need past about 10,000 passages) and share cards (Pillow is not in the base install). The decisions are listed under [Decisions waiting for the owner](#decisions-waiting-for-the-owner).

## Guiding frame

Clio owns the trustworthy data layer; the intelligence layer lives upstream in whatever LLM the user trusts. Our job: make a user's own documents and tabular data faithfully agent-queryable, and never be silently wrong.

Two front doors, equally important:

- **People**, through Clio's own Ask / Find / Library UI.
- **Agents**, over MCP, using Clio as a shared second brain: several agents reading, writing and linking notes over time, alongside the documents people add.

Rules that apply to every phase below:

- Each phase leaves `master` deployable. The simple install stays the default.
- Everything must still work with hubs, catalogues and providers blocked.
- Changes to MCP tool names or shapes keep the old form working for one release, with a deprecation note in the response.

## How to read this plan

Phases are in the order I recommend doing them. Inside a phase, items are in order too. Each item has an id, a size (S = up to a day, M = a few days, L = a week or more) and a "done when" test.

Evidence comes from three sources, marked where it matters:

- **Seen live**: observed in the running app on 2026-11-16 (image built 2026-11-12).
- **Checked**: read in the code and confirmed a second time.
- **Read**: found by a single read of the code. Reproduce before fixing.

| Phase | Theme | Why it is here |
|---|---|---|
| 0 | Fix what is wrong today | Small defects that undercut trust; all S. F1-F17 shipped 2026-11-16, F18-F21 on 2026-11-18 |
| 1 | A clean MCP contract | The power is there; the surface is inconsistent and heavy |
| 2 | A Library you can depend on | The foundation (paper records, lookups, jobs) is the shaky part |
| 3 | Models that fit the job | Suggestions ignore what a model can do |
| 4 | Second-brain primitives | What agents still cannot do with notes |
| 5 | Core UX | Viewer, failures, settings, vocabulary |
| 6 | Code health | Done alongside, in the files each phase touches |

---

## Phase 0 — Fix what is wrong today

F1-F17 shipped on 2026-11-16 and F18-F21 on 2026-11-18, each with a regression test (see the git log), together with B1 from Phase 4. Every "Read" item reproduced before it was fixed. Nothing from that pass is still open.

Kept as decided: with no model connected on a touch screen, Enter adds a new line and the "Find passages" button sends, as phones usually do.

---

## Phase 1 — A clean MCP contract

**What is wrong.** The live catalogue lists 29 tools (30 with a deployment model), 7 resources and 4 prompts. An agent faces:

- six ways to retrieve (`search`, `search_collection`, `search_all_collections`, `research_documents`, `ask_documents`, `find_in_documents`);
- three ways to read (`fetch`, `get_document_context`, `read_document`);
- six ways to list documents;
- three tools that each say "call this first".

The docstrings and signatures total about 42,000 characters, an estimated 12-14k tokens in every session, with no way to load less. The same concept has different names from tool to tool, and the one stable handle (the filename) is accepted by only two tools.

### 1A. One contract (do in this order)

**M1 — One document handle everywhere. (M)**
`document_id` is a content hash and changes on every edit. Only `read_document` and `write_document` accept the stable filename. Add one resolver (the one inside `read_document`, [mcp_server.py:1897](../services/mcp_server.py)) and use it in `get_document_context`, `get_document_metadata`, `reindex_document`, `update_document_metadata` and `cite_sources`. The parameter is `document` and takes a name or an id.
*Done when:* an agent can write a note, then read, cite, re-index and label it, using only the filename.

**M2 — A typed `filters` object that rejects unknown keys. (S)**
Today `filters` is an untyped dict, and an unknown key is dropped with only a server log, so `{"filename": "x"}` silently searches everything ([mcp_server.py:926](../services/mcp_server.py)). Reuse the schema the in-app chat already has ([agent_tools.py:72](../services/agent_tools.py)). An unknown key is an error that names the valid keys.
*Done when:* a misspelled filter fails loudly, and the schema appears in `tools/list`.

*Shipped in part 2026-11-19 (D1):* an unknown key is an error that names the valid keys, and the filters gained `source_type` (`agent_note`, `upload`, `local_reference`, `url`) and `written_after` / `written_before`. The schema in `tools/list` is still the untyped dict.

**M3 — One response envelope. (M)**
Pick one name per concept and apply it to every tool:

| Concept | Today | Use |
|---|---|---|
| Collection | `name` / `collection_name` | `collection_id` + `collection_name` |
| Sizes | `chunks`, `num_chunks`, `total_chunks` (same for pages) | `chunks`, `pages` |
| Count returned | `total_results`, `total_matches`, `total_returned`, `returned`, `row_count` | `returned` and `total` |
| More available | `next_offset`, `truncated`, `documents_truncated`, nothing on search | `next_offset` (null when done) |
| Freshness | `corpus_version` on some tools | `corpus_version` on every response |
| Errors | free-text `ValueError` | `{code, message, hint}` with a short fixed list of codes |

Also: give `get_document_context` a continuation (today a long unpaged document cannot be read past 40k characters), make `mode` validation the same in `search_collection` and `search_all_collections`, and report failed collections in both fan-out tools.
*Done when:* one contract test walks every tool and asserts the envelope.

**M4 — Tool profiles. (M)**
Let a connection choose how much it loads: `?tools=core`, `library`, `notes` or `full`, also settable per token. Read-only tokens never list write tools. `search`/`fetch` (the ChatGPT shape) appear only in their own profile.

Suggested `core` (about 8 tools): `list_collections`, `search` (see M5), `read_document`, `get_document_context`, `find_in_documents`, `list_documents`, `query_table`, `write_document` when the token allows it.
*Done when:* `tools/list` for `core` is under 4k tokens, measured by a test that fails if it grows.

*Shipped 2026-11-19 (D1):* `?tools=notes` (8 tools, about 1,700 tokens) and `?tools=core` (12 tools, about 2,900), held by a test; no profile still lists all 32 tools (about 16,700 tokens). Read-only credentials never list a write tool. Not done: `library`, the per-token setting, and the `search`/`fetch` profile.

**M5 — Fewer, clearer retrieval tools. (M)**
- Fold `search_all_collections` into `search_collection`: `collection_id` accepts one id, a list, or `"all"`.
- Keep `deep_search` as the multi-query tool (R9 renamed it from `research_documents`; drop the old name one release after R9).
- Fold `list_recent_documents` and `get_collection_facets` into `list_documents` (`sort=recent`, `include=facets`).
- Default `get_collection_info` to counts; the 200-document array becomes opt-in.
- Stop attaching up to 150 rows of every small table, plus the collection summary, to every search ([mcp_server.py:1580](../services/mcp_server.py)). Return table names and let the agent ask.
- Cap the `collection://` and `collections://all` resources, and make them match the tools they claim to mirror.

*Done when:* one decision table in the server `instructions` covers every retrieval tool in under ten lines, and only `list_collections` says "start here".

**M6 — Instructions that cover writing. (S)**
The server `instructions` never mention `write_document`, `read_document` or `list_changes`. Move the "which tool when" guidance out of the individual docstrings and into `instructions`; cut each docstring to purpose, parameters and one example.

*Shipped in part 2026-11-19 (D1):* the instructions cover creating, appending and replacing notes, searching one's own notes and the change feed. The docstrings are not yet cut down.

**M7 — One tool registry. (M)**
The in-app chat has its own hand-written catalogue of 13 tools ([agent_tools.py](../services/agent_tools.py)) where `search_collection` is called `search_documents`, and [structured_chat.py:347](../services/structured_chat.py) re-implements dispatch. Generate both from one definition so the in-app chat and MCP cannot drift.

### 1B. The Connect tab

**Seen live:** one long page with a server toggle, token form, four URLs, three credential modes, two transports, a collection list, 44 clients, three output formats, Try it, advanced defaults, WebMCP and the catalogue. About 13 controls come before the first snippet.

**M8 — A three-step default path. (M)**
1. Pick your client (show the six most common; "more" opens the rest).
2. Choose what it may do: read only, or read and write notes. Choose all collections or some.
3. One button creates the token and shows the finished command or file, with the token in it.

Everything else (transport, server name, env-var form, entry-only output, legacy SSE) moves under "Advanced".

Fix along the way:
- Do not tick the first collection by default. It pins the snippet to `?collection_id=…` and names the server `clio-<id>`, while the text says "tick none and the agent picks".
- "Collections" appears twice with different meanings (what the token may see; where searches start). Keep the first, move the second to Advanced as "Start in".
- A token is shown once. After a reload the snippets hold a placeholder, with nothing saying why. Say so, and offer "make a new token".
- The footer says write tools only work for tokens that allow it. That is untrue for password and open callers; fix the text or the behaviour (see M9).
- Tools are grouped by regex, which puts `read_document`, `list_documents` and `cite_sources` under "Write" ([MCPTab.vue:888](../frontend/src/components/MCPTab.vue)). Group from the tool's own annotations.
- The UI assumes `mcp_top_k` 5; the server default is 8.

*Done when:* a new user gets from opening the tab to a first tool call in the Try it log in three clicks and one paste.

**M9 — Simplify who may write. (S, needs a decision)**
Today an open deployment and the shared password both get full write access, while a personal token is read-only by default. Decide whether anonymous and password callers should be read-only unless `MCP_ALLOW_WRITE` says otherwise. Recommended: yes, read-only by default, to match tokens.

**M10 — Remove dead and duplicate settings. (S)**
`MCP_AI_PROVIDER` appears to have no reader. `ask_documents` is registered at import, so changing `AI_PROVIDER` needs a restart; register it always and answer "no model configured" when there is none.

*`MCP_AI_PROVIDER` (and the dead `SCHEMA_VERSION`) removed 2026-11-18.* The `ask_documents` registration is still open.

---

## Phase 2 — A Library you can depend on

**What is wrong.** The parts that check things are solid: citation formatting, quote verification and the draft checker are deterministic and well tested. The shaky parts are underneath and around them.

1. **Paper records are often wrong, and say so with confidence.** Seen live on ten well-known arXiv papers whose filenames are their arXiv ids:
   - four have a wrong or garbled title or author list (a title that is a fragment of the abstract; "ricknowledgetheyencapsulate. Retrieval-AugmentedGeneration(RAG),anad"; authors "LuyuGao XueguangMa JimmyLin JamieCallan" as one name; one paper credited to "Alan Turing");
   - three have the wrong year;
   - seven have no arXiv id recorded.

   The citation map, key papers, reference list and draft checker all build on these records.
2. **Outside lookups fail silently** (F8), and a blocked site waits out a 15-second timeout per request with no "catalogues unreachable" state.
3. **Jobs live in memory.** A restart loses running and scheduled work. Stop takes effect only between documents.
4. **The flow has no visible order and no end product.** Nothing produces a cited review across sources.
5. **"Research" named four different things.** Fixed by R9.

### 2A. Records you can trust

**R1 — A metadata accuracy benchmark. (M) Do this first.**
Same idea as the retrieval benchmark: 40-60 real papers and books with known title, authors, year and identifiers. Include arXiv preprints, two-column conference papers, a scanned PDF, a book and a report with no identifiers. Report per-field accuracy with catalogues on and off.
*Done when:* `scripts/research_bench.py` prints the numbers, and today's baseline is recorded here.

*Shipped 2026-11-16.* Manifest v1 has 59 documents: 32 arXiv preprints, 8 two-column ACL papers, 6 open-access journal articles, 6 NIST/NASA reports (one with no identifiers), 5 books, a scanned 1953 NACA report and a generated image-only scan. The files are downloaded on request (`--download`, about 80 MB) and never committed; eight small invented PDFs are committed for an offline smoke run (`CLIO_RESEARCH_BENCH=1`). Rules and how to run it: [tests/fixtures/research_bench/README.md](../tests/fixtures/research_bench/README.md). Baseline (numbers compare only within one manifest version):

| Field | Offline | Catalogues on |
|---|---|---|
| Title, exact after normalising | 64% (38/59) | 90% (53/59) |
| Authors, mean F1 over family names | 0.36 | 0.80 |
| Authors, whole list right | 17% (10/59) | 75% (44/59) |
| Year | 81% (48/59) | 81% (48/59) |
| arXiv id / DOI / ISBN found | 32/32 · 10/22 · 2/2 | 32/32 · 15/22 · 2/2 |
| Identifiers invented | 0 | 0 |

*Re-measured 2026-11-18, after R2 and R3 (offline; catalogues not re-run):*

| Field | Offline, 2026-11-16 | Offline, 2026-11-18 |
|---|---|---|
| Title, exact after normalising | 64% (38/59) | 85% (50/59) |
| Authors, mean F1 over family names | 0.36 | 0.52 |
| Authors, whole list right | 17% (10/59) | 39% (23/59) |
| Year | 81% (48/59) | 86% (51/59) |
| Confidently wrong (a wrong title, author list or year not marked "needs checking") | not measured | 0 |
| Identifiers invented | 0 | 0 (one DOI found is wrong) |

The new row is the one R3 is judged by: every remaining miss is shown as "needs checking", not as fact (13 titles, 33 author lists and 2 years are marked). The benchmark prints it; it does not fail a run on it.

The 2026-11-16 offline misses, most common first: no authors read (15; HyDE); some authors missing (13; RAG keeps 1 of 12); a DOI known but not printed (11, the ACL PDFs); the wrong people (9: BERT's second title line, "Microsoft Research", a Commerce Secretary); part of the title only (8). These are R2's targets. Two notes for R2: OpenAlex answered 12 of 26 requests with 429 when no contact email is sent; and the benchmark found every arXiv id and year, while the live check above found 7 of 10 missing. Find out what differed in that setup before relying on either.

**R2 — Better extraction without the network. (M)**
In order of expected gain:
- **Read identifiers from the filename** (`2401.18059.pdf`, a DOI-shaped name) and from PDF metadata, not only from text printed on page 1.
- **Pick the title by layout**: the largest text block near the top of page 1. PDFium already gives font sizes.
- **Refuse implausible values.** A title that starts lower-case or mid-sentence, has no spaces across 25 or more characters, or matches a venue line ("In Proceedings of…") is rejected. A single "author" containing several capitalised runs is split or rejected.
- **Repair lost spaces** in run-together text before using it.
- **Year**: prefer the identifier, then the catalogue, then page 1. Never a year taken from the reference list.

*Done when:* the benchmark's offline title and author accuracy clears a target you set after seeing R1's baseline.

*Shipped 2026-11-18* (no target was set; the numbers are under R1). Identifiers come from the filename and the PDF's metadata; the title is the largest upright text near the top of page 1 (rotated stamps are skipped, small caps recovered); venue lines, notices, affiliations and organisations are refused as titles or authors; run-together names are split and lost spaces repaired; the year prefers the identifier and a dated line on page 1.

**R3 — "Not sure" beats wrong. (S)**
Each field carries a confidence. Low-confidence fields show as "needs checking" with the filename as the display title. A record edited by hand (`PATCH /api/research/papers/{id}/record` exists) is never overwritten by a later analysis. The Library gets a "needs checking" filter.

*Shipped 2026-11-18.* Each field is marked high or low with the reason; a doubtful record shows its filename, a *needs checking* badge and why, and the filter exists. Bulk rename lists only the records that are not in doubt and offers *Check these first* for the rest, with *Undo all*.

**R4 — Optional model-assisted reading, verified. (M)**
Where a model is configured, ask it for title, authors and year from page 1, and accept an answer only when every value appears in the page text. This fits the existing grounding rule and works at sites where catalogues are blocked but a local model is not.

**R5 — Analyze on ingest. (S)**
Analysis needs no model, yet it runs only when someone presses "Analyze" (`analyze_document` has no caller outside the API). Run it when a PDF is indexed, and after a reference is downloaded.

*Shipped 2026-11-18* (`LIBRARY_AUTO_ANALYZE`, on by default).

### 2B. Lookups that fail honestly

**R6 — Reachability and backoff. (M)**
- Tell "unreachable" apart from "no record", in the code and in what the user reads.
- After a few timeouts, stop trying for a while and show one "catalogues unreachable" note with a "try again" button.
- Honour 429 and `Retry-After`.
- Add tests for timeout, 429 and blocked hosts.

*Shipped 2026-11-18.* A host that times out rests for five minutes (or its `Retry-After`); the Library shows one *catalogues unreachable* note with *Try again*; connects give up after 5 seconds.

**R7 — Resolve and download as a background job. (M)**
"Find open copies" is one synchronous request over every reference, up to two calls each at 15 seconds, against a 180-second client timeout. Make it a job with progress and Stop.

*Shipped 2026-11-18* (job kind `resolve`, shown in R8b's strip).

### 2C. Jobs that survive

**R8 — Durable Library jobs. (L)**
- Store the queue, schedule and per-document progress in the database.
- Resume or re-queue at startup; mark interrupted work as interrupted at boot, not when someone next looks.
- Check for Stop between model calls, not between documents, and show "Stopping…".
- Add a per-document timeout.
- Show scheduled times in local time.

Design decision needed: the provider key is deliberately held in memory only, so a job that resumes after a restart has no key. The options are under [Decisions waiting for the owner](#decisions-waiting-for-the-owner). Not started until that is decided. Index jobs already survive a restart (2026-11-18); reports left generating are marked failed at start, and re-queueing them waits on the same decision.

**R8b — One job strip for the whole tab. (M)**
Progress, errors and Stop currently live only under Reviews. Analyze and Screen show a bare spinner in the Library; the paper panel's "Being prepared…" never updates. Put one strip at the top of the tab with n/m and Stop, restored when you come back.

*Shipped 2026-11-18.*

### 2D. A flow with a beginning and an end

**R9 — Untangle the names. (S, do early; it is cheap)**
*Decided 2026-11-16: rename the Research tab to Library.* Do it once the Phase 0 branches are merged, because it touches the same files.

| Today | Use |
|---|---|
| "Research" tab | **Library** (papers, map, reviews, cite & check) |
| Ask's Quick / **Research** depth; MCP `research_documents`, `depth="research"` | **Deep search** / `deep_search` (keep the old tool name as an alias for one release) |
| Features toggle "Research" (which only controls catalogue lookups) | **Catalogue lookups** |
| `RESEARCH_MAX_CONCURRENT` vs `RESEARCH_MAX_CONCURRENT_JOBS` | `DEEP_SEARCH_MAX_CONCURRENT`, `LIBRARY_MAX_CONCURRENT_JOBS` (old names still read) |
| briefs (API) / Reviews (UI); assess / Screen / "Worth my time?" / verdict | **Reviews** and **Screening**, in the API as well |
| "Citation map" (tab) / "Citation graph" (ribbon, palette) | **Citation map** |

*Shipped 2026-11-16.* Every old name still works for one release, each with a test:
- **MCP:** `deep_search` is the tool; `research_documents` stays registered and adds a `deprecation` note to its response. The in-app agent dispatches the old name as an alias. Server `instructions`, prompts and the skill name the new tool and say the old one still works.
- **Depth:** `deep`; `research` is accepted by `/api/chat`, `ask_documents` and `/api/v1` (whose response echoes the spelling sent; `ask_documents` adds a `deprecation` note). The answer cache keys deep search as `research` whichever spelling arrives, so the two share entries and answers cached before the rename still hit; quick never shares with deep.
- **Settings:** `DEEP_SEARCH_MAX_CONCURRENT`, `DEEP_SEARCH_QUEUE_TIMEOUT_SECONDS`, `LIBRARY_MAX_CONCURRENT_JOBS`, `LIBRARY_MAX_JOB_DOCUMENTS`; the `RESEARCH_*` spellings are still read, and startup logs a line naming each one set only under its old name.
- **REST:** the prefix stays `/api/research` (the app's own API, with the tables, the job rows and every client already on it; moving it would double every route for no reader's benefit). Added `/papers/{id}/screen`, `/screening`, `/reviews…`, job kinds `screen` and `review`, and `review_count`, `unread_reviews`, `reviews`, `screenings` on papers; the old paths, kinds and fields are served and marked deprecated in `/docs`. Job rows still report the stored kinds `assess` and `brief`. Database names are unchanged.
- **Frontend:** the internal tab id stays `research`, so `?tab=research`, saved ribbon layouts and the last open tab keep working; `?tab=library` and `G` `L` are new. The Library's first view is now *Papers*. A saved `chat_depth` or answer of `research` reads as Deep search.
- Left as they were: `RESEARCH_LOOKUP_ENABLED` and `RESEARCH_CONTACT_EMAIL` (runtime settings saved by the Features page; the switch is labelled *Catalogue lookups*), the MCP prompt `research_question` (an id clients may have saved), the feature id `research` in `/api/system/features`, component file names, `services/research/`, and the review depth names *brief* and *thorough*.

**R10 — Show the order. (M)**
The first visit should read as four steps with their state: *Analyzed 10/10 → Screened for "…" 6/10 → Reviewed 3/10 → Cite & check*. Each step says what it needs ("needs a model" where true, shown before you type) and what it will cost (number of model calls). Screening skips sources already screened for the same question.

*Shipped 2026-11-18 (frontend only).* The Papers view opens on the four steps with counts over the selection, what each needs and costs; Screen skips sources already screened for the question and says so (*Screen 4 more sources*); Prepare reviews prices a run (*up to 16 model calls*). Reviews became a list (unread first, verdict and verified count per row) beside a reader. The cost is counted, not metered: R8b's job strip is still to do.

**R11 — The missing end product: a cited synthesis. (L)**
Add "Write up" on a selection: a literature summary built only from statements the reviews have already verified, each carrying its quote and page, exported with the reference list. It lands in Reports. This is the natural finish for the tab, and it reuses checks that already exist.

*Shipped 2026-11-18.* *Write up* in Reviews builds the summary from verified statements only, each with its quote and page, plus the reference list, and opens it in Reports. It needs no model.

**R12 — Plain-language map. (S)**
Replace "betweenness", "constraint" and "modularity" with what they mean ("bridges two groups", "cited by many of your sources"). Keep the honest "too small to rank" warning; it is good.

*Shipped 2026-11-18, with F19.* The server's reliability note and definitions are plain words ("Too small to rank: 16 works and 32 links…", "the groups are also loose"); the API keys are unchanged. A selected dot says *why it matters here*; broker measures read as sentences; dot sizes are *Links*, *Influence*, *Bridging*; colour *Group*. The map loads through `lazyView`.

**R13 — Library over MCP. (M)**
Agents can format and check citations but cannot list papers, read reviews or verdicts, or start screening. Add `list_papers`, `get_paper` (record, review, screening verdicts) and `screen_papers` in the `library` tool profile (M4).

---

## Phase 3 — Models that fit the job

**What is wrong (Checked).**

- `/api/ai/models` returns bare ids ([api/chat.py:1589](../api/chat.py)). The picker cannot tell a chat model from an embedding, speech or image model, so "All models" lists everything a provider has, alphabetically.
- "Recommended" is a hard-coded list kept in two places ([aiProviders.js:12](../frontend/src/utils/aiProviders.js); `FAST_MODEL` / `QUALITY_MODEL` in [ai_service.py](../services/ai_service.py)), and it is out of date: `gpt-4o`, `grok-3`, `gemini-2.5-pro-preview-03-25`, Claude 3.5 on OpenRouter and Bedrock, `llama3.2` for Ollama.
- Recommendations are listed first whether or not the user's key offers them ([aiProviders.js:465](../frontend/src/utils/aiProviders.js)).
- Ollama abilities are guessed from names: embedding from `embed`/`minilm`/`bge`/`e5` ([api/system.py:722](../api/system.py)), vision from `clip`/`mllama` families plus name patterns ([api/system.py:503](../api/system.py)).
- The embedding catalogue knows dimensions and language only. A custom endpoint gets no suggestions.

**P1 — One capability record per model, built on the server. (M)**
`/api/ai/models` returns objects, not strings:

```json
{"id": "…", "kinds": ["chat"], "vision": true, "tools": true,
 "context_tokens": 200000, "embedding_dimensions": null,
 "max_input_tokens": null, "source": "provider"}
```

Fill it from the best source available, in this order:

1. **What the provider reports.** Ollama's `/api/show` returns a `capabilities` list (completion, vision, tools, embedding) and context length. OpenRouter's model list includes input modalities and supported parameters. Check each provider's current model API for what else it exposes before relying on it.
2. **A small maintained table** of name patterns per provider for what the API does not say (for example, which OpenAI ids are embedding or speech models). Keep it in one file on the server, with a test that every `default_model` in it is well-formed.
3. **Unknown.** Say "not known" and let the user try. Never guess silently; mark `source: "pattern"` or `"unknown"`.

*Done when:* the frontend holds no model names at all.

*Shipped 2026-11-16.* `/api/ai/models` adds `records` (one per model, with `source` and `pattern_fields` saying what was guessed), `defaults` and `suggested`, and keeps the bare `models` list for one release. Names live only in [services/model_catalog.py](../services/model_catalog.py); recommendations include only models the key lists. Checked live on Ollama only; the other providers against mocks. The OpenAI ids and the Grok fast and vision defaults in the table are unconfirmed.

**P2 — Each picker filters by the job. (M)**

| Picker | Shows | Notes |
|---|---|---|
| Answers (Ask, Find, Reports) | chat models | badge for tools and vision |
| Deep search / agent mode | chat models with tool calling | others listed as "cannot use tools" |
| Scanned pages and figures | vision models only | replaces the Ollama-only vision list |
| Search index | embedding models only | dimensions, language, max input, local or hosted |
| Library reviews | chat models | warn when the context is too small for "thorough" |

*Shipped* in `a4e88a3`, checked against the code on 2026-11-18.

**P3 — Recommendations from what you actually have. (M)**
Drop the hard-coded names. Recommend from the live list using the capability record: for answers, the newest model in the provider's main family plus a faster one; for Ollama, prefer installed models that have tools and the largest context that fits. If nothing suitable is installed, say what to pull. Replace `DEFAULT_OLLAMA_MODEL = "llama3.2"` with "first suitable installed model".

**P4 — Embedding guidance that prevents mistakes. (M)**
- Show for each embedding model: dimensions, max input tokens, languages, and whether it needs query/passage prefixes. Apply prefixes automatically for models that need them (check which of the listed models do).
- Before a switch, say plainly what it costs: "changing this model means re-indexing N sources (about M minutes)".
- Warn when the chunk size exceeds the model's input limit.
- For a custom endpoint, list its models through `/models` and probe one embedding to read the dimension.

*Shipped* in `a4e88a3`, checked on 2026-11-18, including the chunk-size warning. First-run setup now offers the site's own embedding server as a choice of its own.

**P5 — Test the choice, not only the key. (S)**
"Test connection" should run the job the model was picked for: one short completion; one tool call for deep search; one small image for vision; one embedding for the index. Report which abilities passed.

*Shipped 2026-11-18.* *Check what the model can do* runs answers, tool calls (native, or the written protocol for providers without native tools) and a small image, and reports each.

---

## Phase 4 — Second-brain primitives

What exists today: `write_document` (create, replace, append) with a per-collection lock, staged atomic swap and `expected_document_id`; `read_document` for exact text; 114 write tests. What an agent still cannot do is below. All of it is **Read** from one pass over [mcp_server.py](../services/mcp_server.py).

**B1 — Tell notes from human sources. (S) Shipped 2026-11-16.**
Agent notes carry `source_type='agent_note'`; `replace` on a person's file needs a token minted with `can_overwrite_uploads`; `create_document` and `append_to_document` are non-destructive. See [ARCHITECTURE.md](ARCHITECTURE.md#mcp). F18 made the type survive a re-index and a bundle import.

**B2 — History. (M)**
Keep previous versions of a note (a bounded number, or by age). Add `list_versions` and `read_document(version=…)`. This also makes every other write feature safer to ship.

**B3 — Authorship. (S)**
Record the token (or identity) on each write. Return `written_by` and `written_at` from the read tools and in the audit row.

*Shipped 2026-11-19 (work order D2):* the token's name is recorded on each MCP write; search results, `read_document` and `get_document_metadata` return `written_at` and `written_by`; the upload audit row carries both and the token id; `deep_search` now writes its tool-call audit row.

**B4 — A change feed for notes. (S)**
`list_changes` carries folder and git sync events only. Add agent and human writes, so one agent can ask "what changed since I last looked".

*Shipped in part 2026-11-19 (D2):* agent writes are in the feed, with who wrote them. Human uploads are not yet.

**B5 — Edit part of a note. (M)**
`edit_document` with exact string replace and "replace under this heading". Today every append re-reads and re-embeds the whole file; re-embed only the chunks that changed.

**B6 — Real folders. (M)**
Path separators are flattened to `_`. Keep the path as first-class metadata (this is also the open item for git sources), and let `list_documents` filter by folder.

**B7 — Titles, tags and frontmatter. (M)**
Read YAML frontmatter from Markdown notes into metadata. Make `tags` filterable in search and settable through `update_document_metadata`, which today sets sensitivity only.

**B8 — Links and backlinks. (M)**
Parse `[[name]]` and Markdown links between sources. Add `get_links(document)` returning outgoing links, backlinks and broken links. Show backlinks on a source in the UI.

**B9 — Soft delete, rename, move. (M)**
All are missing by design today. With history (B2) in place, add them behind the write permission, with a recoverable trash.

**B10 — Notes in the UI. (M)**
The "Notes and tools" panel is a browser-local text box that is never indexed. Replace it with real notes: "Save to notes" on an answer or passage writes a Markdown note into the collection (with its citations), where both people and agents can find it.

*Phase done when:* two agents and one person can keep a shared set of linked notes for a week with no lost edits, and each can see who changed what.

---

## Phase 5 — Core UX

**U1 — An in-app source viewer with the cited passage highlighted. (L)**
The largest gap for a product whose promise is "check the citations". Today a citation expands the passage in place, and "Open original" opens a new browser tab with no highlight. Open the source beside the answer, scrolled to the passage, from Ask chips, Find results and Library quotes. This is the existing "side-by-side source attribution" item.

**U2 — Failures you can see and retry. (M)**
A failed file appears only inside the job card, and "Dismiss" discards the only record. Show a persistent "N failed" row in Sources with the reason and Retry; add "Re-index" per source.

*Shipped 2026-11-18, except Re-index per source.* Every upload path returns `failed` entries with a reason and a fix; the row survives a reload and Dismiss on the job card. Retry works for a browser upload (the server keeps the failed copy until the stale-upload sweep) and for a file picked from this computer. A blocked file offers no Retry.

**U3 — One "Models" page. (M) Pairs with Phase 3.**
The answer model is set in seven places, and the embedding model, OCR and reranking in three or four each. Make one page with four rows (answers, search index, scanned pages and figures, result reordering), each showing what is in use, whether it is set for this browser or the whole server, and what it can do. Split the word "rerank": the local cross-encoder is "Result reordering"; the LLM pass in Find is "AI reorder".

**U4 — One vocabulary. (S)**
- "Sources" everywhere (not docs, documents, files).
- "Reports" as the nav label and the page heading (the page says "Generate").
- "Saved instructions" (not Instructions, pack, Expertise).
- "Connect" (not MCP page, Settings → MCP, MCP Server).
- Two controls are both called "Answer depth"; rename the drawer one "Context size".

*Shipped 2026-11-18.* The glossary is in [ENGINEERING.md](ENGINEERING.md), and a test fails on a retired word in any template.

**U5 — A quieter default shell. (S)**
Seen live: about nine unlabeled ribbon icons, an eight-icon rail above Sources, five header controls and a status bar; the review count shows in three places. Default the ribbon to Ask, Find, Library, Connect and "More", with labels. Reduce the status bar to jobs and problems. One "add" entry point in Sources, not three.

*Shipped 2026-11-18, except the Sources panel.* The ribbon defaults to labelled Ask, Find, Library, Connect and More (saved layouts are kept); the footer counts only problems ("All working"). Still open: one "add" entry point in Sources, and the eight-icon rail above it.

**U6 — Drag and drop. (S)**
There is none. Accept drops on the Sources panel and the chat.

*Shipped 2026-11-19 (work order C6),* with transfer progress in bytes and Cancel.

**U7 — Find across collections, with filters. (M)**
Ask has an "All" scope; Find does not. Add the scope switch, filters for type, source and date, grouping by source, and "load more".

**U8 — Real URLs. (M)**
There is no router; only `?tab=` is read, then stripped. Add hash routes for view, collection, chat and source so Back, refresh and sharing a link work. This also fixes F11 properly.

*Shipped 2026-11-18* (`#/library`, `#/library/map`, `#/find` and the like; `?tab=` still works and survives onboarding). Corrected 2026-11-19: the chat was never in the URL; the collection is now (`#/ask?c=<collection>`, work order C9). The chat stays out until history is on the server (U9).

**U9 — Chat history on the server. (M)**
History lives in the browser (50 sessions of 100 messages), in a 288px dropdown. Store it server-side per user, with a proper list, search and delete-with-undo. Add edit-and-resend. Replace the eight native `confirm()` calls with one confirm component.

*The confirm component shipped 2026-11-18* (every native confirm replaced, and MCP token revoke now asks). Server-side history is still open.

**U10 — Settings you can find. (M)**
About 105 of what were 162 settings are environment-only (193 settings on 2026-11-19). Add a read-only "Set by environment" list in Settings so an operator can see what is in force without reading `.env.example`. Turn Admin's single long page into sections.

*The "Set by environment" list shipped 2026-11-18* (admins only; a saved value that overrides the environment is logged). Admin sections are still open.

**U11 — Typography and consistency pass. (S)**
124 uses of 10-11px text against a "reading-first" brief; three different page-heading styles; `btn-xs` in Ask beside 44px controls in Find. Set a 12px floor, one heading style and one control height per context.

---

## Phase 6 — Code health (alongside, not a separate project)

Do each split when a phase first touches the file.

- **`services/mcp_server.py` (4,898 lines on 2026-11-16; 5,541 on 2026-11-19)** → a package: context and auth, retrieval, documents, tables, writes, resources and prompts, ASGI. Public functions for `/api/v1` and chat, which today import private ones ([api/v1.py:405](../api/v1.py); `ask_documents` imports `api.chat` privates). Do with M1-M3.
- **One registration style** for tools (there are three), and one helper for the excerpt clamp (copied six times) and the document-list serializer (copied four times). Do with M3.
- **`SourcesSidebar.vue` (2,871), `App.vue` (2,543), `SettingsTab.vue` (2,313)** → feature components with stores. Collections, the jobs drawer and three modals move out of `App.vue`. Do with U2, U3, U5.
- **One modal pattern** (`useModal`). Do with F14.
- **Tests where there are none**: Library components, onboarding, command palette, ModelPicker; MCP resources; lookups under failure; one browser end-to-end test of add source → ask → open citation.
- **Library API N+1**: `get_paper` loads every reference twice and the document list three times per open ([api/research.py:289](../api/research.py)).

---

## Suggested order of work

1. **Week 1:** all of Phase 0, plus R9 (names) and B1 (protect human sources), since both are cheap and make later work clearer. Done on 2026-11-16, with R1, P1 and R9, except F18-F21, which followed on 2026-11-18.
2. **Then Phase 1A** (M1 → M2 → M3 → M4 → M5), splitting `mcp_server.py` as you go, then **1B** (the Connect tab).
3. **Then Phase 2**: R1 (benchmark) → R2/R3/R5 (records) → R6/R7 (lookups) → R8/R8b (jobs) → R10 → R11.
4. **Then Phase 3**, with U3 (Models page) in the same stretch.
5. **Then Phase 4** from B2 onward, and **Phase 5** by value: U1, U2, U4/U5/U6, then the rest.

Two decisions are yours before the work reaches them: M9 (should open and password callers be read-only by default) and R8 (how a Library job resumes without a stored key). Three more came out of the 2026-11-18 enterprise pass; all five are below. R9 shipped: the Research tab is now the Library.

---

## Decisions waiting for the owner

Written up by the 2026-11-18 hardening pass; none was made.

1. **M9: who may write over MCP.** Today open and password callers get full write; personal tokens are read-only by default.
   (a) Read-only by default, with `MCP_ALLOW_WRITE` to opt in (recommended in M9). (b) Keep full write and fix the Connect footer, which says otherwise. (c) Read-only only when the server is reachable from the network, the same split model endpoints now use.
2. **R8: how a Library job, or a queued report, resumes without a stored key.**
   (a) Resume only with a server-side key or the deployment's own model. (b) Pause as "needs a model to continue" until someone opens the tab and their browser supplies the key. (c) Keep the key encrypted with the existing secret box for the job's lifetime only, which breaks the "never written down" promise.
3. **Password-only deployments have one shared credential**, so there is no admin separation and no attribution beyond the source address and sign-in method (now recorded).
   (a) Require an identity provider for anything called enterprise, and fail the readiness report without one. (b) A separate admin secret for Admin and deployment settings. (c) Keep it, and say so prominently in the docs and the readiness report.
4. **No browser sign-in of Clio's own.**
   (a) Make the authenticating-proxy compose file the documented enterprise default (`SIGN_OUT_URL` fits it). (b) A small session layer: OIDC code flow, cookie, idle timeout and logout, at the cost of CSRF handling and session storage.
5. **Admins by group.** Today `ADMIN_EMAILS` is a static list read at start.
   (a) Add `ADMIN_GROUPS`, matched against an OIDC or proxy group claim. (b) Re-read `ADMIN_EMAILS` live and make it editable in the app. (c) Keep it static and document the restart.

Added by the 2026-11-19 improvement pass; none was made.

6. **Re-indexing existing collections for indexing recipe 2 (B2).** New collections embed each passage under its source line; existing ones stay on recipe 1 (on the benchmark, recall@10 0.32 instead of 0.98 with the reranker).
   (a) New collections only. (b) Offer *Re-index in place* on collections still on recipe 1, with its cost. (c) Re-index in the background after an upgrade.
   *Decided 2026-11-19: (b).* The notice above the Sources list offers it to whoever may re-index, with Not now remembered; a collection re-index rebuilds from empty and so takes the current recipe.
7. **The default search model.** MiniLM or BGE small for new installs; not yet run on the new benchmark.
   (a) Keep MiniLM. (b) BGE small for new installs only. (c) BGE small as the `:latest` image's model with the documented migration.
   *Decided 2026-11-19: (a), on the numbers.* On `retrieval_hard` with recipe 2 (BGE's query prefix applied), MiniLM beat BGE small: recall@10 0.829 against 0.600 without the reranker, 0.981 against 0.938 with it; revision safety 90% against 85%. The catalogue no longer calls BGE small "stronger"; it stays available, and the `:bge` images stay for sites that chose it.
8. **Feedback capture (work order A3): is the question text stored with a verdict**, at sites with classification markings?
   (a) Verdict and source ids only. (b) Text stored unless the collection is marked local-only. (c) Text always.
   *Decided 2026-11-19: (b).* The question is kept unless the answer could have used local-only data (across all collections, any of them); the answer text is never kept.
9. **Unknown MCP filter keys now fail (D1).** The always-on rule says a changed MCP shape keeps its old form for one release.
   (a) Keep the error. (b) Accept unknown keys with a warning field for one release, then fail.
   *Decided 2026-11-19: (b).* An unknown key is ignored and the response carries `warnings` naming it and the valid keys; make it an error in the release after next.

---

### From the 2026-11-20 publishing pass

Details and options in [docs/prompts/publish-report/REPORT.md](prompts/publish-report/REPORT.md); none was made.

1. **Answers written in advance: approval and label.** Approval is required today (`services/static_export/answers.py`); keep it, or approve automatically when the citation check passes. The label is a placeholder.
2. **Where an answer to publish is written.** Today over the API only: a "Publish this answer" action in Ask, a form in the Export dialog, or from an FAQ report's pairs.
3. **Reader page or the file's own address first** when a source has both (today the reader is the fallback).
4. **Additions in the data-only export**, or for a whole site only.
5. **Large tables**: left out above 5,000 rows (today), or read in the browser from SQLite compiled to WebAssembly.
6. **Keyword search in an index in pieces** (E2): a separate word index, keyword search off, or the text always downloaded whole.
7. **Parquet** for other tools needs pyarrow.
8. **A speech engine** for the podcast script, if any (Piper, Kokoro, espeak-ng, XTTS: licences and sizes in the report).
9. **Share cards** need Pillow and a font in the base install, or only where Pillow is installed, or none.
10. **An edge bundle** (search or read-only MCP as a function on someone else's platform): after E2, and measured against it.
11. **Storing timeline events and the study guide's answers** as data, so both can be published.

## Standing backlog (carried over unchanged)

Items below predate this plan. Where one overlaps a numbered item above, the numbered item is the current plan.

### Library — ingestion & context

- **Git repository indexing** — local trees shipped 2026-10-08: sixty-plus languages chunked by symbol with lenient parsing (syntax errors, unbalanced braces, odd encodings and minified files still index), name-based detection for Makefile/Dockerfile-style files, binary/lockfile/build-output skipping, and symbol name + line range kept on every chunk. Shipped 2026-10-16: a checkout is listed by `git ls-files` (`.gitignore` honoured, HEAD tracked), remote repositories are shallow-cloned and followed (`POST /documents/sync-git`, `GIT_ALLOWED_HOSTS`, refused in offline mode unless allowlisted). Remaining: the relative path as first-class metadata (today it survives only in the flattened filename), and indexing commit messages as searchable history.
- **Watch-folder sync and change alerts** — incremental folder sync shipped 2026-10-08: re-syncing a folder hashes every file, skips the unchanged ones, replaces changed ones, and optionally prunes documents whose file is gone (`POST /documents/sync-folder`, remembered per collection with *Sync*/*Prune* in the Sources panel). Shipped 2026-10-16: *Watch* on a source re-syncs it on an interval (remote git repositories are checked with `git ls-remote` first), and every real change lands in a per-collection Updates feed (`GET /documents/changes`, the header bell, MCP `list_changes`) with the commits since the last sync, plus an optional signed `ALERT_WEBHOOK_URL` POST. Shipped 2026-10-29: every listed source says whether it is out of date and why (`out_of_date`/`stale_reason` on `GET /documents/sync-folders`: HEAD moved or files edited in a checkout, judged from one `git status` without hashing; the remote ahead of a clone, from a `git ls-remote` probe the loop runs for unwatched repositories too; an indexing run that failed), *Sync* on a remote repository fetches before indexing and re-creates a lost clone, a sync whose job failed is repeated by the watcher even when the tip has not moved, and a source whose previous job is still indexing is never queued twice. Remaining: filesystem-event watching (today it polls), per-source include/exclude editing in the UI, and email/SMTP delivery for on-prem.
- **Richer spreadsheet handling** — multi-sheet workbooks and more complex table structures beyond flat CSVs.

### Notebook — synthesis

- **Multi-document synthesis** — answers that explicitly cross-reference several sources rather than retrieving isolated chunks.
- **Side-by-side source attribution** — clicking a citation scrolls the original document to the exact highlighted passage.
- **Thematic collections** — group documents into notebooks with their own shared Expertise (research frameworks).

### Toolbox — extensibility

- **MCP expansion** — let external agents trigger re-indexing and metadata updates, not just read documents (`write_document` shipped 2026-09-11; cross-collection `research_documents`, `corpus_version` freshness tokens and MCP prompts shipped 2026-09-17; `reindex_document`, `update_document_metadata`, `list_index_jobs`/`get_index_job`, ChatGPT-shape `search`/`fetch`, schema limits and titles on every tool, and a live `GET /api/mcp/catalog` shipped 2026-10-08; shipped 2026-10-23: `ask_documents` for a finished cited answer in one call when the deployment runs its own model, every synchronous tool off the event loop with a `WORKER_THREADS`-sized pool, `scripts/mcp_load.py` to measure a host's envelope, and Connect-tab entries for Hermes Agent and any streamable-HTTP client). Progress notifications during long deep searches need the streamable transport switched from JSON responses to SSE, which is a deployment-affecting change to make deliberately.
- **Agents in the browser (WebMCP)** — prototype shipped 2026-11-04: the page registers the read-only MCP tools with `document.modelContext` where a browser has it (Chromium origin trial, Chrome 149 to 162, flag `enable-webmcp-testing`), calls go through `/mcp` with the page's own session, and the Connect tab reports what this browser supports. Decided by feature detection, not the user agent. Open: the API is a moving W3C draft (the namespace already moved from `navigator` to `document`), so re-check it when the trial ends; whether to offer write tools behind a confirmation; an origin-trial token for hosted deployments; and a check against a real in-browser agent (Gemini in Chrome, an agent extension) once one can call page tools on a stable channel. Of the other built-in browser AI surfaces, only on-device translate/summarize of a passage (Translator, Summarizer, stable since Chrome 138) looks worth a display-only enhancement; the Prompt API as a zero-setup answer model is held back because Clio's answer pipeline, citation checks and cache are server-side, and the browser embedder would tie an index to a Chrome build.
- **PWA** — a web manifest and theme-color meta shipped 2026-09-12 (installable from the browser menu); an offline service worker is the remaining step for the app itself. (An exported static site can now work offline: `include=offline`, 2026-11-20.)

### Access & limits

- **Access without accounts** — Clio deliberately has no accounts: it is an open appliance by default, with `AUTH_PASSWORD` as the shared gate and a TLS proxy or private network in front; simplicity is a product requirement. Expiring, collection-limited personal MCP tokens shipped 2026-09-17. Pluggable identity shipped 2026-09-17: `IDENTITY_PROVIDER=oidc` (your own IdP) or `trusted_header` (an authenticating proxy), plus `MCP_AUDIT_TOOL_CALLS` for auditing agent reads. OAuth 2.1 on `/mcp` shipped 2026-09-17: RFC 9728 metadata and the Bearer challenge delegate to the site's IdP, so Claude Desktop, claude.ai, ChatGPT and `claude mcp login` attach with the endpoint URL alone (`MCP_PUBLIC_URL`, optional `MCP_OAUTH_SCOPE`, or `MCP_OAUTH_ISSUER` for a different IdP on `/mcp` only). ChatGPT `search`/`fetch` aliases shipped 2026-10-08. Next: per-tool token scopes, group-derived collection access for OIDC deployments, and an admin token fleet view.
- **Per-collection storage cap** — shipped 2026-09-12 (5 GiB default, `COLLECTION_STORAGE_LIMIT_BYTES`). Shipped 2026-11-18: ingest stops, and `/readyz` says why, below `MIN_FREE_DISK_MB` free. Next: per-user totals across collections, caps on total storage and on the number of collections, and an admin view of storage by collection.

### From the September 2026 product assessment

Shipped 2026-09-12: the answer cache keys on the whole request (corpus version, guide, attached instructions, provider/model, retrieval settings, source selection) and the source selection is enforced end-to-end (retrieval, tool calls, table queries, overview, citations, reports, cache). Still open, in priority order:

- **Retrieval benchmark** — shipped 2026-10-23: a versioned fixture corpus ([tests/fixtures/retrieval_bench/](../tests/fixtures/retrieval_bench/), 60 documents, 101 chunks, 80 labelled queries: plain questions, exact identifiers, quoted phrases, negation, dates, table lookups and unanswerable cases) and a runner, [scripts/retrieval_bench.py](../scripts/retrieval_bench.py) (`venv/Scripts/python scripts/retrieval_bench.py`, or `CLIO_RETRIEVAL_BENCH=1 pytest tests/benchmark -s`; skipped by default), that indexes the corpus with the real local embedding model and reports recall@5/@10, MRR, per-category breakdowns and p50/p95 latency for no reranker and candidate multipliers 2/3/5, plus throughput and shedding for `RERANK_MAX_CONCURRENT` 0 vs 2 under eight parallel callers. Numbers are comparable only within one corpus version; bump it when a passage or label changes. First run (12-core desktop, ~3 minutes, the cross-encoder passes are the cost): recall@10 is 1.0 in every configuration, 2x/3x/5x rank identically, latency is linear in the pool (p50 22 ms without the reranker, 315/500/792 ms at 2x/3x/5x), and the gate at 2 doubles throughput and cuts p95 from 3.3 s to 1.9 s under load while shedding 60% of searches to retrieval order; the defaults (3x, 2) stay. Still open: cross-user cases, citation correctness and abstention scoring; a corpus large enough that first-stage retrieval misses the top 10, so pool size can show a difference (today it cannot); and the one regression the reranker introduces (an exact-identifier query demoted from rank 2 to 7) points at identifier-aware reranking.
  *2026-11-19:* the corpus and the scoring now exist. [tests/fixtures/retrieval_hard/](../tests/fixtures/retrieval_hard/) (generated by [scripts/make_retrieval_corpus.py](../scripts/make_retrieval_corpus.py): 304 documents in four collections, 1,795 passages, 226 questions; `--fixture tests/fixtures/retrieval_hard`) and [scripts/answer_eval.py](../scripts/answer_eval.py) (citation precision, supported sentences, abstention, time to answer, against a named model). Baseline, MiniLM, before any retrieval change: recall@10 0.219 without the reranker, 0.290 / 0.305 / 0.305 at pools 2x / 3x / 5x. After B1 and B2 (recipe 2, reranker reading the source line): recall@10 0.829 without the reranker and 0.981 with it at 3x, MRR 0.898; every category at 0.93 or above at @5. Answer baseline (recipe 1, llama3.2:3b on CPU, 25 questions): citation precision 0.158, supported sentences 0.889, declined on every unanswerable question, answered 77% of the answerable ones, first text p50 25 s. Full tables in the improvement report. With the reranker the new fixture is close to saturated; add harder distractors (version 2) before judging the next retrieval change on it.
- **Privacy and authorization review** — exercise every API/tool/resource as owner, reader, outsider, revoked token and anonymous; confirm egress for embeddings, OCR, reranking and chat.
- **One dependable connector** — watched folder or SharePoint, with incremental sync, deletion propagation, resumable jobs and visible failure state.
- **Reproducible deployment** — locked dependencies, validated container + Postgres path, backup/restore and upgrade drills. Started 2026-10-16: `CLIO_PROFILE=secure` plus `scripts/preflight.py` / `GET /api/system/readiness` check posture, local model presence and every outbound path against the live configuration ([docs/SECURE_PROFILE.md](SECURE_PROFILE.md)). Shipped 2026-10-27: the container runs as an unprivileged user, `docker-compose.hardened.yml` adds a read-only root, dropped capabilities and `no-new-privileges`, and `scripts/backup.py` backs up (online-safe SQLite snapshots, SHA-256 manifest), verifies and restores the data directory. Shipped 2026-11-04: every push to master runs the backend and frontend suites (`.github/workflows/tests.yml`), including every database method against a real Postgres (`tests/test_postgres_backend.py`). Shipped 2026-11-18: settings are range-checked at start and on save; a newer database is refused, `app.db` is versioned and copied before a migration, and a backup records versions and refuses to restore over an older app. Remaining: locked dependency hashes, an SBOM in the offline bundle, scheduled backups, and schema versioning for Postgres (today: `pg_dump` before upgrading).

### Always-on constraints

- Raw data passes through by default; semantic layers exist only where Clio itself must act deterministically (aggregations, type coercion).
- When inference fails, degrade to raw-data tools and let the LLM handle semantics — never guess and pretend.
