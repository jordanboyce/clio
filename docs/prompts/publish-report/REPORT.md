# Report: more to publish from a collection

The work order ([EXPORTS_PROMPT.md](../EXPORTS_PROMPT.md), written from a read of `8c257c3`) asked for 27 items in eight workstreams. This is what happened to each, from `8c257c3` to the commit that adds this file, all on `master`, one item per commit with its id in the message. Nothing was pushed.

Everything below was run on this machine (Windows 11, i7-8750H). Browser checks used headless Edge 152 over the DevTools protocol, with every host but `127.0.0.1` unreachable. "Real model" means `all-MiniLM-L6-v2` from the fastembed cache and the transformers.js 4.3.2 runtime copied out of the local `clio:hardening-slim` image (`/opt/clio/static-search/vendor`); nothing was downloaded.

## The acceptance run

`tests/test_static_acceptance.py` runs the eleven steps on the sample collection (the four files the welcome screen offers), exported as a whole site with `--include all`, with the real model. It needs `CLIO_TEST_STATIC_RUNTIME` pointing at a runtime folder and is skipped without it. Last result:

| Step | Result |
|---|---|
| 1. Contents page, four sources, each opens | Pass. 4 sources. The register's page has no passages (a spreadsheet is indexed as a table), only its table. |
| 2. Search from a reader page, every result a link that lands | Pass, by meaning: 8 results, all links; the first opened at its passage (`:target`). |
| 3. Related passages, no model and no vectors fetched | Pass. The page fetched no `vectors.bin`, nothing under `models/` or `vendor/`. |
| 4. Paraphrase shows its answer; unrelated query none | Pass. "What is the deadline for returning checked-out gear?" showed the answer with a working citation; "What is the capital of Australia?" showed none. |
| 5. Register sorts by date, filters by name; CSV runs no formula | Pass: 18 rows sorted by date, "Laptop" filtered to 3; no CSV cell starts with `= + @`, tab or CR. |
| 6. A held source is in no file, and no count gives it away | Pass. The held register's id, file name and rows appear in no file; the manifest lists 3 sources and says nothing about a fourth. |
| 7. `<img src=x onerror=alert(1)>` in a source runs nothing | Pass on 9 pages and the search box: no element made from the text, no dialog. |
| 8. Load, go offline, reload, search | Pass with the server stopped, not emulated: the site reloaded and found 6 results. |
| 9. Change one source, export over the old folder, read the feed | Pass: one entry, "Changed: 03 equipment desk note". |
| 10. Last release's `clio-search.js` against the new export | Pass: the v1.7 `clio-search.js` and `clio-static-search.js` (from `8c257c3`) searched the new export by meaning, 8 results. |
| 11. `llms.txt` at the root, four sources | Pass. |

The run found two real defects, both fixed in `c40695c`: ONNX Runtime loads its WebAssembly glue from a `blob:` URL, which the reader pages' CSP refused, so search by meaning silently fell back to keywords on every reader page; and the search page (`index.html`) carried an inline script.

## Each item

