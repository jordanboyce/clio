# Work order: make the middle of Clio as good as its edges

This file is a prompt. Hand it to a coding agent working in this repository, whole or one workstream at a time. It was written on 2026-11-19 from a read of the code at commit `18f7e4a` (v1.7.1). The reasoning behind it is in [IMPROVEMENT_WRITEUP.md](IMPROVEMENT_WRITEUP.md).

## Your job

Clio is a self-hosted knowledge base: people add sources to a collection, ask questions answered with citations, and hand the same knowledge to AI agents over MCP. It runs at sites that block model hubs and AI providers, often with a small local model as the only answer model. The owner's goal for the agent side is a shared second brain: several agents reading, writing and linking notes over time, beside the sources people add.

The last two passes made the edges dependable (the Library, model setup at blocked sites, visible failures, the enterprise review). This work order is about the middle:

1. **Nothing measures answer quality**, so no change to search can be shown to help.
2. **Retrieval has one real defect and several cheap gains** that need no model and no network.
3. **Ask is slow to read and does not check itself.** Answers do not stream, and Ask is the one surface where "never silently wrong" is left to the system prompt.
4. **The agent memory is a document store with a write tool.** Roadmap phases 1 and 4 are still open.
5. **A release can ship from a failing commit**, and a few operator paths are fragile.

Fix the items below. Each has an id, the evidence for it, the files to start in, and a "done when" test. Do not add features that are not listed. Where an item overlaps `docs/ROADMAP.md`, the roadmap id is given and the roadmap's design stands; this file adds what was found since and sets the order.

## Evidence labels

Nothing behind this file was run against a live server. Every item comes from reading the code, so reproduce each one before you change anything. If it does not reproduce, say so in your report and skip it.

- **Checked**: read in the code by the author of this file.
- **Reported**: found by a single read-only audit pass and not re-read.

Line numbers are as of `18f7e4a` and will have moved.

## Rules for all of this work

- Read `docs/ROADMAP.md` and `docs/ENGINEERING.md` first. The roadmap's three rules apply: every change leaves `master` deployable with the simple install as the default; everything must still work with hubs, catalogues and providers blocked; a changed MCP tool name or shape keeps the old form for one release.
- Measure before and after. For any item in workstream B, record the benchmark numbers from workstream A before the change and after it. A change that does not improve the numbers is not shipped, however sound it looks; report it as measured and dropped.
- A change to stored data or to search must not surprise an existing install. Before committing one, run the old code and then the new code on a scratch copy of a real `data/` directory and compare results under one `PYTHONHASHSEED` (hybrid search breaks score ties by set order, so two processes differ on their own). An existing collection keeps working without a re-index; where a re-index gives better results, offer it and say what it costs.
- Every fix ships with a regression test. Backend: `venv/Scripts/python.exe -m pytest` (the system Python lacks dependencies). Frontend: `cd frontend && npm test`.
- Every source file is CRLF. Keep it that way or the diff becomes the whole file.
- Commit straight to `master` in small commits, one item or one tight group per commit, with the item id in the message. Do not push and do not tag unless asked.
- User-facing copy: plain words, sentence case, the vocabulary table in `docs/ENGINEERING.md`, no environment-variable names or internal terms in text an end user reads.
- When you touch one of the large modules (`services/mcp_server.py`, `api/chat.py`, `frontend/src/App.vue`, `ChatTab.vue`, `SourcesSidebar.vue`), move the part you are changing into its own module first, as roadmap Phase 6 describes. Do not start a separate refactoring project.
- If an item needs a product decision, stop on that item, write the options in your report, and carry on with the next one. The decisions already known are listed at the end; do not make them.

### How to check your work for real

Reading the code has been wrong about this app before; run it.

