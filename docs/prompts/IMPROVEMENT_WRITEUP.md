# What I would do next with Clio

Written on 2026-11-19 from a pass over the code at commit `18f7e4a` (v1.7.1). The matching work order for a coding agent is [IMPROVEMENT_PROMPT.md](IMPROVEMENT_PROMPT.md).

## The short version

The roadmap is sound, and the last two passes made the edges dependable: the Library, model setup at blocked sites, failures you can see, the enterprise review. What has had the least attention is the middle of the product: how good the retrieved passages are, how an answer arrives and is checked, and the agent memory the product is built for.

If I had full control I would do six things, in this order.

1. **Make answer quality measurable.** Today no change to search can be shown to help or hurt.
2. **Fix retrieval where the code is weak.** One real defect and four cheap gains, none needing a model or the network.
3. **Make Ask fast to read and easy to check.** Answers do not stream, and Ask is the one place the "never silently wrong" rule is not enforced.
4. **Build the agent memory.** It is the stated goal and the least built part.
5. **Protect releases and operators.** A release can ship from a failing commit.
6. **Stop the surface growing.** Then shrink it.

Items 1 to 3 are for the people using Ask and Find. Item 4 is for agents. Items 5 and 6 are for whoever runs and maintains it.

## How to read the evidence

Nothing in this pass was run against a live server. Everything comes from reading the code.

- **Checked**: I read the code that shows it.
- **Reported**: one read-only audit found it and I did not re-read it. Reproduce before acting.

Line numbers are as of `18f7e4a` and will drift.

---

## 1. Make answer quality measurable

**What is wrong.**

- The retrieval benchmark cannot tell configurations apart. It has 60 documents and 80 questions, and the roadmap records recall@10 of 1.0 in every configuration, with every reranker pool size ranking identically. *(Checked.)*
- Nothing scores answers automatically. `scripts/pilot_eval.py` asks the questions and leaves the judging to a human reviewer. That is the right tool for a customer pilot and the wrong one for a daily check.
- The app captures no signal from use. There is no "this answer was wrong" or "wrong citation" control anywhere in the backend or the frontend. *(Checked by search.)*
- No per-stage timings are recorded for a search or an answer. *(Reported.)*

**What I would do.**

- Grow the retrieval corpus until first-stage search misses, a few thousand passages with near-duplicates and distractors, so pool size, fusion and chunking show a difference.
- Add an answer-level run that needs no person: for each question, is every cited passage one of the labelled ones, does each cited sentence appear in its passage, and did the answer decline when the corpus has no answer. Run it against a local model so it works offline.
- Add two buttons under an answer, "Helpful" and "Something is wrong" (wrong answer, wrong citation, missing source). Store them on the server and export them in the question-file format the pilot script already reads. Real failures become test cases.
- Record time spent in embedding the question, vector search, keyword search, reranking and each model call, and expose them in `/metrics`.

**Why first.** Every item in section 2 is a guess until this exists.

## 2. Fix retrieval where the code is weak

All *Checked*.

**A defect: asking across all collections throws away the reranker's work.** Each collection is searched and reranked on its own, then the results are merged by `similarity_score` (`api/chat.py:864`). The reranker reorders the list and never updates that score (`services/reranker.py:450`), so the merge re-sorts by the first-stage score. That score is also not comparable between collections, because the keyword half is divided by each collection's own best keyword score (`services/vector_store.py:669-675`). The MCP `search_all_collections` picks its "best collection" the same way and searches collections one after another (`services/mcp_server.py:1164-1185`). Fix: pool the candidates from every collection, rerank once, and search the collections in parallel.

**Passages know nothing beyond their own page.**

- Text is split one page at a time (`services/chunker.py:52`), so a sentence that crosses a page break is cut in two.
- The current heading is remembered only inside one page (`services/chunker.py:93`), so page 2 of a section loses its heading.
- The text sent to the embedding model is the bare passage (`services/indexing/indexer.py:523`), with no source title or section. A passage that says "the limit is 30 days" carries nothing about which policy it belongs to.

