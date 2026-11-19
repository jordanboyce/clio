# Improvement work order: report

The report back for [IMPROVEMENT_PROMPT.md](../IMPROVEMENT_PROMPT.md), 2026-11-19. Every item marked done has its commit on `master` (search the log for its id) and a regression test. Nothing has been pushed or tagged. The roadmap is updated in the same pass: [ROADMAP.md](../../ROADMAP.md).

## What changed for people and agents

- **Search finds the right passage far more often.** On the new benchmark, recall@10 with the reranker went from 0.31 to 0.98 for new collections (B2), and asking across all collections keeps the reranker's order (B1).
- **Answers stream** from Ollama, OpenAI-style and Anthropic providers as the model writes them, and Stop stops the model (C1).
- **Ask keeps its work**: switching tabs no longer cancels the answer or loses the draft; a failed answer keeps the question with Retry; a failed regenerate keeps the old answer (C2, C3).
- **Ask checks its own citations** with no model, and marks a sentence whose figure, identifier or quote is not in the passage it cites (C4).
- **People can say an answer was wrong** (Helpful, or wrong answer / wrong citation / missing source), and an admin exports those as test cases (A3).
- **Adding sources** shows bytes sent, can be cancelled, and takes files dropped on Sources or Ask (C6).
- **Agents** can connect with `?tools=notes` (8 tools, about 1,700 tokens instead of 32 tools and about 16,700), search their own notes, and get an error for a misspelled filter (D1). Every result and read says when a source was written and by which agent, and `list_changes` reports agent writes (D2).
- **Releases** publish nothing from a commit whose tests fail, and add no tag before the smoke test passes (E1). Saving the acceptable-use text can no longer write a second setting into the env file (E2).

## Per item

**Workstream A: measure answer quality**

| Item | Status | Reproduction and result |
|---|---|---|
| A1 | Done | The 60-document fixture scored recall@10 1.0 everywhere. New generated fixture `tests/fixtures/retrieval_hard` (304 documents, 4 collections, 1,795 passages, 226 questions in 8 categories, labels by answer sentence so any chunking is measured). Baseline recall@10 0.219 without the reranker and 0.290 / 0.305 / 0.305 at pools x2 / x3 / x5: the pool sizes now differ. Table below. |
| A2 | Done | `scripts/answer_eval.py` runs the app's own answer turn in-process against a named model and prints citation precision, supported-sentence rate, both halves of abstention and time to answer. Baseline below (llama3.2:3b, CPU, network blocked). |
| A3 | Done, verdict and ids only | Helpful / Something is wrong (three choices) under each answer; stored on this server (SQLite and Postgres, tested on both) with collection, model and cited sources; the question and answer are not sent. Admin shows counts and exports the wrong ones as a `pilot_eval.py --questions` file whose cases say the question must be added. Verified live: a wrong-citation report appeared in the export. Storing question text waits on the owner (decision 3). |
| A4 | Done | Embedding the question, vector search, keyword search, rerank, each model call, the model's own wait for its first text, first text on screen and the whole answer: `clio_stage_duration_seconds` at `/metrics`, `retrieval.timings_ms` on every search, one log line per answer. Verified live: `embed_query 423, vector_search 0, keyword_search 535, model_first_text 37479, first_text 38966, model_call 49310, answer 51084`. |

**Workstream B: retrieval** (numbers below; every run on `retrieval_hard`, MiniLM, `PYTHONHASHSEED=0`)

| Item | Status | Reproduction and result |
|---|---|---|
| B1 | Done, in part | Reproduced: the merge sorted on the first-stage score. Results now carry the cross-encoder's score and merge on it; collections are searched in parallel in the caller's context; MCP `best_collection` follows the merged order. Cross-collection recall@10 with the reranker 0.15 → 0.25. **Measured and dropped:** merging by rank without a reranker (0.15 → 0.10), so that case keeps the old score. |
| B2 | Done | Heading carried across pages, a split sentence finished on its first page, a source line embedded above each passage, and the reranker reading the same line. Per-collection recipe in `index_info`; existing collections unchanged (checked on a scratch copy of four real collections, identical results). recall@10 with the reranker 0.324 → 0.981. |
| B3 | Measured, not adopted | Reciprocal rank fusion behind `semantic_weight`. It lost: recall@10 0.219 → 0.190 without the reranker, identifiers 0.95 → 0.75 at @5. The fusion is one function now and the benchmark's `--fusion rrf` reruns it; the default is unchanged. The "identifier demoted from rank 2 to 7" case is from the old fixture; identifiers on the new one are 1.00 at @5 with B2 and the reranker. |
| B4 | Not started | Neighbouring passages in the context. Measure with A2 when done. |
| B5 | Done, in part | Reproduced: a filtered search fetched every keyword match and filtered in Python. The allowed chunks are now joined into the FTS5 query: on a 200,000-chunk index, 1.0-1.5 s before and 0.15-0.2 s after, same results. The cost still follows the number of matches, not the filtered set; the shape that would (`rowid IN (subquery)`) measured 12 s for 2,000 chunks, because FTS5 cannot use it. |

