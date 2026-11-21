# Work order: more to publish from a collection

This file is a prompt. Hand it to a coding agent working in this repository, whole or one workstream at a time. It was written on 2026-11-21 from a read of the code at commit `8c257c3`.

## Your job

Clio can already publish a collection to a static website. The "For a website" export writes `manifest.json`, `passages.json` and `vectors.bin`, and a whole-site export adds a search page, the `<clio-search>` custom element, the embedding model and the library that runs it. A visitor's browser does the searching; there is no server. Read [docs/STATIC_SEARCH.md](../STATIC_SEARCH.md) before anything else: it is the contract this work extends.

The owner wants that export to carry more than a search box: pages to read, related passages, answers written in advance, tables, files for AI agents, and a few other formats. The items below are that list, in the order to build them. Each has an id, what is known about the code it touches, where to start, and a "done when" test. Do not add exports that are not listed.

Where things are today:

| Thing | Where |
|---|---|
| The export: gate, addresses, manifest, zip | `services/static_export.py` (992 lines), `scripts/export_static.py` |
| HTTP routes | `api/collections.py`, the four `/api/collections/{id}/export/static…` routes |
| The dialog | `frontend/src/components/ExportCollectionModal.vue` |
| The reference client and the search box | `docs/static-search/clio-static-search.js`, `docs/static-search/clio-search.js`, `docs/static-search/index.html` |
| Web server settings shipped with a site | `docs/static-search/hosting/` |
| Pinned browser runtime | `scripts/vendor_static_search.mjs`, copied into the image by the `Dockerfile` |
| Tests | `tests/test_static_export.py`, `frontend/src/components/__tests__/CollectionTransfer.test.js` |

## Evidence labels

Nothing behind this file was run. Every statement about the code comes from reading it, so confirm each one before you build on it. If it turns out wrong, say so in your report and adjust the item.

- **Checked**: read in the code by the author of this file.
- **Assumed**: inferred from a file name, a docstring or a doc, and not read. Verify first.

## Rules for all of this work

- Read `docs/STATIC_SEARCH.md`, `docs/ENGINEERING.md`, `docs/CLASSIFICATION.md` and `docs/ROADMAP.md` first. The roadmap's rules apply: every change leaves the default install working with nothing to configure, and everything must still work with model hubs, catalogues and CDNs blocked.
- **One gate.** Anything written for a public website goes through the checks the static export already makes: nothing marked local only, the collection labelled Public, the confirmation, held sources and sources with a stricter label left out, and an audit entry. No new file may mention a source the gate left out, by id, name, text or count. Do not write a second copy of the gate; call the one that exists.
- **Computed at export, never at visit.** A published page calls no server and no model provider. A whole-site export loads nothing from another origin.
- **Source text is hostile.** It was written by whoever wrote the documents. Every component and every generated page inserts it as text, never as markup, and generated pages carry a Content-Security-Policy that allows no inline script. Check how `clio-search.js` does this and do the same.
- **The format stays readable by what is already deployed.** A site that copied `clio-search.js` last month must keep working with a new export. Add new files as optional entries in the manifest, each with its checksum. Where an older client would misread an export, raise the format version so it refuses with a message, as the keyword export does (`KEYWORD_VERSION`).
- **Output is deterministic.** Stable ordering, one record per line where a person will read a `git diff`, no timestamps outside the manifest.
- **Say the size first.** The dialog states what each addition costs in bytes before the download, as it does for passages and vectors.
- **No new dependency without the owner.** No new Python package and no JavaScript build step. Components are single-file ES modules with no dependencies, like the two that exist. Where an item cannot be done without a dependency, stop on it and write up the options.
- Every item ships with tests. Backend: `python -m pytest` in the project's virtual environment. Frontend: `cd frontend && npm test`. For components, find how the reference client is tested today (**Assumed**: only through `tests/test_static_export.py`, which checks files, not behaviour) and add a browser test that serves an exported folder and uses the component; `scripts/screenshots.py` already has a Playwright setup.
- User-facing copy: plain words, sentence case, and the vocabulary table in `docs/ENGINEERING.md` (source, passage, report; never document, chunk, artifact).
- A setting stored with the collection (like the addresses) must survive a re-index and travel in a `.clio.zip` bundle, as the addresses do.
- New file types need lines in each file under `docs/static-search/hosting/`, or IIS will refuse to serve them.
- Update `docs/STATIC_SEARCH.md` as each item lands, including its Limits section.
- Small commits, one item per commit, the item id in the message, on the branch the session was given. Do not open a pull request unless asked.
- If an item needs a product decision, stop on that item, write the options in your report, and carry on with the next. The decisions already known are listed at the end; do not make them.

