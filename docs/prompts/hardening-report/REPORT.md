# Hardening work order: report

The report back for [HARDENING_PROMPT.md](../HARDENING_PROMPT.md), 2026-11-18. Every item below has its commit on `master` (search the log for its id), and every fix has a regression test. The roadmap is updated in the same pass: [ROADMAP.md](../../ROADMAP.md), including the decisions left for the owner.

## Per item

**Workstream A: the Library**

| Item | Status | Reproduction and result |
|---|---|---|
| A1 (R3) | Done | The smoke fixtures and the 59-document benchmark: a wrong title or author list was shown as fact. Each field now carries a confidence; a doubtful one shows *needs checking* with the reason and the file name as title. Benchmark: 0 fields wrong and shown as certain. |
| A2 (R2) | Done | Benchmark, offline, before → after below. |
| A3 | Done | Bulk rename ticked every record, bad ones included. It now ticks only records known to be right, lists the rest under *Check these first*, and offers *Undo all*. |
| A4 (R5) | Done | Indexing a PDF left it unanalysed until someone pressed Analyze. Analysis now runs as its own job after indexing (`LIBRARY_AUTO_ANALYZE`, default on). |
| A5 (R8b) | Done | One job strip for Analyze, Screen, Reviews and Find open copies, rebuilt from the server after a reload. |
| A6 (R6, R7) | Done | A blocked catalogue cost 15 s per source. A host now rests after a timeout (5 min, or its `Retry-After`), one *catalogues unreachable* note offers Try again, and Find open copies is a background job with Stop. |
| A7 | Done | The step strip follows its own column width (container query), legible at 1280x720 with both panels open. |
| A8 | Done | Repeated abstract line, "registry", an article cited as an internal document, an unconfirmed arXiv link, a silent disabled rename: each fixed. |
| A9 (R11) | Done | *Write up* builds a cited summary from verified review statements only, with quotes, pages and the reference list, and saves it in Reports. No model is called. |
| A10 (R8) | Not started: needs the owner's decision | Options in the roadmap. |

**Workstream B: models in every environment**

| Item | Status | Reproduction and result |
|---|---|---|
| B1 | Done | First run with the hub black-holed: "On this server" now says why it cannot be chosen, naming the host, with Check again (screenshot). |
| B2 | Done | `nomic-embed-text` and `nomic-embed-text:latest` were offered as two models; they are one. |
| B3 | Done | Pulling an Ollama model from first run raced its readiness probe; selecting and pulling is now one call that follows the pull. |
| B4, B5 | Done | `missing` is explained; a fourth first-run choice, *Our own embedding server*, is tested with one embedding before saving and is offered offline. |
| B6, B7 | Done | The footer counted deliberately-off features as broken; it now shows "All working" unless something needs attention, and the search model's line says which failure it is, with the matching fix. Matrix rows 3, 4 and 6 found three more places that disagreed with the footer (readiness and the search note); fixed in `8dff9e8`. |
| B8 | Done | Matrix row 4: a MiniLM collection on a `:bge` image is marked, searched by keyword with a notice, and recovered from the UI alone (*Re-index in place* or *Copy to the current model*). Verified live on the image. |
| B9 | Done | Download progress named a server folder; it names the model. |
| B10 | Done | Deny-all logging proxy: with `HF_ENDPOINT` the log held only the mirror's host; without it only `huggingface.co`; offline, nothing. The Xet client is turned off when a mirror is set. |
| B11 | Done | First `/api/bootstrap` with the hub black-holed: 6.0 s before, 0.39 s after. |
| B12 | Done | A blocked provider's model list now says why, and a model can be typed. |
| B13 (P2, P4, P5) | Done | P2 and P4 checked against the code; P5 runs answers, tools and a picture. Matrix row 4 also found that a model behind an OpenAI-compatible endpoint that refuses native tools ended the turn with "Chat failed"; it now answers in the written protocol (`003842d`). |

**Workstream C: the first five minutes**