**Workstream C: Ask**

| Item | Status | Reproduction and result |
|---|---|---|
| C1 | Done | Live, Ollama llama3.2:3b on CPU: 28 pieces arrived from 44.0 s to 55.2 s instead of all at 55 s; the model spent 37.5 s reading the prompt (CPU only here, no VRAM in use), and the first piece reached the page as soon as the model wrote it. Stop: Ollama's log shows `srv stop: cancel task` for the stopped request, and the turn ends with no follow-up call. Rendering is throttled to every 150 ms while text arrives. |
| C2 | Done | Verified live: asked, opened Find during the answer, came back to the finished answer. The draft survives a tab switch. A message saved mid-stream reads as interrupted, with Retry, after a reload. |
| C3 | Done | Verified live with a model name that does not exist: the question stayed in the thread with the error and Retry; Retry after fixing the model answered it once. A failed regenerate keeps the old answer. |
| C4 | Done | `services/answer_check.py`. An answer with one altered figure is marked on that sentence only (test). False alarms on real answers: on A2's 25 llama3.2 answers it marked 2 of 18 cited sentences, 1 right (a figure cited to the wrong policy) and 1 false alarm ("as stated in Source 4" read as the figure 4). Fixed; the same answers now give 1 mark, the right one. A second live answer showed the same kind of false alarm on a server started before the fix. The sample is small: see "Not verified". |
| C5 | Not started | The source viewer (U1). |
| C6 | Done | Reproduced: only a spinner while transferring. Now a bar follows the bytes sent with a Cancel; files dropped on Sources are added at once, and on Ask are passed to Sources. Verified live: ten 3 MB files dropped, the bar read 0, 6.5 and 23 MB of 29 MB; a second drop cancelled mid-transfer added nothing and said so. |
| C7 | Done | Verified live: one source ticked, Find returned only its passages and said "only in the 1 selected source". |
| C8 | Done | Top-level handler for component errors and unhandled rejections: one message with Reload, kept until dismissed, logged with the request id. A toast that offers an action no longer times out. |
| C9 | Done | The hash carries the collection (`#/ask?c=default`), verified live. The roadmap's U8 note claimed `#/ask/<chat>`; corrected. |

**Workstream D: the agent memory**

| Item | Status | Reproduction and result |
|---|---|---|
| D1 | Done, in part | `?tools=notes` (8 tools, 6,700 characters, about 1,700 tokens; 1,000 for a read-only token) and `?tools=core` (12 tools, about 2,900 tokens), held by a test; no profile lists all 32 tools (about 16,700 tokens) as before. A read-only credential never lists a write tool. Unknown filter keys are an error naming the valid ones; `source_type` and `written_after` / `written_before` added; tested end to end (an agent finds only its own note). Instructions cover writing. **Left:** M1's single `document` parameter. |
| D2 | Done, in part | Reproduced: results had no time; the author was the token's creator; `list_changes` showed syncs only; `deep_search` wrote no audit row. Now a write records the token's name (new nullable `documents.written_by`), results and reads carry `written_at` and `written_by`, `list_changes` reports each agent write with `by`, and `deep_search` goes through the audit seam. Tested with two agents and a third reading the feed. **Left:** human uploads in the change feed (they are in the audit trail). |
| D3 to D7 | Not started | History, stable ids, cheap append, finding what is known, crash reconciliation. D4 and D7 are stored-data changes that need the scratch-copy procedure. |

**Workstream E: releases and operators**

| Item | Status | Reproduction and result |
|---|---|---|
| E1 | Done | The test workflow is callable and runs first; the build pushes by digest only; every tag is added after the smoke test; signing after that. Held by a structural test. **Not verified:** a tag on a failing commit, which only GitHub can run. |
| E2 | Done | Reproduced: `AUP_TEXT` with a second line `CHUNK_SIZE=5` set `CHUNK_SIZE=5` at the next read. Values with line breaks are now database-only and their old line is removed; the file is written as UTF-8 through a temporary file and a rename. |
| E3 to E6 | Not started | Locked dependencies, lint and Windows in CI, the browser end-to-end test, the bare-metal path. |
| E7 | Done | Thirteen settings had no line in `.env.example`; each has one now. A test fails when a setting is added without a line, when settings pass 193, when the always-on MCP tools pass 32 or 70,000 characters, or a profile passes 16,000 characters. |

## Numbers

### A1 baseline and workstream B

`retrieval_hard` v1, MiniLM (all-MiniLM-L6-v2), cross-encoder ms-marco-MiniLM-L-6-v2, top_k 10, 12-core desktop. Latency figures are inflated by other work running at the same time and are not comparable between rows.