---

## Workstream A: foundations (do first)

**A1. Split the export module before it grows.** *Checked: one 992-line file.*
`services/static_export.py` holds the gate, the address rules, the model bundling, the passage and vector writers and the zip. Turn it into a package with those parts as modules, keeping every name that `api/collections.py`, `scripts/export_static.py` and the tests import. No behaviour change.
*Done when:* the existing tests pass unchanged and no module in the package is over about 300 lines.

**A2. A way to choose what an export carries.** *Checked: today the only choices are `site`, `quantization` and `passages_per_file`.*
Add a single `include` option (HTTP query, CLI flag, dialog checkboxes) naming the additions below. The preview reports, for each one, whether it is available, why not if not, and its size. The manifest lists what the export carries, each file with its checksum. The audit entry records which additions were included.
Start in the preview and `write_export`.
*Done when:* an export with nothing chosen is byte-for-byte what it is today apart from the manifest's date, and one test builds an export with everything chosen, from a collection holding a held source and a source with a stricter label, and asserts that neither source's id, name or text appears in any file.

**A3. Shared code for components.** *Checked: `clio-search.js` is built on `clio-static-search.js`.*
Move what every component will need (reading the manifest past the cache, fetching files by checksum, verifying them, resolving paths relative to the page, the custom properties and `::part` names) to where new components can import it without loading the search code. Add the browser test harness named in the rules.
*Done when:* `<clio-search>` works as before, and a second, trivial component can load the manifest in under 30 lines.

---

## Workstream B: a site to read

**B1. A page for each source.** *Checked: passages carry `document_id`, `page`, `text` and sometimes `lines`; a result with no address has no link.*
Add a reader: one HTML page per exported source, built from the passages, with an anchor on every passage (its id), page markers, the source's title, and a contents page listing the sources. Plain, accessible HTML that takes its look from a small stylesheet a site can replace. Before you start, find out whether passages overlap (`services/chunker.py`) and whether a page's whole extracted text is stored: a reader page must not repeat overlapped text. File names must be stable across exports and safe on every file system.
*Done when:* every exported source has a page, the pages validate, a source whose text contains `<script>` renders it as text, and two exports of an unchanged collection produce identical files.

**B2. Results fall back to the reader.**
Add the reader page as the last address a source can have, after the four in "Where results link". `passageLink` then always returns something when the reader is in the export, pointing at the passage's anchor. Whether a reader page should instead come before a file's own address is the owner's decision; build it as the fallback.
*Done when:* in an export with the reader, every result in `<clio-search>` is a link, and the dialog's "N of M sources link to a page" line still tells the truth about addresses on the web.

**B3. The site's own address, a sitemap and canonical links.**
A sitemap and a feed need absolute addresses. Add one setting beside the existing addresses: where the site will be served. With it set, write `sitemap.xml` and a canonical link on each reader page. Validate it with the same rules as the other addresses (`clean_public_url`).
*Done when:* the sitemap lists every reader page, and without the setting no sitemap is written and the dialog says why.

**B4. The same pages as Markdown.**
For a site built with a generator (Astro, Hugo, MkDocs, Eleventy), write one Markdown file per source with front matter (title, source id, link, page count) in place of the HTML. CLI and HTTP only; the dialog need not offer it.
*Done when:* the output drops into a fresh MkDocs or Hugo site and builds without edits.