- **Local run.** Start the backend from an empty working directory so it cannot read or rewrite the checkout's `.env`: `cd <scratch>/run && PORT=8474 DATA_DIR=<scratch>/data PYTHONPATH=<repo> <repo>/venv/Scripts/python.exe <repo>/main.py`. Copy `data/models/fastembed` into `<scratch>/data/models/` first when you want the search model present. Frontend: `cd frontend && VITE_API_URL=http://localhost:8474 npm run dev`.
- **A slow local model.** Workstream C is judged on a local model, where waiting is longest. Use Ollama with a small model, and check each change with it as well as with a fast hosted one if a key is available.
- **Benchmarks.** `scripts/retrieval_bench.py` and `CLIO_RETRIEVAL_BENCH=1 pytest tests/benchmark -s`. The supplier-quality fixture (`tests/fixtures/quality_bench`, `docs/demo/quality-evaluation.json`) and `scripts/pilot_eval.py` hold a question set with expected answers and "not in the records" cases.
- **Agents.** `scripts/mcp_load.py` for load; the Connect tab's Try it log to watch calls arrive.

---

## Workstream A: measure answer quality (do first)

**A1. A retrieval benchmark that can tell two configurations apart.** *Checked.*
The fixture has 60 documents and 80 questions. The roadmap records recall@10 of 1.0 in every configuration and identical rankings at every reranker pool size, so it cannot show whether a change helps. Grow the corpus until first-stage retrieval misses: a few thousand passages, with near-duplicate documents, superseded versions, long documents whose answer sits on a late page under a heading from an earlier page, and questions whose words do not appear in the answer. Add cross-collection questions (the answer is in one of four collections). Keep the files generated or freely licensed and small enough to commit, or fetched on request as the research benchmark does. Bump the corpus version.
Start in `scripts/retrieval_bench.py`, `tests/fixtures/retrieval_bench/`.
*Done when:* the baseline shows recall@10 below 0.95 for at least one category, the reranker pool sizes produce different numbers, and the baseline table is recorded in the roadmap.

**A2. An answer-level run that needs no person.** *Checked.*
`scripts/pilot_eval.py` asks questions through `/api/v1` and leaves the judging to a reviewer. Add an automatic mode for the fixture corpus, where the right answer is known: for each question, were the cited passages among the labelled ones; does every sentence that carries a `[Source N]` have its figures, dates and quoted strings present in that passage; did the answer decline on the unanswerable questions and answer on the rest. Report citation precision, supported-sentence rate, correct-abstention rate and time to answer. It must run against a local model with the network blocked.
*Done when:* one command prints the four numbers for a named model, and the numbers for one local model are recorded in the roadmap as the baseline.

**A3. Capture what people find wrong.** *Checked: no such control or endpoint exists.*
Under each answer add "Helpful" and "Something is wrong", the second with three choices: wrong answer, wrong citation, a source is missing. Store the verdict on the server with the collection, the source ids cited and the model. Add an admin export in the question-file format `pilot_eval.py --questions` reads, so a reported failure becomes a test case. Whether the question text is stored is the owner's decision (see the end); until it is made, store the verdict and ids only, and build the export so text can be added.
Start in `api/chat.py`, `services/app_database.py`, `frontend/src/components/ChatTab.vue`, `AdminTab.vue`.
*Done when:* a verdict given in the browser appears in the admin export, and nothing is sent anywhere but this server.

**A4. Time each stage.** *Reported.*
No timings are recorded per stage of a search or an answer. Record the time spent embedding the question, in vector search, keyword search, reranking, and in each model call, and the time to the first answer text. Add them to `/metrics` and to the `retrieval` block the search already returns.
*Done when:* one answered question produces a log line and metric samples for every stage.

---

## Workstream B: retrieval

Measure each item with A1 and A2, before and after.

**B1. Asking across all collections discards the reranker's order.** *Checked. A defect; do first.*
With the scope set to all, each collection is searched and reranked on its own, and the results are merged by `similarity_score` (`api/chat.py:864`). The reranker reorders the list without changing that score (`services/reranker.py:450-451`), so the merge re-sorts by the first-stage score. That score is not comparable across collections either: the keyword half is divided by each collection's own best keyword score (`services/vector_store.py:669-675`). The MCP tool `search_all_collections` picks `best_collection` from the same score and searches the collections one after another (`services/mcp_server.py:1164-1185`), as the chat loop does (`api/chat.py:856-863`).
Pool the candidates from every collection, rerank the pool once, and take the top results from that order. Where no reranker is available, merge by rank, not by score. Search the collections in parallel, bounded by the existing worker pool.
*Done when:* a test with two collections, where the better passage has the lower first-stage score, returns it first; and the A1 cross-collection questions improve.