| Item | Outcome | Commit |
|---|---|---|
| A1 Split the export module | Done. Package of 7 modules at the split (now 21 with the additions), none over 300 lines; the 48 existing tests passed unchanged. | `d2e772c` |
| A2 One `include` option | Done: HTTP, CLI, dialog checkboxes, sizes and reasons in the preview, files and checksums in the manifest, the choice in the audit entry. Nothing chosen gives the export as before. The "everything chosen" leak test covers every addition. | `a2284a4` |
| A3 Shared code for components | Done: `clio-common.js`; a 20-line second component; the browser harness. | `5579d87` |
| B1 A page for each source | Done. | `03da959` |
| B2 Results fall back to the reader | Done, as the last address. | `5c468f9` |
| B3 The site's address, sitemap, canonical links | Done. | `834524e` |
| B4 Markdown pages | Done; **not built with MkDocs or Hugo**, neither is installed here. | `af2dbf2` |
| C1 `<clio-related>` | Done. | `4a8277c` |
| C2 `<clio-answers>`, answers written in advance | Done, with the approval step, except a way to write an answer in the app: answers are written over the API. | `d75e1d1` |
| C3 `<clio-cite>` | Done, without the optional page thumbnail. | `dfe91d0` |
| C4 A report as one file | Done. | `4281318` |
| C5 `<clio-timeline>` | **Stopped**: the report does not hold events (below). | none |
| C6 Index and glossary | Done (an A to Z index; no glossary of definitions, which nothing stores). | `6a7160b` |
| D1 Spreadsheets | Done, with a 5,000-row limit. | `ee17a2b` |
| E1 Measure the whole-index download | Done. | `2d3c4a2` |
| E2 An index in pieces | **Stopped for a decision** (keyword search; below). E1 shows the need. | none |
| F1 `llms.txt` | Done. | `647682a` |
| F2 A collection as an agent skill | Done; **not loaded in Claude Code here** (that would have started another agent session). The format follows `skills/clio/SKILL.md`. | `044c433` |
| F3 Passages and vectors for other tools | Done; Parquet left out (no package). | `d875af1` |
| G1 What changed between exports | Done. | `bcb96b3` |
| G2 A site that works offline | Done. | `9199262` |
| G3 A reference list | Done. | `cb3c3d3` |
| H1 Study cards | Done for the FAQ report; the study guide cannot (below). | `3ff4cbe` |
| H2 Print and EPUB | Done; the EPUB **not run through epubcheck** (not available). | `0cddfa8` |
| H3 Slides | Done; **not opened in Marp or Reveal.js**. | `36ae92d` |
| H4 Transcripts that play | Done, from the times in the text. | `473eb54` |
| H5 An audio overview | Written up, no code (below). | none |
| H6 Share cards | **Stopped**: needs Pillow, which the base install does not have (below). | none |

## What the "Assumed" statements turned out to be

| Statement | What was true |
|---|---|
| A3: the reference client is tested only through `tests/test_static_export.py`, which checks files | Not quite. There was also a jsdom test of `<clio-search>` in the frontend suite (`frontend/src/utils/__tests__/clioSearchElement.test.js`), with a stubbed `fetch`; it found a defect in this work (a manifest object loaded twice could not take its `baseUrl` again), fixed before the last commit. But nothing ran the components in a real browser, and Playwright is not in the venv. The new harness uses `websockets` (already brought by `uvicorn[standard]`) to drive an installed Edge/Chrome/Chromium, so no package was added. |
| C4: reports are stored as text with `[Source N]` markers and a record of sources and content hashes | Mostly. `content` is Markdown; `source_documents` holds id, file name and content hash. But the citations are mixed: the report prompt asks for `[filename p.N]`, while the chat loop it runs through asks for `[Source N]`, so a report can hold either. The file handles both. The only download before was the browser saving `content` as `.md`. |
| C5: the timeline report is prose written by a model | True. A Markdown list with "the date (or best-known period) in bold". |
| H1: the study guide report is prose | True. Its "Review Questions" have no answers. The FAQ report does hold pairs. |
| H4: segments are not stored after indexing | True. Only `[mm:ss]` prefixes in the text survive, in pages of 240 seconds. |
| Rules: "a setting stored with the collection (like the addresses) ... travel[s] in a `.clio.zip` bundle, as the addresses do" | **Not true before this work.** Each source's own address travelled (inside `metadata.db`), but the collection's base address for files did not travel at all. B3 now carries both collection addresses in the bundle, checked again on import. |
| Checked: passages overlap | Confirmed, by up to the chunk overlap in characters (or a whole short last paragraph of up to 120), never across pages. No page's whole text is stored, so the reader reassembles pages from passages. |
| Checked: `normalize_entity` strips plurals | Confirmed (`analysis` is kept as `analysi`); only the normalised form is stored. |

## Sizes

What each addition adds, in bytes, before a server compresses it. "Core" is the search files alone (manifest, passages, vectors). The sample is the four sample files with the real model; the 5,000-passage collection is generated text (313 sources, 5,015 passages) with the tests' 8-dimension fake model, so `tools` there holds vectors of 8 numbers: at 384 dimensions `vectors.npy` is about 7.7 MB more. Additions that need something the generated collection lacks (answers, tables, references, transcripts) have no figure there.

| | Sample (8 passages) | 5,000 passages |
|---|---|---|
| Core | 11,571 | 2,801,913 |
| `reader` | 12,230 | 2,806,595 |
| `related` | 3,782 | 649,706 |
| `answers` (one answer) | 3,864 | — |
| `terms` | 1,887 | 813 |
| `tables` | 5,149 | — |
| `tools` | 16,374 | 3,215,237 |
| `changes` | 1,604 | 1,544 |
| `book` | 7,095 | 2,768,009 |
| `offline` | 8,655 | 8,598 |
| `markdown` | 2,933 | 2,101,243 |
| `llms` | 3,242 | 2,133,672 |