---

## Workstream C: components over the index

**C1. `<clio-related>`: see also.** 
At export, compute each passage's nearest passages from other sources and write them to a small file (ids and scores only). The component shows the related passages for the page it sits on, matching the page's address against the manifest's links, or for a source named in an attribute. It must not download the model or `vectors.bin`. Work in blocks so a collection of 50,000 passages does not need the full similarity matrix in memory. Available only for an export searched by meaning.
*Done when:* on a reader page the box lists passages from other sources, the browser's network log shows neither the model nor the vectors, and the export of 50,000 passages finishes within the memory an install already needs to search them.

**C2. `<clio-answers>`: answers written in advance.** *Checked: reports include an FAQ (`services/artifacts.py`), and answers can be checked against their passages (`services/answer_check.py`, `check_citations` in `services/mcp_server.py`).*
Let the owner choose questions to publish with their answers. At export write each question, its answer, the passages it cites, and the question's vector. Leave out any answer that cites a source the gate left out, or whose citations do not pass the existing check. The component lists the questions; used with the search box, a query close enough to a published question shows that answer above the results, with its citations as links and a plain statement that it was written in advance, when, and from which sources. Set "close enough" from a measurement on a fixture of paraphrased and unrelated questions, and record the false-match rate; do not guess a threshold. Nothing is generated in the browser.
Two things are the owner's to decide and are listed at the end: whether a person must approve each answer before it can be published, and the wording of the label. Build the approval step; it can be removed more easily than added.
*Done when:* a paraphrase of a published question shows its answer, an unrelated query shows none, an answer whose citation fails the check is absent from the export, and the false-match rate is in the report.

**C3. `<clio-cite>`: a quotation that shows its source.**
`<clio-cite passage="…">` renders that passage as a quotation with its source's title, page and link, for use in an article. It reads the passage files already exported. A page thumbnail beside it is optional: previews are made on request and not stored (**Checked**: `services/page_preview.py`), so it would mean rendering them at export for named passages only.
*Done when:* a page with three citations fetches each passage file once, and an unknown id renders a visible "not found" rather than nothing.

**C4. A report as one file.** *Assumed: reports are stored as text with `[Source N]` markers and a record of the sources and content hashes they were built from; read `services/artifact_store.py` and `api/artifacts.py` to confirm, and see what download exists today.*
Export a saved report as a single HTML file: the text, each citation opening the passage it cites, and the list of sources with their hashes. No scripts from elsewhere, no requests at all when opened. This is not a public-website export, so work out from `docs/CLASSIFICATION.md` which rule applies to a report leaving the server as a file, and apply that one. If it is not clear, stop and write up the options.
*Done when:* the file opens from disk with the network off, every citation shows its passage, and a report on a local-only collection is handled as that document says.

**C5. `<clio-timeline>`.** *Assumed: the timeline report is prose written by a model, not structured events.*
A timeline is only useful on a site if each event has a date and the passages behind it. Find out what the report holds. If events can be read from it reliably, export them as data and render them in date order, each linking to its passage. If not, say what the report would need to store, and stop there.
*Done when:* either the component ships with every event linked, or the report says what is missing.

**C6. An index and a glossary.** *Checked: entities are extracted and stored in a normalised form that strips plurals (`normalize_entity` gives `analysi`), so the stored form cannot be shown to a reader.*
Write an A to Z index page for the reader: each term with the passages that mention it. It needs a display form for each term; find one or record one at export from the passage text. Keep the list short enough to be useful (the graph already filters by how common a term is). Turning terms into links inside reader pages is off unless asked for.
*Done when:* the index page lists terms as a person would write them, each entry links to passages that contain it, and no entry names a source the gate left out.

---

## Workstream D: tables