| Item | Status | Reproduction and result |
|---|---|---|
| C1 (U8) | Done | `?tab=library` landed on Ask after onboarding. Hash routes now; verified live from fresh storage and from `#/ask`, and Back returns to the previous view. |
| C2 | Done | A non-admin, or a collection that already has sources, gets a card instead of the takeover. Found live: the card sat under the toasts; it now hangs under *Connect a model*. |
| C3 | Done (Reported) | Every *Connect a model* opened Settings wherever it was last; all now open the providers section. |
| C4 (U5) | Done, except the Sources panel | Labelled ribbon (Ask, Find, Library, Connect, More). The Sources "add" entry points and icon rail are left for the SourcesSidebar split (roadmap). |
| C5 | Done | Find's examples come from the collection. |
| C6 | Done (Reported) | Panel widths saved on a large monitor squeezed a projector's answer column; widths are clamped and share the space left after 480 px. |
| C7 (U4) | Done | One vocabulary, with a test that fails on a retired word in any template. |

**Workstream D: failures you can see**

| Item | Status | Reproduction and result |
|---|---|---|
| D1 (U2) | Done | `/documents/upload` dropped the reason; every path now returns `{filename, reason, fix}`, and a persistent row in Sources keeps them. Found live: Retry always answered "no longer on the server", because the job deleted the failed staged copy; fixed (`361b25a`), and files picked from this computer now have Retry too. Verified live for both. |
| D2 | Done (Reported) | A 401 shows one sign-in prompt and stops polling; polling backs off and pauses on a hidden tab. |
| D3 | Done (Reported) | A 422 reads "field: message"; a proxy's HTML 502 reads as a sentence that says retrying is safe. |
| D4 | Done (Reported) | Each setting that fails to save says what was not saved and why. |
| D5 | Done (Reported) | One confirm dialog for every native `confirm()`, and MCP token revoke now asks. |
| D6 | Done (Reported) | Copy works on a plain-HTTP host, falling back to a dialog with the text selected. |
| D7 | Done (Reported) | One error envelope `{detail, code, request_id}`; a 500's own text goes to the log, not to the caller. |

For the Reported items: D7's plain-text 500 and D1's dropped reason were reproduced against the running server before the fix. For C3, C6 and D2-D6 each fix comes with a regression test of the reported behaviour, but no separate live reproduction was recorded in this pass.

**Workstream E: enterprise** (done by a separate agent and merged; details in the commit messages)

| Item | Status |
|---|---|
| E1 | Done: audit rows carry source address, request id and sign-in method; sign-in events; a paging cursor; one JSON line per row for a SIEM. |
| E2 | Done: a suspended identity is refused everywhere it can sign in; Reinstate. |
| E3 | Done: a network-reachable server calls only its own model endpoints by default. **Behaviour change**: a URL typed into a browser for a model is refused there until the operator sets `AI_ALLOWED_BASE_URLS`. |
| E4 (U10) | Done, narrowed: an override of the environment is logged and listed (admin only). Admin sections not done. |
| E5 | Done: about 75 settings range-checked at start and on save; every setting documented. |
| E6 | Done: newer databases refused, `app.db` versioned and copied before a migration, backups record versions. Postgres not versioned. |
| E7 | Done for index jobs and reports, narrowed: index jobs survive a restart with *Run again*; stalled jobs stop. Re-queueing reports and Library jobs waits on R8. The proxy grace period did not reproduce. |
| E8 | Done: settings are written to the file they were read from. |
| E9 | Done, narrowed: free-disk floor in readiness and before ingest, failure metrics, request id on every log line. Caps on total storage and collection count not added (roadmap). |
| E10 | Done: browser storage per signed-in person, and *Sign out and clear this browser*. |
| E11 | Done: readiness says when one proxy address shares the sign-in budget. |

Also fixed in this pass: test isolation (hub variables leaked between tests, so download tests passed or failed by order; `b95aed0`).

## Benchmark (manifest v1, 59 documents, offline)