**B2. Passages that know where they are.** *Checked.*
Text is split one page at a time (`services/chunker.py:52`), the current heading is remembered only within a page (`services/chunker.py:93`), and the text embedded is the bare passage (`services/indexing/indexer.py:523`).
- Carry the current heading from one page to the next.
- Let a passage finish its sentence across a page break, recorded against the page it starts on.
- Embed each passage with one header line made of the source title and the heading path. The stored and displayed text does not change, and keyword search stays on the stored text.
This changes what an index holds. Record the indexing recipe per collection, keep existing collections searchable as they are, and follow the owner's decision on offering a re-index.
*Done when:* A1's late-page and heading questions improve, and an existing data directory returns the same results as before until it is re-indexed.

**B3. Fusion on ranks.** *Checked.*
Hybrid search adds raw cosine similarity to a keyword score divided by the top keyword score (`services/vector_store.py:680-692`), so the best keyword hit always scores full marks. Implement reciprocal rank fusion behind the same `semantic_weight` control, measure it against the current sum on A1, and keep whichever wins per category. The roadmap notes the reranker demotes one exact-identifier question from rank 2 to 7; check that case in both.
*Done when:* the numbers for both are in your report and the default is the better one.

**B4. Give the model the surrounding text.** *Checked.*
Each source in the context block is the passage alone (`api/chat.py:552-569`); reading its neighbours is a tool the model may or may not call. Before the first model call, widen the top few results to the passages either side, merged where they touch, inside a character budget that scales with the context size setting. The citation still points at the passage that matched.
*Done when:* A2's supported-sentence rate improves on a local model with no loss in citation precision.

**B5. Filtered keyword search reads the whole collection.** *Checked.*
When a search is filtered, the keyword index is asked for every match and the filter is applied in Python (`services/vector_store.py:662-667`). Push the filter into the query.
*Done when:* a filtered hybrid search on a large collection takes time in proportion to the filtered set, shown by A4's timings.

---

## Workstream C: Ask, fast to read and easy to check

**C1. Stream the answer.** *Checked.*
Every provider call is blocking, and the finished answer is replayed a word at a time with an 8 ms pause (`api/chat.py:1322-1325`; no provider in `services/ai_service.py` streams). On a local model the reader sees a timer for the whole generation. Stream the answer pass from each provider that supports it: the final pass of the tool loop, and the single pass at quick depth. Keep the event names the frontend already reads. A provider that cannot stream keeps today's behaviour without the artificial pause. Stop must cancel the provider call.
Real streaming also raises the cost of rendering: each piece of text re-runs the Markdown and sanitising pass over the whole answer (`frontend/src/components/AnswerEvidence.vue:90`, *Reported*). Render at most a few times a second while text arrives.
*Done when:* with a local model, the first words of an answer appear within two seconds of the model starting to write, measured by A4, and Stop ends generation on the model server.

**C2. Leaving Ask must not cancel the answer.** *Checked.*
The Ask view is removed when another tab opens (`frontend/src/App.vue:255`), which aborts the request (`frontend/src/components/ChatTab.vue:1669-1672`) and loses the unsent question. A reload mid-answer loses the answer. Hold the request, the partial answer and the draft question in the chat store so they outlive the view. A saved message still marked as streaming after a reload (*Reported*: `stores/chatStore.js`) is shown as interrupted, with Retry.
*Done when:* ask, switch to Find and back, and the answer has kept arriving; type a question, switch tabs and return, and the text is still there.

**C3. A failed answer keeps the question.** *Checked.*
On error the placeholder is removed and the question box is already empty (`ChatTab.vue:1586-1591`). Regenerate deletes the question and the old answer before the new request starts (`ChatTab.vue:1390`). Keep the question on the page with the error and a Retry button; keep the old answer until the new one has arrived.
*Done when:* stop the model server, ask, and the question is still visible with Retry; regenerate with the server stopped, and the old answer is still there.