**D1. Spreadsheets in the export.** *Checked: `docs/STATIC_SEARCH.md` lists their absence as a limit; they are kept as typed SQLite tables (`services/structured_store.py`).*
Export each table of an exported source as data with its column names and types, up to a stated row limit, and a `<clio-table>` component that sorts, filters and pages through it and offers the rows as a CSV (through `services/csv_safety.py`). Add the tables to the reader page of their source. A table over the limit is left out and counted in the dialog. Reading large tables in the browser from a SQLite file is a dependency question for the owner.
*Done when:* the sample collection's checkout register can be sorted and filtered on a static page, a cell beginning with `=` downloads as text, and the Limits section no longer says spreadsheets are missing.

---

## Workstream E: size

**E1. Measure where the whole-index download stops being acceptable.** *Checked: every visitor downloads all passages and all vectors; the doc says "past a few tens of thousands of passages, use a server".*
Build exports of 10,000, 50,000 and 200,000 passages from generated text and record download size, time to first result on a throttled connection, and memory in the browser. Put the numbers in `docs/STATIC_SEARCH.md`.
*Done when:* the numbers are recorded. Do not start E2 without them.

**E2. An index in pieces.**
If E1 shows the need: cluster the vectors at export, write the cluster centres and one file of vectors and passages per cluster, and have the client fetch only the clusters nearest the query. Raise the format version so an older client refuses it. Measure results against the exhaustive search on the retrieval benchmark fixture and report recall at 10; below 0.95 is not shipped. Keyword search needs every passage's text, which this design does not download: work out what a pieces export does for keyword queries and for the keyword fallback, and write up the options before building.
*Done when:* a 200,000-passage export answers a query after downloading under a tenth of the index, with the recall figure in the report.

---

## Workstream F: for agents