Fix: carry the heading across pages, let a passage finish its sentence over a page break, and embed each passage with a one-line header of source title and heading path. The stored and displayed text stays as it is.

**Fusion is a weighted sum of two unlike scales.** Raw cosine similarity is added to a keyword score divided by the top keyword score (`services/vector_store.py:680-692`). The best keyword hit always gets full marks, however weak it is. Reciprocal rank fusion is the usual answer. Measure both and keep the winner.

**The model reads 1,000 characters per source and no more.** The context block is the passage alone (`api/chat.py:552-569`). Reading the neighbouring passages exists only as a tool the model may choose to call. Fix: widen the top few hits to their neighbours before the first model call, inside a size budget.

**The default search model is the weaker of the two that ship.** The catalogue describes BGE small as "stronger than MiniLM, same size class", with twice the input limit (`services/embedding_providers.py:75-76`), and MiniLM is the default. Its 256-token limit sits right at the default 1,000-character passage. Changing a default is the owner's call, and the benchmark from section 1 should make it.

One cost to fix along the way: a filtered hybrid search asks the keyword index for every match in the collection and filters in Python (`services/vector_store.py:662`).

## 3. Make Ask fast to read and easy to check

**Answers do not stream.** *(Checked.)* Every provider call is a plain blocking call. The finished answer is then split into words and replayed at 8 ms a word (`api/chat.py:1322-1325`). On a local model, which is the reference case at a blocked site, the person watches a timer for the whole generation and then watches the replay. Streaming the final answer pass is the largest felt improvement available.

**Leaving Ask cancels the answer.** *(Checked.)* The Ask view is removed from the page when another tab is opened (`frontend/src/App.vue:255`), and removing it aborts the request (`frontend/src/components/ChatTab.vue:1669`). The unsent question is lost too. A reload mid-answer loses the answer. Keep the request and the draft in the store, not the view.

**A failed answer is a dead end.** *(Checked.)* On error the placeholder is removed and the question box is already empty. Regenerate deletes the question and the old answer before the new request starts (`ChatTab.vue:1390`), so a failure leaves neither.

**Ask does not check its own citations.** *(Checked.)* The Library verifies quotes with deterministic checks. Ask relies on the system prompt alone. After an answer, check each cited sentence: the source number exists, and the figures, dates and quoted strings in the sentence appear in the cited passage. Mark what fails as "not found in the cited passage". This is the roadmap's own guiding rule applied to the front door, and it needs no model.

**Open the source beside the answer.** The roadmap calls this (U1) the largest gap, and it is still open: a citation expands a passage in place and "Open original" opens a new browser tab. I would do it straight after streaming.

**Smaller things that cost people daily.** *(Checked unless marked.)*

- Find ignores the selection made in the Sources panel, while Ask honours it.
- Uploads show no transfer progress and cannot be cancelled; there is no drag and drop (U6).
- The app has no top-level error handler, so a component error fails silently.
- Chat history is one browser-local blob capped at 50 chats, trimmed without notice (U9). *(Reported.)*
- The roadmap says U8 shipped `#/ask/<chat>`. The route carries only the tab and the Library view (`frontend/src/utils/route.js:16-22`), so a shared link opens in the recipient's last collection.

## 4. Build the agent memory

The roadmap's Phase 1 (M1 to M7) and Phase 4 (B2 to B10) are all still open. *(Reported, with the central points Checked.)* The agent surface today is a document store with a careful write tool.

**What an agent hits.**

- **Cost.** 32 tools by default, about 44,000 characters of description, with no way to load fewer. A read-only token still lists the write tools. *(Reported.)*
- **Nothing below the filename is stable.** The document id is a hash of the content, and passage ids are built from it, so every edit invalidates every id an agent saved. *(Reported.)*
- **Appending is expensive.** Each append reads the whole note, re-indexes all of it, and holds the collection's write lock while it does (`services/mcp_server.py:3466-3486`, `:3566`, `:3704`). A shared journal gets slower with every entry and blocks every other writer. *(Checked.)*
- **Memory cannot be told from sources.** Search filters are document ids, filenames, formats and upload dates (`services/mcp_server.py:928`). An agent cannot ask for its own notes only, or for what was written since yesterday, and results carry no timestamp. *(Checked for the filter list.)*
- **No history, no author, no feed.** A replace keeps no previous version. Two agents on tokens made by one person are one author. `list_changes` reports folder and git syncs only. *(Reported.)*
- **The instructions never mention writing.** *(Checked: `services/mcp_server.py:199-216`.)*