The generated text repeats a small vocabulary, so the index of terms finds almost none in it; a real collection's index is up to 400 terms, about 400 bytes each.

## E1: where the whole-index download stops

From `scripts/static_scale_bench.py`, real format, generated text, 20 Mbit/s and 40 ms, uncompressed (worst case):

| Passages | Download | Gzipped | First result (keyword / meaning) | Ranking | Heap |
|---|---|---|---|---|---|
| 10,000 | 12.5 MB | 4.7 MB | 6.0 s / 5.8 s | 13 ms | 24 MB |
| 50,000 | 62.6 MB | 23.4 MB | 30.6 s / 29.4 s | 107 ms | 87 MB |
| 200,000 | 250.6 MB | 92.9 MB | 114 s / 109 s | 376 ms | 316 MB |

The ranking never matters; the download does. Past about 10,000 passages the first visit is slow, and past 50,000 too slow. The generated text compresses about six times; real prose about three, so real gzipped text is about twice the figure.

## C2: the false-match rate and the threshold

`scripts/answer_threshold.py` on `tests/fixtures/static_answers.json`: 20 published questions, 40 paraphrases, 50 queries that should match none (25 off-topic, 25 on the same topics but not published). The threshold is the lowest score with no false match and no wrong answer on the fixture, plus 0.02.

| Model | Threshold | Paraphrases answered | Wrong answers | False matches |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 0.58 | 55% | 0% | 0 of 50 |
| BAAI/bge-small-en-v1.5 | 0.77 | 40% | 0% | 0 of 50 |

So it is strict: half the fair paraphrases get the ordinary results instead. At 0.50 MiniLM answers 80% of paraphrases, with 2% wrong answers and 2% false matches. The closest unrelated query was "How do I book a desk for tomorrow?" against the published "How do I book a meeting room?". The fixture is small and from one domain; the threshold should be measured again on a site's own questions before the owner relies on it.

## Stopped, and why

**C5, timelines.** The timeline report is a Markdown list whose dates are free text ("the date (or best-known period)"), and its citations are in either of two forms. Read back into events, the dates could not be put in order reliably ("early 2020s", "Q3"). To make a timeline a site can show, the report would have to store, beside its text, each event as data: a normalised date or date range (ISO 8601, with a precision), a one-line label, and the ids of the passages behind it, produced by a step that is checked like Ask's citations. With that stored, the export and the component are a day's work.

**E2, an index in pieces.** Clustering the vectors and fetching only the nearest clusters works for search by meaning, but keyword search, and the keyword fallback a page uses before the model loads, need every passage's text, which a pieces index does not download. The options are written below under decisions. Nothing was built.

