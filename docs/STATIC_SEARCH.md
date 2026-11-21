# Search on a static website

A collection can be exported as files that a static website searches by meaning, with no Clio and no server behind it. The page downloads the files, embeds the visitor's query in the browser, and ranks the passages itself. A collection whose model has no browser build is exported too, and searched by keyword ([below](#the-same-model-on-both-sides)).

The export is meant to become part of a website you already run: a search box, `<clio-search>`, drops into any of its pages and takes on their look, and each result links to the page its source is on, on that site or anywhere else ([On your website](#on-your-website)).

It can also be exported as a **whole site**, ready to serve: the three files, a search page, and the embedding model and library the page runs, so the site loads nothing from Hugging Face or a CDN. Use that on a network that blocks either, or for a site whose visitors' browsers should contact no one else ([below](#a-site-that-carries-its-own-model)).

Use it for a small, public body of text: a handbook, a course catalogue, a set of published guidance. For anything private, or anything large, share the collection or use the [App API](APP_API.md) instead.

## What you get

| File | Holds |
|---|---|
| `manifest.json` | The embedding model and how it pools, the vector size and quantization, the passage count, the export date, a checksum for each file, and the list of sources: each with its name, a readable `title` (the Library's, where it is sure of it) and, when it has one, a `link` to where it lives on the web ([Where results link](#where-results-link)). |
| `passages.json` | One passage per line: `id`, `document_id`, `page`, `text`, and `lines` (first and last) for a passage from a file that has line numbers. Above 2,000 passages it is split into `passages-0001.json`, `passages-0002.json`, and so on; the manifest lists them in order. |
| `vectors.bin` | One vector per passage, in the same order as the passages, as signed 8-bit integers with no header. A browser reads it straight into an `Int8Array`. Absent from an export searched by keyword. |

A passage's score for a query is `dot(row, query) / manifest.vectors.scale`, where `query` is the query's unit-length vector. That is cosine similarity, the score Clio's own semantic search reports, to within about 0.003.

Sizes: 384 bytes of vectors per passage with either supported model, plus the passage text and about 90 bytes of JSON around it. A collection of 5,000 passages of 800 characters comes to roughly 2 MB of vectors and 4.5 MB of text, before the web server compresses it. The Export dialog states both figures before you download.

## Exporting

**In the app.** Use the **Export** button beside the collection, then the **For a website** tab. It names the model the site will have to use (or says why the site will search by keyword), says how many sources are left out and why, and gives the sizes. **Where results link** sets where each result points ([below](#where-results-link)). **Include the model** is ticked whenever this server can include it, and gives `<name>.static-site.zip`, a whole site; untick it for `<name>.static-search.zip`, the data files alone. Tick the confirmation and download.

**From the command line**, which suits a site kept in a repository:

```bash
python scripts/export_static.py "Staff handbook" --check
python scripts/export_static.py "Staff handbook" --out ../site/public/search --confirm-public
python scripts/export_static.py "Staff handbook" --out ../site/public --site --confirm-public
python scripts/export_static.py "Staff handbook" --out ../site/public/search --files-url /files/handbook/ --confirm-public
```

Run it with the server's environment (`DATA_DIR` and the rest of `.env`). It only reads, needs no network, and replaces a previous export in `--out` while leaving the folder's other files alone. If the export is refused, last time's files stay as they were. `--float32` writes full-precision vectors, four times the size, for anyone who wants scores identical to Clio's. `--site` writes the whole site into `--out`, with the export in `search/`. `--files-url` sets where the site keeps its copies of the files for that export only; without it the address saved in Clio is used.

**Over HTTP.** `GET /api/collections/{id}/export/static/preview` returns the counts, sizes, model and any `blockers`. `GET /api/collections/{id}/export/static?confirm_public=true` returns the zip (`quantization=int8|float32`, `passages_per_file`, and `site=true` for the whole site, which the preview's `site.available` says this server can make). `GET` and `PUT /api/collections/{id}/export/static/addresses` read and save where results link and where the site will be served (`files_base_url`, `site_url`, and each source's own address). All of these are for the collection's owner, and saving addresses is written to the audit trail as `collection.static_addresses`.

### Choosing what else it carries

Besides the search files, an export can carry additions: pages to read, related passages, and the others listed under [What else an export can carry](#what-else-an-export-can-carry). Nothing is added unless you choose it, and an export with nothing chosen is exactly the search files.

- **In the app**, tick them under **Also include**. Each says its size before you download, or why it cannot be made for this collection. One marked "Needs the site ready to serve" uses the site's scripts, so it is offered only with **Include the model** (or **As a site ready to serve**) ticked.
- **From the command line**, `--include reader,related`, or `--include all` for every one this export can carry. `--check` lists them with their sizes.
- **Over HTTP**, `include=reader,related` (or `all`) on the download; the preview's `additions` lists each one with `available`, `reason`, `bytes` and `site_only`.

Every addition reads the same sources the export does, after the same checks ([What is allowed out](#what-is-allowed-out)): no file mentions a source that was left out, by id, name, text or count. The manifest lists what an export carries under `additions`, each with its files and their checksums, and the audit entry records which were included. A client that predates an addition ignores it, so a site that copied `clio-search.js` from an earlier export still searches a new one. Replacing an export from the command line removes the previous export's additions, as listed in its manifest, and leaves the folder's other files alone.

### Reviewing a new export

Passages are ordered by source name, then page, then position, and each sits on its own line. A passage's `id` is Clio's chunk id, built from the source's id, the page and the position, and a source's id comes from the file's content. So an unchanged source produces the same lines every time, and `git diff` on the passage files shows what is new, gone or reworded since the last export. `manifest.json` changes on every export because it carries the date; its `sha256` values tell you whether anything else did.

## What is allowed out

Everything in the export is published: the text of every passage goes to every visitor, and the vectors give away the gist of the text even without it. The export is therefore the strictest outside service Clio has, and it is refused unless all of these hold.

- **Nothing in the collection is marked local only.** A collection that is marked, or that holds one marked source, is refused whole, as it is for every other outside service ([CLASSIFICATION.md](CLASSIFICATION.md)). The refusal is a 403 and is written to the audit trail.
- **The collection is labelled Public.** New collections are Internal. Setting the sensitivity to Public in the collection's settings is the act that says it has been cleared.
- **You confirm it**, with the tick box or `--confirm-public`, each time.

Within a collection that passes, sources that are held for review are left out, and so are sources that carry a stricter sensitivity label of their own. The dialog counts both; the published manifest does not mention them.

Each export is recorded in the audit trail as `collection.static_export`, with who made it, the counts, the model and the checksum of the vectors, never the text.

The export does not judge the content. Read the passage diff before you commit it.

## What else an export can carry

| Addition | `include` | Writes | Needs |
|---|---|---|---|
| A page for each source | `reader` | `read/index.html` (the contents), `read/<name>.html` for each source, `read/reader.css`, and `sitemap.xml` when the site's address is set | nothing |
| Related passages from other sources | `related` | `related/<name>.json` for each source, and `<clio-related>` on the reader's pages | a whole site, searched by meaning |
| Answers written in advance | `answers` | `answers.json`; the search box shows one above its results, `<clio-answers>` lists them | answers written and approved |
| An index of terms | `terms` | `read/terms.html`, linked from the contents page | the pages to read (included with it) |
| Spreadsheets as tables | `tables` | `tables/<name>.json` and `tables/<name>.csv` for each table; `<clio-table>` on the reader's pages | a spreadsheet among the sources |
| What changed since the last export | `changes` | `read/changes.html`, and the feed `changes.xml` when the site's address is set | the pages to read (included with it) |
| Files for AI agents | `llms` | `llms.txt` and `llms-full.txt`, at a whole site's root | the Markdown pages (included with it) |
| Passages and vectors for other tools | `tools` | `data/passages.jsonl`, `data/vectors.npy`, `data/embedding.json` | nothing |
| The pages as Markdown | `markdown` | `markdown/_index.md` and `markdown/<name>.md` for each source | command line and HTTP only |

### Related passages

`related` works out, when you export, which passages of other sources are closest in meaning to each source, and `<clio-related>` shows them on a page: "see also", each linking to its passage. For every passage it finds the four nearest passages in other sources, from the vectors the export already has; a source then gets the six best of those, no more than two from any one other source, each with its id, source, page, score and the first 240 characters of its text, in `related/<name>.json`. The box reads the manifest and that one file: it never downloads the model, the vectors or the passage files.

```html
<script type="module" src="/assets/handbook/clio-related.js"></script>
<clio-related src="/assets/handbook/search/"></clio-related>
```

Without a `source` attribute (a source's id) it shows the source whose page it is on: the reader's page, or the source's own address. On a page that is neither, or with an export that has no related passages, it hides itself. It takes the shared `--clio-*` properties, has parts `label`, `status`, `results`, `result`, `title`, `meta` and `snippet`, and sends `clio-related-ready` (`{ source, count }`). With the reader in the same export, each source's page carries the box.

It needs an export searched by meaning, and a whole site, since the component ships with the site's scripts. The similarities are worked out a block of 64 passages at a time, never as one table of every pair: at 50,000 passages the work needs 66 MB on top of the vectors (77 MB, which the server holds anyway to search them) and took about 75 seconds on a 2018 laptop.

### Answers written in advance

`answers` publishes questions with answers written beforehand, each citing the passages it rests on. Nothing is generated in the browser: a visitor whose query is close enough in meaning to a published question sees its answer above the search results, with its citations as links and a line saying it was written in advance, when, and from which sources. `<clio-answers>` lists them all.

An answer is written over HTTP (`POST /api/collections/{id}/export/static/answers` with `question`, `answer` and `citations`, a list of passage ids; the answer cites them as `[Source 1]`, `[Source 2]`, ... in that order) and waits for approval. The dialog lists every answer under **Answers written in advance**, says whether each would be published and why not, and approves or removes it (`POST .../answers/{answer}/approval`, `DELETE .../answers/{answer}`). Every change is in the audit trail as `collection.static_answers`. An answer is published only when:

- someone has approved it;
- every passage it cites is still in the collection, and in a source this export carries: one citing a held source, or one with a stricter label, is left out, so no answer mentions a source the gate left out;
- it passes the check Ask's answers get ([services/answer_check.py](../services/answer_check.py)): the figures, identifiers and quotes in each sentence that cites a passage are in that passage. The check does not judge meaning; read the answers before you approve them.

`answers.json` carries each question, its answer, the date it was approved, its sources' titles, its citations (passage id, source, page and the start of the passage's text) and the question's vector, embedded by Clio's own model as a query. The answers travel in a bundle and come back unapproved.

**How close is close enough** was measured, not guessed. `scripts/answer_threshold.py` embeds the questions in [tests/fixtures/static_answers.json](../tests/fixtures/static_answers.json): 20 published questions, two paraphrases of each, and 50 queries that should match none (25 off-topic, 25 on the same topics but not published, such as "Can I take unpaid leave?" beside "How many days of annual leave do staff get?"). The threshold is the lowest score with no false match and no wrong answer on the fixture, plus 0.02:

| Model | Threshold | Paraphrases answered | Wrong answer | False matches (50 unrelated) |
|---|---|---|---|---|
| `all-MiniLM-L6-v2` | 0.58 | 55% | 0% | 0% |
| `BAAI/bge-small-en-v1.5` | 0.77 | 40% | 0% | 0% |

So the threshold is strict: about half of the paraphrases find their answer, and the rest get the ordinary results. Lower it and more paraphrases match, at a cost the script prints (MiniLM at 0.50: 80% answered, 2% a wrong answer, 2% false matches). The closest unrelated query was "How do I book a desk for tomorrow?" against "How do I book a meeting room?". The threshold is in `answers.json`; a site that wants another can change it there. A model with no measured threshold, or an export searched by keyword, lists its answers but never shows one above the results.

The label on a published answer ("Written in advance, not for your question.") is a placeholder until the owner decides its wording.

### Quoting a passage

`<clio-cite>` quotes one passage of an export in an article, with its source's title, its page and a link to it (the same link a search result has). It needs nothing beyond the export: every whole site carries `clio-cite.js`.

```html
<script type="module" src="/assets/handbook/clio-cite.js"></script>
<clio-cite src="/assets/handbook/search/" passage="5f0c…_3_0" words="40"></clio-cite>
```

`passage` is the passage's id, as in `passages.json` or a reader page's anchor (`#p-…` works too); `words` quotes only the start. The passage files are read once for the page, however many quotations it has, and an id the export does not hold shows "Passage … is not in this export" rather than nothing. Parts: `quote`, `caption`, `title`, `meta`, `status`. A page thumbnail beside the quotation is not offered: Clio renders previews of a page on request and does not keep them, so it would mean rendering every quoted page at export.

### An index of terms

`terms` adds an A to Z index to the reader: each term with links to the passages that mention it, on their sources' pages. The terms are the names and acronyms Clio picks out of passages for its own search (capitalised phrases such as "Regional Analysis Unit", and acronyms), kept when they appear in at least two passages and in no more than 30% of them, as Clio's search does, and at most the 400 most widely used. Clio stores them only in a normalised form (lower case, plurals stripped: "analysis" is kept as "analysi"), so the export reads the exported passages again and shows each term the way it is written most often. Only exported passages are read: no term, link or count comes from a source that was left out. Terms are not turned into links inside the pages. A term in more than twelve passages links to the first twelve and says how many more.

### Spreadsheets

Clio keeps a spreadsheet as typed tables as well as passages, and `tables` exports each table of an exported source: `tables/<name>.json` (its columns and their types, then one row per line) and `tables/<name>.csv` for a spreadsheet program. Every CSV cell a spreadsheet would read as a formula (one starting `=`, `+`, `-`, `@`, a tab or a carriage return) starts with a `'`, so it opens as text ([services/csv_safety.py](../services/csv_safety.py)). A cell is shown as the source wrote it where Clio kept that text (dates and amounts, which sort by their value), and a true/false column as yes and no. A table of more than 5,000 rows is left out and counted in the dialog.

`<clio-table>` shows one: sort by any column (again to reverse), filter by words in any cell, page through 25 rows at a time, and download its CSV. With the reader, a spreadsheet's page carries its tables.

```html
<script type="module" src="/assets/handbook/clio-table.js"></script>
<clio-table src="/assets/handbook/search/" table="tables/equipment-register.json"></clio-table>
```

`table` is a file the manifest lists under `additions.tables`, or a source's id for its first table. Parts: `label`, `filter`, `status`, `table`, `header`, `cell`, `pages`, `download`.

### What changed

`changes` adds a page to the reader, and with the site's address an Atom feed (`changes.xml`), saying which sources were added, changed, replaced or removed since the last export, newest first. A passage's id comes from its source's content, page and position, so a reworded source has new ids throughout: changes are told per source, not per sentence. A source whose file name stayed but whose content changed is "changed"; one marked in Clio as replaced by another exported source is "replaced by" it; new and gone sources are "added" and "removed". Re-exporting an unchanged collection adds nothing and leaves the files as they were.

The comparison is with the export being replaced when there is one (the command line reads the old `manifest.json` in `--out` first), and otherwise with what the server remembers of the collection's last export: each source's id, file name and title, and the change history, never any text. The history (the last 50 changes) travels in the manifest, so the next export carries it on. A source that is still in the collection but left out of this export, held or labelled more strictly, is not named, not even in an earlier entry.

### Files for AI agents

`llms` writes `llms.txt`, the index the [llms.txt convention](https://llmstxt.org) describes: the collection's name and description, then each exported source with one line (the first sentence of its text) and a link to its Markdown page. `llms-full.txt` is the text of every source, one after the other. Both are made from the Markdown pages, which come with them, so they hold what the export holds and nothing it left out. In a whole site they sit at the site's root, where agents look; the manifest lists them as `../llms.txt`. With the site's address set the links are absolute, otherwise relative to the file. Re-exporting from the command line replaces them; a site that stops including them keeps the old files at its root until you delete them.

### Markdown for a site generator

`markdown` writes the same pages as Markdown, for a site built with Astro, Hugo, MkDocs or Eleventy: copy `markdown/` into the site's content folder (`content/` for Hugo, `docs/` for MkDocs). Each file has YAML front matter, `title`, `source_id`, `source` (the file name), `link` (where the source lives on the web, when it has an address) and `pages`, then the text with `## Page 3` before each page of a source with several. `_index.md` lists the sources; Hugo reads it as the section's page, MkDocs as one more page. It is offered on the command line (`--include markdown`) and over HTTP, not in the dialog.

Markdown renders HTML, links and, in Hugo, shortcodes, and the text was written by whoever wrote the sources. So everything that could start one is escaped: `<`, `>`, `&`, `{` and `}` as entities, and the punctuation Markdown reads as syntax with a backslash. The text reads the same and can only be text. Passages have no anchors here; link to the HTML reader for that.

### Pages to read

`reader` writes a plain page for each source, built from its passages, and a contents page listing them. Each passage on a page has an anchor, `#p-` and its id (characters other than letters, digits, `-` and `_` become `_`), and a source with several pages has a marker before each, `#page-3`. A page's name comes from its source's file name (`Leave Policy (2024).pdf` gives `read/leave-policy-2024.html`): lower-case letters, digits and hyphens, the same in every export while the file keeps its name, with the start of the source's id added when two sources share a name. The manifest gives each source's page as `reader` on its entry.

Clio does not keep a page's whole text, only its passages, and the passages on a page overlap: each starts with up to the last 200 or so characters of the one before (the collection's chunk overlap, or a short last paragraph). The reader drops what each passage repeats, matching no more than the chunker can have carried over, so nothing is read twice. The text is as Clio extracted it: a PDF's layout, a table's cells and a page's images are not there.

**The site's own address.** A sitemap and canonical links need whole addresses, so they are written only when the collection says where the export will be served: **Where the site will be served** under **Where results link** (`site_url` over HTTP), an `https://` address checked like the others. For a whole site it is the address of the folder holding `index.html`; for the files alone, of the folder holding `manifest.json`. With it, the reader writes `sitemap.xml` beside the manifest, listing the contents page and every source's page, and each page carries `<link rel="canonical">`. Without it there is neither, and the dialog says why. The setting is kept with the collection, through a re-index, and travels in a bundle with the base address for files; an imported one is checked again and dropped if it is not a web address.

The pages carry no script of their own and a Content-Security-Policy that allows none inline; source text is always written as text. Their look is `read/reader.css`: replace it to give them your site's. In a whole site each page also carries the search box. The pages are deterministic: an unchanged collection gives the same files.

## How far it scales

Every visitor downloads every passage and every vector. `scripts/static_scale_bench.py` measures where that stops being acceptable: it writes exports in the real format from generated text (800 characters a passage, 384-dimension vectors at 8 bits), opens each in headless Chromium with every other host blocked and the connection throttled to 20 Mbit/s with 40 ms of latency, and times the first result. The static server it uses does not compress, so the times are the worst case; the gzipped sizes say what a server that compresses sends. Measured 2026-11-20 on a 2018 laptop (i7-8750H):

| Passages | Download | Gzipped | First keyword result | First result by meaning | Ranking alone | Browser heap |
|---|---|---|---|---|---|---|
| 10,000 | 12.5 MB (8.7 text, 3.8 vectors) | 4.7 MB | 6.0 s | 5.8 s | 13 ms | 24 MB |
| 50,000 | 62.6 MB (43.3 text, 19.2 vectors) | 23.4 MB | 30.6 s | 29.4 s | 107 ms | 87 MB |
| 200,000 | 250.6 MB (173.2 text, 76.8 vectors) | 92.9 MB | 114 s | 109 s | 376 ms | 316 MB |

"By meaning" here is given its query vector directly; the model is a separate, one-time download (above). The generated text uses a small vocabulary and compresses about six times; real prose compresses about three times, so a real collection's gzipped text is about twice the figure shown. Vectors barely compress (8-bit numbers look random).

The ranking is never the problem: a plain loop over 200,000 vectors takes under half a second. The download is. Up to about 10,000 passages the whole index is a few megabytes compressed and arrives in seconds. At 50,000 a first visit waits half a minute on a good connection and the page holds nearly 90 MB; at 200,000 it is two minutes and a third of a gigabyte, which a phone will not manage. So past about 10,000 passages, split the collection, use a server, or wait for an index in pieces (the owner's decision on keyword search is open: docs/ROADMAP.md).

## Exports that are not a website

These leave the server as files for one person or one tool, not as a public site, so the website's gate does not apply; each follows the rule named below.

### A collection as an agent skill

`GET /api/collections/{id}/export/skill` gives `<name>-collection.skill.zip`: one folder an agent client such as Claude Code loads as a skill without MCP (unpack it into `.claude/skills/`). `SKILL.md` says what the collection covers and how to look things up (search `references/`, read the page, answer only from the files and name the source and page) and lists the sources with what each opens with; `references/<source>.md` holds each source's text, page by page, overlaps removed. It is for a small collection: above 300 sources or 1,000,000 characters of text it is refused with a message pointing to MCP, which searches a collection rather than reading all of it.

It follows the bundle's rules: owner only, an administrator for a restricted collection, held sources left out. And since an agent's model is not this server's to choose, it counts as an outside service ([CLASSIFICATION.md](CLASSIFICATION.md)): a collection that holds anything marked local only is refused whole, with the same 403 and audit entry as MCP. Each download is in the audit trail as `collection.skill_export`.

### Passages and vectors for other tools

`include=tools` adds, beside the search files, `data/passages.jsonl` (one passage per line: its row, id, source, page, text, the source's title and address), `data/vectors.npy` (the passages' unit vectors as float32, row n for line n) and `data/embedding.json` (the model, its name in fastembed, pooling, normalisation, the query prefix): what DuckDB, LanceDB or pgvector need to load the collection with no Clio code. The vectors are full precision, so a score from them is Clio's own cosine, to within 0.001. An export searched by keyword gets the passages alone. Parquet is not offered: writing it needs a package (pyarrow) Clio does not have.

From the export folder, with fastembed installed and the model on disk:

```python
import json, numpy as np
from fastembed import TextEmbedding
emb = json.load(open("data/embedding.json"))
rows = [json.loads(line) for line in open("data/passages.jsonl", encoding="utf-8")]
vectors = np.load("data/vectors.npy")
q = next(TextEmbedding(emb["fastembed_model"]).embed([emb["query_prefix"] + "When must shared equipment be returned?"]))
scores = vectors @ (q / np.linalg.norm(q))
for i in np.argsort(-scores)[:5]:
    print(f"{scores[i]:.3f}", rows[i]["title"], rows[i]["page"], rows[i]["text"][:80])
```

## The same model on both sides

A query vector is only comparable with passage vectors from the same model, pooled and normalised the same way, with the same query prefix. The browser has to run the model itself, so the export is offered only for a collection embedded **on this machine** with a model that has a browser build whose vectors were compared with Clio's:

| Collection's model | Browser build | Pooling | Query prefix |
|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | `Xenova/bge-small-en-v1.5` | CLS | `Represent this sentence for searching relevant passages: ` |
| `all-MiniLM-L6-v2` | `Xenova/all-MiniLM-L6-v2` | mean | none |

Both give 384 dimensions. Measured with transformers.js 4.3.2 against Clio's fastembed backend: at `fp32` and `fp16` the browser's query vector agrees with Clio's to a cosine of 0.99999 or better. The `q8` builds, a third of the download, agree only to 0.991 to 0.995, which moved scores by up to 0.02 and reordered close results. Use `fp32` (about 130 MB for bge-small, 90 MB for MiniLM) or `fp16` (half that).

A collection embedded with any other model, or through Ollama or a hosted embedding service, is exported for **keyword search**: the passages and the manifest, no `vectors.bin`, format version 2 (which an older client refuses with a message rather than misreads), and `embedding.reason` saying why. The Export dialog says the same before you download. To search such a collection by meaning on a site, clone it or re-index it with one of the two models above and export the copy. Clio does not re-embed during export: that would publish vectors nobody has searched against.

The manifest also carries a **probe**: one fixed sentence and the vector Clio's model gives it. A page embeds the sentence itself and compares. If the result disagrees, the page has the wrong model, the wrong pooling, a missing prefix or a quantised build, and it must not search by meaning. The reference client does this check and falls back to keyword matching. When the embedding model could not be loaded at export time the probe is `null`, and a page has only the model's name to go on.

## On your website

### The search box

A whole-site export carries `clio-search.js`, a search box for any page of your site. Upload the export anywhere on the site, say `/assets/handbook/`, and add two lines where the box should be:

```html
<script type="module" src="/assets/handbook/clio-search.js"></script>
<clio-search src="/assets/handbook/search/" label="Search the handbook"></clio-search>
```

It works from any folder of the site: paths are read relative to the page, as a link's would be. It searches by keyword as soon as the passages arrive (or, with `index="focus"`, fetches them only when someone first uses the box), and loads the model the first time someone uses the box, so a page that only shows it costs a visitor nothing more. It is a standard custom element in its own shadow DOM: the site's styles do not reach inside it, and its own do not leak out.

| Attribute | Default | Does |
|---|---|---|
| `src` | `search/` beside `clio-search.js` | The export folder. Several, separated by spaces, are searched as one box. |
| `runtime` | beside `clio-search.js` | The folder holding `vendor/` and `models/`, so several exports can share one copy. |
| `model` | `auto` | `auto` uses the model the export carries, else the CDN; `bundled` never the CDN; `cdn`; `off` searches by keyword and downloads no model. |
| `load` | `focus` | `now` fetches the model with the page instead of on first use. |
| `index` | `now` | `focus` fetches the passages and vectors only when someone first uses the box, for a page that is mostly read (the reader's pages use it). |
| `results` | `8` | How many results to show. |
| `label`, `placeholder` | | The box's label and placeholder. |
| `target` | | `_blank` opens results in a new tab (with `rel="noopener"`). |

It uses the page's own font and colours. Every Clio component reads the same shared properties, so a site can set them once: `--clio-accent`, `--clio-muted`, `--clio-border`, `--clio-background`, `--clio-radius`, `--clio-mark`, `--clio-font`, `--clio-color`. To set the search box alone, use its own, which win over the shared ones: `--clio-search-accent` (links and focus), `--clio-search-muted`, `--clio-search-border`, `--clio-search-background`, `--clio-search-radius`, `--clio-search-mark` (highlighted words), `--clio-search-font`, `--clio-search-color`; or style its parts with `::part()`: `label`, `input`, `status`, `answer` (a published answer above the results), `results`, `result`, `title`, `meta`, `snippet`. The status line is a polite live region, so a screen reader hears how many passages are searched, how, and when nothing matches. It sends `clio-search-ready` (`{ semantic, reason }`) and `clio-search-results` (`{ query, hits }`) events, which bubble out of the shadow DOM.

### Where results link

A result's title links to its source when the export gave the source a web address, and points inside it: a PDF opens at the passage's page (`#page=`), a web page scrolls to the passage's opening words and highlights them (a text fragment, `#:~:text=`, which browsers ignore when the page's text differs), and a file from a repository opens at its lines. A source's address is, in order:

1. **its own address**, set in **Where results link**: any `https://` address with no user name or password in it, or a path on the site starting with `/`;
2. **the page it came from**, for a source added by link;
3. **the file at the commit last synced**, for a file synced from a repository on GitHub, GitLab (gitlab.com, or a host whose name starts `gitlab.`), Bitbucket, Codeberg, Gitea or Forgejo; credentials in the clone's remote are never published, and other hosts get no address of their own;
4. **the base address for files** plus the file's name, for files the site keeps copies of (`/files/handbook/` for `/files/handbook/leave-policy.pdf`). Pasted text and agents' notes have no file and get no address this way.

5. **its page in the export's reader**, when the export carries one (`include=reader`), at the passage's anchor. This is a page of the export, not an address on the web, so the dialog's count of sources that link to a page does not include it.

A source with none is shown without a link. The addresses are kept with the collection, carried through a re-index and in a bundle (an imported one keeps only addresses a browser may open), and checked again when exported, so a stored address that is not a web address is never published.

### Several collections on one site

The `vendor/` and `models/` folders are the same in every export made with the same model. Upload them once, and point every box at them with `runtime`; they may be on another origin, an assets host or a CDN, if it sends `Access-Control-Allow-Origin`. To search several collections in one box, list their folders in `src`. When all of them are searched by meaning with the same model, the query is embedded once and the scores are compared directly; otherwise the box searches all of them by keyword, since a keyword score and a similarity cannot be compared.

### Updating, and caches

Export again and replace the whole folder. The box reads `manifest.json` past every cache on each visit, then asks for the other files by their checksum (`passages.json?v=…`), so a browser cache or a CDN cannot pair this year's manifest with last year's passages. Where the page can compute checksums (any `https://` site, or `localhost`), each file is also checked against the manifest after download, once more with the cache bypassed; if the site really holds files from two exports, the box says so and turns itself off rather than show wrong results.

### Web server settings

A whole site carries settings for the common servers at its root; each reads only its own:

| File | For | Sets |
|---|---|---|
| `web.config` | IIS | the types of `.bin`, `.onnx`, `.wasm`, `.json` and `.mjs`, and of the files additions write (`.csv`, `.tsv`, `.md`, `.vtt`, `.webmanifest`, `.epub`, `.bib`, `.npy`, `.jsonl`), which IIS otherwise refuses to serve (a 404.3); no caching for the manifest |
| `.htaccess` | Apache | the same types, caching, compression |
| `_headers` | Netlify, Cloudflare Pages | caching and the `.wasm` type; read only at the site's root, so for a folder inside a bigger site, copy its lines into the site's own file with the folder's path in front |

Each has the `Access-Control-Allow-Origin` line for serving to other origins, turned off. `README.txt` beside them says all of this for whoever puts the folder on the site, including nginx.

## The reference client

[static-search/clio-static-search.js](static-search/clio-static-search.js) is a JavaScript module with no dependencies beyond [clio-common.js](static-search/clio-common.js) beside it, and [static-search/index.html](static-search/index.html) is a page that uses it. Copy the three files beside a `search/` folder holding the export and serve the folder with any static web server. (Before this release the client was one file; a page that copies the new `clio-static-search.js` needs `clio-common.js` beside it too.)

```js
import { createSearch, transformersEmbedder } from './clio-static-search.js'
import { pipeline } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2'

const search = await createSearch('./search/', {
  embedder: (manifest) => transformersEmbedder(manifest, { pipeline }),
})
if (!search.semantic) console.warn(search.reason)   // keyword matching only
const hits = await search('how much holiday do staff get', { k: 5 })
// [{ score, passage: { id, document_id, page, text }, document: { id, name, pages } }]
```

It loads the files, checks the model against the probe, embeds the query with the manifest's prefix and pooling, and ranks every passage. Ranking is a plain loop: 5,000 passages take about 3 ms. `passageLink(hit)` gives the address of a hit's passage ([Where results link](#where-results-link)); `search.upgrade(embedder)` switches a search that started with keywords to search by meaning; `combineSearches([...])` searches several exports as one. `loadIndex`, `checkModel`, `rank` and `keywordRank` are exported for a site that wants to assemble these differently. The search box is built on exactly these.

### Writing a component

`clio-common.js` holds what every component needs and nothing of the search: `loadManifest(folder)` reads the manifest past every cache and refuses a format it cannot read; `loadListed(manifest, file)` fetches a file the manifest lists by its checksum and checks it; `folderOf(element, 'src', 'search/', import.meta.url)` resolves an attribute the way a link on the page would be; `addition(manifest, id)` is an addition's manifest entry, or null; `adoptStyles`, `BASE_STYLE`, `el` and `emit` give a shadow root the shared look, build elements from text only, and send events out of the shadow DOM. A component that shows how many passages an export holds is twenty lines ([tests/browser/clio-count.js](../tests/browser/clio-count.js)).

Two rules every component follows. Source text is set as text, never as markup: it was written by whoever wrote the sources. And styles go in through `adoptStyles`, a constructed stylesheet, so a page whose Content-Security-Policy allows no inline script or style can still use the component.

The components are tested in a real browser: `tests/browser_harness.py` drives an installed Edge, Chrome or Chromium headless over the DevTools protocol, with every host but `127.0.0.1` blocked, against an exported folder served by a plain static server. The tests skip where no such browser is installed; `CLIO_TEST_BROWSER` names one.

### A site that carries its own model

The example page, given only the three files, fetches transformers.js from jsDelivr and the model from the Hugging Face hub. An export made as a whole site carries both instead, and the same page loads them from beside itself:

```
index.html                 the reference page
clio-static-search.js      the reference client
clio-common.js             what it and every component share
clio-search.js             the search box
search/                    manifest.json, passages.json, vectors.bin
vendor/                    transformers.min.js, its WebAssembly runtime, their licences, runtime.json
models/<model>/            config.json, tokenizer files, onnx/model.onnx, SOURCE.txt
```

The model under `models/` is the ONNX file Clio itself embeds with, the copy in the image or the data directory, so a site exported from a server that never reached the hub works on a network that cannot reach it either. Because the file is the same, the page's probe vector matches Clio's exactly (cosine 1.0000 in Chromium for `all-MiniLM-L6-v2`, served with nothing but `localhost` reachable). The manifest names it under `embedding.browser.bundled`, with its source repository, licence and checksum.

What it adds, before the web server compresses it: about 26 MB of runtime, plus the model: 86 MB for `all-MiniLM-L6-v2`, or 63 MB for `BAAI/bge-small-en-v1.5` (fastembed's 8-bit build, which is what Clio embeds with). Every visitor downloads the model once; browsers cache it after that.

The runtime is transformers.js 4.3.2 and the ONNX Runtime WebAssembly build it was released with, pinned with their checksums in `scripts/vendor_static_search.mjs`. The published images carry them in `/opt/clio/static-search/vendor`. A source install has no copy until you run the script once, with the npm registry reachable: `node scripts/vendor_static_search.mjs "$DATA_DIR/static-search"`. Until then the dialog says why the model cannot be included.

### Hosting the model yourself

To assemble the same thing by hand, for a page of your own: serve transformers.js and the model from the site, and point the library at them before loading the model. The reference client's `localTransformers()` does exactly this.

```js
import { pipeline, env } from './vendor/transformers.min.js'
env.allowRemoteModels = false
env.allowLocalModels = true               // off by default in a browser
env.localModelPath = new URL('./models/', location.href).pathname   // a path, not a URL: see below
env.backends.onnx.wasm.wasmPaths = {      // name both files: see below
  mjs: new URL('./vendor/ort-wasm-simd-threaded.asyncify.js', location.href).href,
  wasm: new URL('./vendor/ort-wasm-simd-threaded.asyncify.wasm', location.href).href,
}
```

Three traps, all found the hard way with 4.3.2. Without `allowLocalModels` the library refuses to load anything. Given a full `http(s)://` URL as `localModelPath`, it skips its check that a local file exists, decides the model has no tokenizer, and every query fails with `this.tokenizer is not a function` while the model itself loads fine; a path on the site (`/handbook/models/`) works. And the WebAssembly loader ships as `ort-wasm-simd-threaded.asyncify.mjs` (from the `onnxruntime-web` package, not transformers.js's own `dist`): many web servers, older nginx and Python's on Windows among them, send `.mjs` as `text/plain`, and the browser will not run it. Renaming it to `.js` and naming both files in `wasmPaths`, as above, avoids depending on the server.

These settings are transformers.js's own and have changed between its major versions, so check them against the version you ship. The probe tells you whether the result is right: `search.semantic` is true only when the page's model reproduces Clio's vector.

## Limits

- Search is by meaning only, with a simple keyword fallback. Clio's hybrid ranking, reranker, entity boosts and table search stay in Clio.
- Every visitor downloads the whole index and the model. Past about 10,000 passages a first visit waits many seconds, and past 50,000 it is too slow for most visitors ([How far it scales](#how-far-it-scales)): split the collection or use a server.
- A spreadsheet goes out as a table (`include=tables`) only up to 5,000 rows; a bigger one is left out, and the dialog counts it. Reading a large table in the browser from the SQLite file itself would mean shipping a WebAssembly build of SQLite with the site, which is the owner's decision.
- A result links only where its source has an address; the export cannot know where a file is published unless someone says so.
- The Markdown pages follow the conventions MkDocs and Hugo document, but have not been built with either here: neither was installed where this was written. Links in `_index.md` are to the `.md` files, which MkDocs resolves and Hugo does not without a link render hook.
- A published answer is shown only for a query close to its question by a fixed threshold, measured on a small fixture; it recognises about half of fair paraphrases. It is not a search over the answers' text.
- A reader page is the text Clio extracted, in passage order: no layout, images or tables, and a page boundary only where the source had one.
- A source marked as replaced by a newer version is exported like any other. Remove it from the collection first if the site should not show it.