**C4. Check the answer's citations.** *Checked: Ask has no check after the answer.*
The Library checks quotes with deterministic code (`services/research/draft_check.py`, `grounding.py`). After an Ask answer, check each sentence that cites a source: the source number exists, and the figures, dates, identifiers and quoted strings in the sentence appear in the cited passage (after the same normalising the Library uses). Mark a failing sentence in the answer as "not found in the cited passage", with the passage one click away. No model call. An answer from the cache is checked the same way.
Run it over A2's answers first and report the false-alarm rate; if it is high, narrow the check before showing it to anyone.
Start in `services/chat_evidence.py`, `api/chat.py`, `frontend/src/components/AnswerEvidence.vue`.
*Done when:* an answer with one altered figure is marked on that sentence only, and the false-alarm rate on A2's answers is in your report.

**C5. Open the source beside the answer. (Roadmap U1.)** *Checked: still open.*
A citation expands a passage under the answer, and "Open original" opens a new browser tab with no highlight (`AnswerEvidence.vue:54-56`, `:115-124`). Build the viewer the roadmap describes: the source beside the answer, at the cited page, with the passage highlighted, opened from Ask chips, Find results and Library quotes, and closed with Escape back to the sentence. For formats with no page rendering, show the text with the passage highlighted.
*Done when:* from an answer, one click shows the cited passage highlighted in its page without leaving the tab, at 1280x720 with the Sources panel open.

**C6. Adding sources: progress, cancel, drag and drop. (Roadmap U6.)** *Checked.*
There is no transfer progress (no upload progress handler exists in `frontend/src`), no cancel, and no drop target. The progress bar that exists is never shown while uploading (*Reported*: `SourcesSidebar.vue:505-515`). Show bytes sent, allow cancel, and accept files dropped on the Sources panel and on Ask.
*Done when:* dropping ten files shows a moving bar, and Cancel stops the transfer.

**C7. Find honours the source selection.** *Checked.*
Ask stays inside the sources ticked in the Sources panel. Find shows the same panel and ignores the ticks (`frontend/src/components/SearchTab.vue` never reads the selection store). Send the selection with the search and say so above the results.

**C8. Catch what the app does not expect.** *Checked.*
There is no top-level error handler in the frontend. Add one that shows a message with a reload offer and logs the error with the request id when there is one. Error messages that need a decision should not vanish after six seconds (*Reported*: `stores/uiStore.js:105`).

**C9. Say what a link opens. (Roadmap U8.)** *Checked.*
The roadmap says `#/ask/<chat>` shipped. The route holds only the tab and the Library view (`frontend/src/utils/route.js:16-22`). Add the collection to the route so a shared link opens the same collection, and correct the roadmap. The chat stays out of the URL until history is on the server (U9).

---

## Workstream D: the agent memory

Roadmap M1 to M7, M9, M10's registration part, R13 and B2 to B10 are all still open (*Reported*, item by item, on 2026-11-19). The roadmap's designs stand. This workstream sets an order that gives agents something useful early, and adds what the roadmap does not list.

**D1. A small `notes` profile. (Roadmap M4, M1, M2, M6.)** *Checked for the filter list and the instructions; the rest Reported.*
An agent loads 32 tools and about 44,000 characters of description, with no way to load fewer, and a read-only token still lists the write tools. Search filters are `document_ids`, `filenames`, `source_formats`, `date_from`, `date_to` and `include_superseded` (`services/mcp_server.py:928`); an unknown key is dropped. The server instructions never mention writing (`services/mcp_server.py:199-216`).
- Add `?tools=` profiles as M4 describes, starting with `notes` and `core`. A read-only credential never lists a write tool.
- One `document` parameter that takes a name or an id, in every tool that takes a document (M1).
- A typed `filters` object that rejects unknown keys and adds `source_type` (for example `agent_note`) and `written_after` / `written_before` (M2).
- Instructions that cover reading, writing and the change feed in under ten lines (M6).
*Done when:* `tools/list` for `notes` is under 4,000 tokens, held there by a test; an agent can search only agent notes written in the last day; a misspelled filter key is an error that names the valid keys.