**H6, share cards.** Drawing a card (title, a quoted line, the collection's name) as an image needs an imaging library with a font rasteriser. Pillow is installed in this venv but listed only in `requirements-ocr.txt`, not the base install, and the base image may lack a TrueType font. Open Graph images must be raster (PNG or JPEG); platforms do not accept SVG. Options are under decisions.

**H5, an audio overview.** No speech engine was added. The local options, as known in mid-2026 (check each licence and size before choosing):

| Engine | Licence | Size | Notes |
|---|---|---|---|
| Piper | The maintained fork (`piper1-gpl`) is GPL-3.0; the original `rhasspy/piper` was MIT and is archived | 20 to 60 MB a voice (ONNX), voices under their own licences | Fast on a CPU, many languages, the usual offline choice |
| Kokoro-82M | Apache-2.0 (model and code) | about 330 MB at fp32, about 90 MB quantised (ONNX) | Better voices than Piper, English first, runs on ONNX Runtime, which Clio already has |
| espeak-ng | GPL-3.0 | a few MB | Robotic, but tiny and in most Linux distributions |
| XTTS-v2 (Coqui) | Coqui Public Model Licence, non-commercial | about 1.8 GB | Voice cloning; the licence rules it out for most deployments |

The podcast-script report already writes two speakers' turns, and the report store already keeps audio files, so an engine would plug in at one place. Kokoro through ONNX Runtime is the smallest new dependency; Piper is the most proven offline.

## Decisions for the owner

Written up, none made. Added to `docs/ROADMAP.md` under "Decisions waiting for the owner".

1. **C2: must a person approve each answer before it is published?** Built with approval required (removing it is one line in `answers.judge`). (a) Keep it. (b) Approve automatically when the citation check passes. And **the label**: today a placeholder, "Written in advance, not for your question. Written on (date) from (sources)."
2. **C2: where answers are written.** Today over the API only. (a) A "Publish this answer" action on an Ask answer, which already has its citations. (b) A form in the Export dialog that searches for passages to cite. (c) From an FAQ report's pairs.
3. **B2: the reader page or the file's own address first** when a source has both? Built as the fallback. The reader page is always there and lands on the passage; a file's address may be a PDF that a phone opens badly.
4. **A2: additions in the data-only export.** Today every addition that needs no script is offered for both; those with a component need a whole site. (a) Keep it. (b) Whole site only, which is simpler to explain.
5. **D1: large tables.** (a) Leave out above 5,000 rows (today). (b) Ship a WebAssembly build of SQLite (about 1 MB) and read the table from the SQLite file in the browser, for any size.
6. **E2: keyword search in an index in pieces.** (a) A separate word index (term to passage ids, in pieces by term), so keyword search downloads only the pieces of its words. (b) Turn keyword search off in a pieces export: the page searches by meaning only, and says so until the model loads. (c) Always download the passage text whole, and split only the vectors, which halves the download at best.
7. **F3: Parquet** needs pyarrow (about 40 MB). Not added.
8. **H5: which speech engine, if any** (table above).
9. **H6: share cards.** (a) Make Pillow and one bundled font (DejaVu Sans, a permissive licence) part of the base install. (b) Offer share cards only where Pillow is installed. (c) Leave them out; a site can make its own.
10. **An edge bundle.** E2, built with option (a) or (b), would let a browser search collections of a few hundred thousand passages by downloading a tenth or less of the index, which removes most of the reason for a search function on Cloudflare Workers or similar. What it would not remove: a collection that must not be downloaded whole by every visitor (an edge function can return passages one query at a time), and a read-only MCP endpoint for agents, which a static site cannot be. If the owner wants either, an edge bundle is the first code Clio would ship to run on someone else's platform, and it should come after E2 and be measured against it.
11. **C5: storing timeline events** as data (above), and **H1: storing the study guide's answers**, so both can be published.

## What was left out, and what was not verified

- B4's Markdown, H2's EPUB and H3's slides follow their tools' documented formats and pass format checks here (YAML front matter parses, every XHTML chapter parses as XML, the EPUB's `mimetype` is first and stored), but none was built with MkDocs, Hugo, epubcheck, Marp or Reveal.js.
- F2's skill was not loaded in Claude Code.
- The answers threshold comes from a 110-query fixture in one domain.
- C3 has no page thumbnail; C6 is an index, with no glossary of definitions.
- `llms.txt`, and anything else written at a whole site's root, is replaced by a re-export but not removed when an addition is dropped.
- The real-model tests (C2's browser test, F3's script, the acceptance run) skip without `CLIO_TEST_STATIC_RUNTIME`; CI does not set it, so CI runs everything else.

## Tests

Last full runs, on this machine: backend `python -m pytest -n 6` 3,377 passed, 48 skipped, 4 failed; the four were two git-clone tests that fail only on Windows (`shutil.rmtree` on read-only git objects; they pass on Linux), one parser-deadline test that fails on timing under parallel load and passes on its own (both known from earlier passes), and the new related-passages memory test, which hit a MemoryError under six workers and was made lighter (it passes, alone and with the static tests). Frontend `npm test` 743 of 743. The static-export, report, skill and bundle tests together, with the real model: 180 passed.

New test files: `test_static_additions.py`, `test_static_browser.py`, `test_static_reader.py`, `test_static_related.py`, `test_static_answers.py`, `test_static_cite.py`, `test_report_file.py`, `test_static_terms.py`, `test_static_tables.py`, `test_static_llms.py`, `test_agent_skill.py`, `test_static_tools.py`, `test_static_changes.py`, `test_static_offline.py`, `test_static_references.py`, `test_report_cards.py`, `test_static_book.py`, `test_report_slides.py`, `test_static_transcripts.py`, `test_static_acceptance.py`, with `tests/browser_harness.py` and `tests/static_real.py`. The browser tests skip where no Chromium-based browser is installed.