| Field | Before | After |
|---|---|---|
| Title, exact after normalising | 64% (38/59) | 85% (50/59) |
| Authors, mean F1 | 0.36 | 0.52 |
| Authors, whole list right | 17% (10/59) | 39% (23/59) |
| Year | 81% (48/59) | 86% (51/59) |
| Wrong and shown as certain (title · authors · year) | not measured | 0 · 0 · 0 |
| Marked needs checking (title · authors · year) | — | 13 · 33 · 2 |
| Identifiers invented | 0 | 0 (one DOI found is wrong) |

Catalogues-on numbers were not re-run.

## Model test matrix

Run on images built locally from `master` with the release Dockerfile (unchanged since v1.6.1): `clio:hardening-full`, `-slim` and `-bge-slim` (bge-small baked, as `:bge-slim` is). For each row, the endpoints behind the first-run screen and banner (`/api/embedding/status`, `/options`), the footer (`/api/features`) and `GET /api/system/readiness` were read, then a file was uploaded and searched by keyword and by meaning, and one question asked of the answer model. The release smoke test (`scripts/smoke_image.sh`: hardened, no network, semantic search on the baked model) passed on the slim image.

| Row | Search model | Answer model | Result |
|---|---|---|---|
| 1. Full image, open network | Baked, ready | Hosted key in the browser: **not run** (no hosted key on this machine) | Status, footer and readiness agree; keyword and meaning searches find the file. |
| 2. Slim image, `--network none` | Baked, ready | None: footer and readiness say so, with the fix | All surfaces agree; both searches work. |
| 3. Full image, hub black-holed, `EMBEDDING_MODEL=BAAI/bge-small-en-v1.5` (not baked) | Cannot download | Local Ollama (gemma3:4b): answered with a citation | **Found:** readiness said "it will download on first use" and a search by meaning "Still trying … minutes to fail" while the footer said the download failed. Fixed in `8dff9e8`; on the rebuilt image all four surfaces say the download failed, and keyword search works. |
| 4. `:bge` image on a MiniLM volume, `OFFLINE_MODE=1` | One collection's model missing | OpenAI-compatible (Ollama `/v1`, gemma3:4b): answered with a citation | The collection is flagged and its notice offers *Re-index in place* or *Copy to the current model*; Re-index from the UI made search by meaning work. **Found:** the search note said "Still trying" for a model that cannot arrive (fixed, `8dff9e8`), and the answer failed with "does not support tools" (fixed, `003842d`). |
| 5. Bare metal on Windows, no model cached, hub through a mirror | Downloads from mirror: **partly run** | None | Through a deny-all logging proxy, the warm-up contacted only the mirror's host with `HF_ENDPOINT` set (only `huggingface.co` without it, nothing offline). No real mirror was available, so a completed download through one was not seen. The blocked-hub first run on bare metal was run (screenshot). |
| 6. Slim image, `EMBEDDING_PROVIDER=openai_compatible`, server down at start | Error (did not answer) | Site endpoint: answered with a citation | **Found:** readiness passed the embedding server while the footer said keyword only; fixed (`8dff9e8`), now both say it did not answer. Keyword search works. **Not run:** the server coming up afterwards (the stand-in listener could not be started in this environment). |

## Acceptance demo

Not run as one scripted pass in the three environments. Parts of it were checked live at 1280x720:
- Step 1: the first-run screen offers the samples, and with the hub blocked "On this server" says why it is unavailable (screenshot).
- Step 3, partly: a failed file shows its reason, fix and Retry, and Retry works. Drag and drop (U6) is not built.
- Step 4: `?tab=library` opens the Library from fresh storage and after onboarding; records are there without pressing anything; doubtful ones are marked.
- Step 7: the footer reads "All working" on a default install (screenshot).
- Step 9, partly: rows 3, 4 and 6 above show search working by keyword with the search model gone.
- Step 10: covered by E7's tests and its live restart run (index jobs come back as interrupted with *Run again*).

Steps 2, 5, 6 and 8 were not run end to end in this pass.

## Screenshots (1280x720)

- [Library with a paper open](library-paper-open.png)
- [First-run search-index step, hub blocked](first-run-hub-blocked.png)
- [Footer on a default install](footer.png)