**D2. Who wrote what, and when. (Roadmap B3, B4.)** *Reported.*
Results carry no timestamp. The author recorded is the person who created the token, so two agents are one author, and the write's audit row has no token id. `list_changes` reports folder and git syncs only. `deep_search` writes no tool-call audit row.
Return `written_at` and `written_by` (the token's name) from search results and the read tools; record the token on the write; add agent and human writes to `list_changes`; route `deep_search` through the same audit seam as the other tools.
*Done when:* two agents on different tokens write to one collection, and a third can list what changed since a time, with the right author on each.

**D3. History, then delete, rename and move. (Roadmap B2, B9.)** *Reported.*
A replace keeps no previous version. Keep a bounded number of versions per note, add `list_versions` and `read_document(version=…)`, then add soft delete, rename and move behind the write permission with a recoverable trash.
*Done when:* an overwritten note can be read back and restored by an agent and from the Sources panel.

**D4. An identity that survives an edit.** *Reported.*
The document id is a hash of the content and passage ids are built from it, so every edit invalidates every id an agent has saved, every `filters.document_ids`, and every stored citation. Add a stable source id per note that does not change on edit; keep the content hash as the version. Old ids keep resolving for one release. This is a stored-data change: follow the scratch-copy rule above and write the migration with a pre-upgrade copy, as `app.db` migrations do.
*Done when:* an id saved before three edits still reads the current note, and a citation made before an edit says it points at an older version.

**D5. Append without re-indexing the whole note. (Roadmap B5.)** *Checked.*
Each append reads the whole file, indexes all of it, and holds the collection's write lock for the duration (`services/mcp_server.py:3466-3486`, `:3566`, `:3704`). Each write also rewrites the whole vector index file (*Reported*: `:3734`, `services/vector_store.py:811-859`). Index only the appended text, plus the one passage it joins, and batch the index save. Add `edit_document` as B5 describes.
*Done when:* appending to a 500 KB journal takes about as long as appending to an empty one, and sixteen writers appending to different notes in one collection all finish inside the lock timeout. Add a writing mode to `scripts/mcp_load.py` to show it.

**D6. Finding what is already known.** *Reported: not on the roadmap.*
- A cheap listing for "what do I have on X": title, first line, tags, `written_at`, no passages. One call, small.
- When a write is very close to an existing note, say so in the response and name the note; do not refuse it.
- Roadmap B7 and B8 (tags, frontmatter, links, backlinks) follow from here, in the roadmap's design.
*Done when:* an agent starting a session can get an index of its notes on a topic in one call under 2,000 tokens.

**D7. A write interrupted by a hard stop.** *Reported; reproduce first.*
The audit found no startup check for a write killed between the database commit, the file swap and the index save, and no sweep for leftover `.write-*` directories. Kill the process at each of those points in a test, record what state is left, and add a startup reconciliation that repairs it and logs what it did.
*Done when:* a kill at each point leaves, after restart, exactly one current version of the note, searchable.

---

## Workstream E: releases and operators

**E1. Publish only what passed.** *Checked.*
`.github/workflows/docker-publish.yml` pushes the versioned tags when the build finishes (`:108-128`), runs the smoke test afterwards, and does not depend on the test workflow. Only the moving tags wait for the smoke test (`:159`). Require a passing test run for the tagged commit before any push, and push no tag until the smoke test has passed.
*Done when:* a tag on a commit with a failing test publishes nothing.

**E2. Saving a setting must not be able to write a second setting.** *Checked in the code; reproduce first.*
`_update_env_file` writes `KEY=value` with no escaping and not atomically (`services/config_manager.py:536-561`), and `aup_text` is a saveable multi-line field. Reproduce with a two-line value whose second line is `NAME=value`. Then: refuse or encode line breaks for every field written to the file, write through a temporary file and rename, and keep multi-line values in the database only, as `data_classifications` already is.
*Done when:* a test saves a multi-line value and the file gains exactly one line, or none.

**E3. Builds that repeat.** *Ranges Checked; the rest Reported.*
Requirements are open ranges with no lock file, and the weekly audit resolves fresh versions and does not cover the requirement files the full image adds. Add a locked, hashed requirements set per image variant, build the images from it, audit the locked set, and add a job that refreshes the lock and runs the tests. This is the roadmap's "locked dependency hashes".
*Done when:* two builds of one commit install identical versions, and the audit reads the same files the image installs.

**E4. Make CI enforce what the repository claims.** *Reported.*
CI runs pytest on Linux with one Python version; no lint runs, though the code carries well over a hundred `# noqa` markers; the README promises Python 3.12 and Windows. Add the linter with its configuration to the repository and to CI; add a Python 3.12 job; add a Windows job for a fast subset (mark it).
*Done when:* a lint error fails CI, and a Windows-only path failure fails CI.

**E5. One browser test of the product's main path.** *Reported.*
There is no browser end-to-end test. Add one, using the Playwright setup `scripts/screenshots.py` already has: first run with sample documents, ask a suggested question (a stub model server is enough), see text arrive, open a citation in the viewer from C5.
*Done when:* it runs in CI against the built frontend and the real backend.

**E6. The bare-metal path.** *Reported; reproduce first.*
`run.bat` skips the dependency install and the frontend build once they have run, so a `git pull` leaves both stale; it has no Python version check. The README's Python route omits the frontend build, so the first page says the build is missing. Fix both, and put upgrade steps for every install type on one page. The README and `scripts/backup.py` disagree about stopping the server for a backup; make them agree with the code.

**E7. Hold the size.** *Checked.*
Settings went from 162 to 194 and MCP tools from 29 to 32 since the roadmap was written. Add tests that hold the counts: tool descriptions per profile (D1 adds the first), and a settings test that fails when a field is added without a line in the example file and a reason in the commit. Correct the numbers in the roadmap.

---

## Decisions for the owner (write up options, do not choose)

- **The default search model.** MiniLM is the default; the catalogue calls BGE small stronger in the same size class with twice the input limit (`services/embedding_providers.py:75-76`). Report A1's numbers for both. Options: keep MiniLM; BGE small for new installs only; BGE small as the `:latest` image's model with the documented migration.
- **A3: is the question text stored with a verdict**, at sites with classification markings? Options: verdict and ids only; text stored unless the collection is marked local-only; text always.
- **B2: re-indexing existing collections** for the new passage headers. Options: new collections only; offer a re-index with its cost; re-index in the background after an upgrade.
- **Roadmap M9: who may write over MCP.** Unchanged, and D1 to D5 make it matter more.
- **Roadmap R8** and the other three decisions already listed in the roadmap stay open; nothing here depends on them.

---

## Acceptance

Run this once on a default install with a local model and the network blocked, and once with a hosted model if a key is available. Record the result of each step.

1. Run A1 and A2. Each number is at or above the baseline recorded before workstream B, and at least the cross-collection and late-page categories are better.
2. Ask a question. Text appears within two seconds of the model starting to write. Switch to Find and back; the answer kept arriving.
3. Stop the model server and ask again. The question stays on the page with one plain sentence and Retry. Start the server and retry; it answers.
4. Ask across all collections for something held in one of four. The first source cited is from the right collection.
5. Click a citation. The source opens beside the answer with the passage highlighted.
6. Alter one figure in a stored answer (or use the test fixture) and see that sentence, and only that sentence, marked.
7. Drop ten files on the Sources panel. A bar moves; Cancel stops it.
8. Mark an answer "Something is wrong". It appears in the admin export.
9. Connect an agent with `?tools=notes`. It writes three notes, appends to one twenty times, edits one, and a second agent lists what changed since the start with the right author on each. An id saved at the start still reads the current note.
10. Kill the server during an append and restart. The note has one current version and is searchable.
11. Push a tag to a commit with a failing test on a fork or a dry run. Nothing is published.

## What to report back

For each item: done, skipped (did not reproduce, or needs a decision), or changed in scope, with the reproduction you ran. The A1 and A2 numbers before and after each workstream B item, including the ones that did not help. The false-alarm rate for C4. The time to first text for C1 on a local model. The `tools/list` size for each profile. Then update `docs/ROADMAP.md`: mark what shipped with its date, correct the figures and the U8 note, and move anything you found and did not fix into the right phase.