**F1. `llms.txt`.**
Write `llms.txt` (the site's sources with a line each and a link) and `llms-full.txt` (the text), from the same content as B4. Needs the site address from B3 for absolute links; without it, write relative ones.
*Done when:* both files are in a whole-site export and list only exported sources.

**F2. A collection as an agent skill.** *Checked: the repository has skills of its own under `skills/`; read them for the house format.*
For a small collection, write a folder an agent client can load without MCP: a `SKILL.md` saying what the collection covers and how to look things up, with the sources as reference files. State a size limit and refuse above it with a message that points to MCP. This leaves the server as a file but is not a website: apply the rule the `.clio.zip` bundle follows, and the local-only refusal.
*Done when:* the folder loads as a skill in Claude Code and a question about the sample collection is answered from it with the source named.

**F3. Passages and vectors for other tools.**
An export of passages as JSON lines and vectors as a NumPy file, with the model, pooling and prefix stated, for someone loading the collection into DuckDB, LanceDB or pgvector. Parquet needs a package; check whether one is already installed, and if not, leave it out and say so.
*Done when:* a ten-line script in the docs loads the files and reproduces a search score to within the tolerance the doc states for the website export.

---

## Workstream G: changes, offline, references

**G1. What changed between exports.** *Checked: a passage's id comes from its source's content, page and position, so a reworded source gets new ids throughout; changes can be told per source, not per sentence.*
Given the previous export (the CLI already replaces one in `--out`; read its manifest before replacing it), write a changes page for the reader and an Atom feed: sources added, removed and replaced, using the superseded-by relation where it exists. The feed needs the site address from B3. For the in-app download there is no previous folder to read, so decide what the server must remember from the last export (ids and checksums only, never text) and say so in the doc.
*Done when:* exporting, changing one source and exporting again yields one feed entry naming that source.

**G2. A site that works offline.**
Add a web app manifest and a service worker to the whole-site export so a handbook can be installed on a phone and searched with no connection. The worker caches files by the checksums in the manifest. It must not break the rule that the manifest is read past every cache when there is a connection, and must never pair one export's manifest with another's files.
*Done when:* after one visit the site loads and searches with the network off, and after a new export is uploaded the next online visit shows the new content.

**G3. A reference list.** *Checked: the Library formats citations and exports BibTeX, RIS and CSL-JSON (`services/research/citations.py`).*
For a collection with papers in the Library, write a references page in the reader and put the standard scholarly `citation_*` meta tags on each paper's reader page. Offer the BibTeX file as a download from that page.
*Done when:* the references page lists each exported paper once, in the citation style the Library is set to.

---

## Workstream H: other formats

Each of these stands alone. Do them last, in this order, and stop on any that needs a dependency.

**H1. Study cards.** *Assumed: the study guide report is prose.* Export question-and-answer pairs as a tab-separated file Anki imports, and as JSON for a quiz. If the report does not hold pairs, say what it would need to. No LMS packages.

**H2. Print and EPUB.** A print stylesheet for the reader, one page holding the whole collection for printing to PDF from a browser, and an EPUB built with the standard library (it is a zip of XHTML).

**H3. Slides.** The briefing report as Markdown that Marp or Reveal.js reads.

**H4. Transcripts that play.** *Checked: transcription keeps each segment's start and end (`services/audio_transcriber.py`); the indexed text carries `[mm:ss]` prefixes and is split into pages of about four minutes (`services/document_extractor.py`).* *Assumed: the segments themselves are not stored after indexing.* Export a WebVTT file for each audio source, and a `<clio-transcript>` component that shows the transcript beside a player and seeks when a passage or a search result is chosen. If the segments are not stored, the times in the text are the fallback.

**H5. An audio overview.** *Checked: a podcast script is already a report type, and the report store expects audio files; nothing in the repository turns text into speech.* Do not add a speech engine. Write up the local options, their size and licences, for the owner.

**H6. Share cards.** An image for each reader page (title, a quoted line, the collection's name) referenced by Open Graph tags. Pillow is already used for page previews; confirm before relying on it.

---

## Decisions for the owner (write up options, do not choose)

- **C2: must a person approve each answer before it is published**, and what does the label on a published answer say?
- **B2: reader page or the file's own address first**, when a source has both?
- **A2: are the additions offered for the data-only export**, or only for a whole site?
- **D1: large tables.** Left out above a row limit, or read in the browser from a SQLite file, which means shipping a WebAssembly build of SQLite with the site.
- **E2: keyword search in an index in pieces.** A separate word index, keyword search turned off, or the passage text always downloaded whole.
- **F3: Parquet**, if it needs a new package.
- **H5: which speech engine**, if any.
- **An edge bundle.** Search, or a read-only MCP endpoint, as a function for Cloudflare Workers or similar, for a collection too large for the browser. It would be the first code Clio ships to run on someone else's platform. Not in this work order; write one paragraph on whether E2 removes the need.

---

## Acceptance

Export the sample collection as a whole site with everything chosen. Serve the folder with a plain static server, with every other host blocked. Record the result of each step.

1. The contents page lists four sources. Each opens as a page.
2. Search from a reader page. Every result is a link, and it lands on the passage.
3. A reader page shows related passages from other sources. The network log has no model and no vectors.
4. Ask a paraphrase of a published question. Its answer appears above the results with working citations. An unrelated query shows no answer.
5. The checkout register sorts by date and filters by name. Its CSV opens in a spreadsheet with no formula run.
6. Mark one source as held and export again. Its name, text and id are in no file, and no count gives it away.
7. Put a source containing `<img src=x onerror=alert(1)>` in the collection and export. Nothing runs on any page.
8. Load the site, go offline, reload, search. It works.
9. Change one source, export over the old folder, and read the feed. One entry.
10. Point a site that still has last release's `clio-search.js` at the new export. It searches as before.
11. `llms.txt` is at the root and lists the four sources.

## What to report back

For each item: done, skipped (with what you found), or stopped for a decision. For every *Assumed* statement, what was actually true. The sizes each addition adds to the sample export and to a 5,000-passage export. The E1 numbers. The C2 false-match rate and the threshold chosen. Then update `docs/ROADMAP.md` and `docs/STATIC_SEARCH.md` with what shipped.