| configuration | recall@5 | recall@10 | MRR |
|---|---|---|---|
| Baseline, no reranker | 0.186 | 0.219 | 0.130 |
| Baseline, reranker x2 | 0.210 | 0.290 | 0.166 |
| Baseline, reranker x3 | 0.205 | 0.305 | 0.165 |
| Baseline, reranker x5 | 0.214 | 0.305 | 0.167 |
| B1 with rank merge (dropped), no reranker | 0.167 | 0.210 | 0.124 |
| B1, reranker x3 | 0.200 | 0.324 | 0.171 |
| B3 RRF, recipe 1, no reranker (dropped) | 0.143 | 0.190 | 0.092 |
| B3 RRF, recipe 1, reranker x3 | 0.200 | 0.329 | 0.172 |
| B2 recipe 2, no reranker | **0.738** | **0.829** | **0.583** |
| B2 recipe 2, reranker on the bare passage | 0.257 | 0.438 | 0.199 |
| B2 recipe 2, reranker on the headed text (shipped) | **0.967** | **0.981** | **0.898** |

Recall@5 by category, reranker x3:

| | near_duplicate | late_page | cross_page | paraphrase | identifier | revision | cross_collection |
|---|---|---|---|---|---|---|---|
| Baseline | 0.20 | 0.12 | 0.00 | 0.08 | 0.95 | 0.20 | 0.12 |
| B2 (shipped) | 0.93 | 0.97 | 1.00 | 1.00 | 1.00 | 0.95 | 0.95 |

Revision safety (the current passage retrieved and no stale one above it): 35% → 90%.

Without the reranker, recipe 2 still has room: late_page 0.62 and cross_collection 0.55 at @5. With it, the fixture is close to saturated; a v2 of the fixture should add harder distractors before the next retrieval change is judged on it.

### A2 baseline

`retrieval_hard`, 25 questions spread over the categories (22 answerable, 3 not), top_k 5, llama3.2:3b on Ollama (CPU only on this machine), network blocked.

| measure | recipe 1 (before B2) | recipe 2 (after B2) |
|---|---|---|
| citation precision | 0.158 | 0.733 |
| supported sentences | 0.889 | 1.0 |
| declined when unanswerable | 1.0 | 1.0 |
| answered when answerable | 0.773 | 0.682 |
| answer, p50 | 34 s | 32 s |
| first text on screen, p50 | 25 s | 26 s |

"Answered" counts an answer with no citation as declining; llama3.2:3b often answers without citing, so the drop from 0.773 to 0.682 is partly the scorer's strictness, not the retrieval. The time to first text is the CPU reading the prompt (Ollama ran with no VRAM here).

## Decisions

Four were taken on 2026-11-19 at the owner's request, as recommended here.

1. **The default search model: keep MiniLM**, decided on the numbers. On `retrieval_hard` with recipe 2, with BGE's query prefix applied (both local backends add it): MiniLM recall@10 0.829 and BGE small 0.600 without the reranker; 0.981 and 0.938 with it; revision safety 90% and 85%. The catalogue's "stronger than MiniLM" label for BGE small was wrong for this kind of collection and now reads "same size class, reads longer passages". The `:bge` images stay.
2. **Re-indexing existing collections for recipe 2: offer it.** The notice above the Sources list says a collection still on recipe 1 can search better, what it costs, and offers Re-index now or Not now (remembered per collection), to whoever may re-index. A collection re-index rebuilds from an empty index, so it takes the current recipe; tested with a real re-index of an old collection.
3. **Question text with feedback: stored unless local-only.** The question is kept so the export is a runnable case, except when the answer could have used local-only data (any collection, when asking across all); not being able to tell counts as local-only. The answer text is never stored.
4. **Unknown MCP filter keys: a warning for one release.** Following the roadmap's compatibility rule, an unknown key is ignored again, and the response now carries `warnings` naming it, the valid keys and that it becomes an error in the next release (search_collection, search_all_collections, deep_search).

Still open: **roadmap M9, who may write over MCP**, and the decisions the roadmap already lists.

## Found and not fixed

- **The chunker's overlap cuts words.** The 200-character overlap seed is the last 200 characters of the previous paragraph, so a chunk can start mid-word ("ception. Questions about…"). Harmless to search, ugly in a citation. Phase 6 or the next chunking change.
- **Ollama on this machine runs on CPU** (no VRAM in use), so the time to first text is dominated by reading the prompt (25-40 s). Streaming cannot help that part; B4's larger context would make it longer. A smaller `top_k` for local models, or a shorter prompt, is the lever.
- **Provider errors during streaming are not counted** in `clio_provider_errors_total`: the counter wraps the blocking calls only.
- **A collection rebuilt in place keeps recipe 1** (see decision 2).

## Not verified

- E1 on GitHub (a tag on a failing commit).
- The full suites, run twice on this machine: the failures were the two git lost-clone tests that only fail on Windows, one parse-timeout test that fails under parallel load and passes alone, one frontend panel test that times out under load and passes alone, and the provenance test while the tree had uncommitted edits. Final run at c1a7251 on a clean tree: backend 3,288 passed, 48 skipped, 3 failed (the two Windows-only git tests and the load-timing parse test); frontend 736 of 736; lint has no errors.
- C4's false-alarm rate on a larger sample or a stronger model; the A2 sample had 18 cited sentences.
- C1 with a hosted provider (no key on this machine) and with Bedrock.
- Postgres: `get_filtered_chunk_ids` is the per-collection SQLite store, so D1's filter does not touch the Postgres backend, but the full suite was run on SQLite only.