**What I would do, in order.**

1. A `notes` tool profile of about eight tools, with one `document` handle and a typed filter that includes `source_type` and `written_after`. Small, and it changes what an agent can do on day one.
2. `written_at` and `written_by` on every result and read, with the token recorded on the write. Agent writes in `list_changes`.
3. Version history, then delete, rename and move behind it.
4. A stable source id that survives edits, with the content hash kept as the version. This is the deep change. It is what makes saved references, links and cheap appends possible.
5. Append that indexes only the new text.
6. Tags, frontmatter, links and backlinks.

**Not on the roadmap, and worth adding.**

- A cheap "what do I have on X" listing: titles and first lines, no passages.
- A signal at write time that a very similar note already exists.
- A check at startup for a write that was interrupted by a hard stop. *(Reported: no reconciliation was found.)*
- `deep_search` writes no tool-call audit row. *(Reported.)*
- A load test that writes. `scripts/mcp_load.py` only reads. *(Reported.)*

## 5. Protect releases and operators

- **A release is not gated on tests.** The publish workflow pushes the versioned image tags as soon as the build finishes, runs the smoke test afterwards, and has no dependency on the test workflow (`.github/workflows/docker-publish.yml:108-128`, `:159`). Only the moving tags wait for the smoke test. *(Checked.)*
- **Saving settings can corrupt the environment file.** Values are written as `KEY=value` with no escaping (`services/config_manager.py:536-561`), and the acceptable-use text is a saveable multi-line field. A second line that reads `NAME=value` would be read as a setting at the next start. *(Checked in the code, not reproduced.)*
- **Dependencies are open ranges with no lock file**, so two builds of one commit can differ. The weekly audit resolves fresh versions and skips the requirement files the full image adds. *(Ranges Checked; audit coverage Reported.)*
- **Lint is on the honour system.** CI runs the tests only, on Linux and one Python version; the README promises Python 3.12 and Windows. *(Reported.)*
- **The Windows launcher goes stale.** After a `git pull` it neither reinstalls dependencies nor rebuilds the interface. *(Reported.)*
- **No browser test** covers add a source, ask, open a citation. That path is the product.

## 6. Stop the surface growing

Three numbers the roadmap quoted two days ago are already out of date: 162 settings is now 194, 29 MCP tools is 32, and the 4,898-line MCP module is 5,473. *(Checked.)* The README, the `docs` folder and the example environment file come to about 500 KB of prose.

- Set budgets and test them: tool descriptions under a token count, no new setting without removing one or showing why.
- One upgrade page and one backup page. Today the upgrade steps are in four places and two pages disagree about stopping the server for a backup. *(Reported.)*
- Split the large modules as each is touched, as Phase 6 already says.

## What I would not do yet

- New source formats, new tabs, or new model providers.
- Shared stores and several API servers (scaling phase 3). The flat in-memory vector index is fine to about a million passages. *(Reported from the deployment notes.)*
- Server-side chat history before streaming and the source viewer. It is a bigger change for a smaller daily gain.

## Decisions that are yours

1. **The default search model**: keep MiniLM, or move new installs to BGE small once the benchmark can show the difference.
2. **Feedback capture**: whether question text may be stored on the server at sites with classification markings, or only a verdict and source ids.
3. **Embedding passages with a title and heading line** changes what a re-index produces. Offer a re-index to existing collections, or apply it to new ones only.
4. **Who may write over MCP** (roadmap M9), still open and now more pressing.

## What this pass did not cover

- No live run, no timing, no benchmark numbers of my own.
- The Library, PRA, static export, sharing and governance code were not re-read; the last pass covered them.
- Whether the answer-level checks in section 3 produce false alarms on real answers. Measure on the benchmark before showing them to people.
