# Research: papers and books

**Research** (in the ribbon, or `G` then `R`; under *More* on a phone) turns a collection of papers,
books and reports into a library a lab can work from. It was built around
requests from lab researchers:

| Request | What Research does |
|---|---|
| "Is this worth my time? Does it actually contain what I need?" | **Worth my time?** on one source, or **Screen** the library against a question. Each source gets a verdict (*worth reading*, *partly relevant*, *not about this*, *can't tell from the text*) backed by quotes from it, plus the passages where it discusses the question. |
| "Rename downloads to something descriptive" | **Rename to descriptive names** builds `2021 - Okafor et al - Root Hydraulics Predict Drought Survival.pdf` from the source's own title, authors and year. Preview first, choose a pattern, undo per source. |
| "Download everything it cites and links to" | A source's **References** lists every entry of its reference lists (a book's per-chapter lists and bibliography included) and every link in its text. **Download…** fetches open copies (arXiv, open-access PDFs) into the collection; references you already have are marked *In your library*. |
| "A visual map of my library and its abstracts" | **Citation map** lays out who cites whom, plus the works several of your sources cite that you don't have yet. Click a card to read its abstract. |
| "Prepare a review while I sleep" | **Reviews** schedules brief or thorough reviews for many sources ("start at 01:00"), runs them on the server, and leaves an unread queue to skim in the morning. |

## Your selection is the scope

The checkboxes in the Sources panel and the ones in the Research list are the
**same selection** that Ask and Find use. When sources are selected, every
action here works on them only: analyzing, screening, renaming, reviews and
the citation map. A bar at the top says how many are selected, with *Clear
selection* to go back to the whole collection. Spreadsheets and code files in
a selection are ignored here.

## Papers and books

Each source is read as an **article** or a **book** (one that prints an ISBN
or runs to 80 pages or more). For a book:

- the title comes from the first page that has one (after a blank cover or a
  half-title), the authors from a *by …* line under it;
- the year is the latest on the copyright page — the edition in hand;
- the ISBN (checksum-verified) is looked up in **Open Library** for the
  canonical title, authors, year and publisher, used only if that title is
  printed in the front matter;
- with no abstract, the opening of the **Preface**, **Foreword** or
  **Introduction** is shown instead, quoted, and labelled *Opening — there is
  no abstract*. It is never presented as an abstract;
- references are read from every chapter's list and the bibliography (running
  heads and the index are not mistaken for entries);
- a thorough review of a long book reads parts spread evenly from beginning to
  end and says how much it read.

A Markdown source takes its title from its first heading.

## Why you can trust what it shows

The worry researchers raise first is fabrication: a summary that sounds right
but says something the source never said. Research is built so that cannot
pass silently.

**Metadata is never written by a model.** Title, authors, year, DOI, ISBN,
abstract and references are read from the source's own text by fixed rules,
or come from a catalogue (OpenAlex, Crossref, arXiv, Open Library) looked up
by an identifier **printed in the source**. A catalogue record is used only
if its title is printed where the source's own title is (page one of an
article, the front matter of a book); a title search for a reference is
accepted only if the found title is printed in that reference. Anything the
rules cannot find stays empty, and *Where these details came from* names the
origin and page of every field.

**Every statement a model makes must quote the source.** Verdicts and reviews
are produced from the source's own numbered passages. The model must attach
to each statement an exact quote and the passage it came from. Clio then
checks each quote against the passage text before keeping the statement:

- extraction noise (hyphenation, ligatures, curly quotes, a dropped letter) is
  tolerated;
- a changed number (`23%` quoted as `32%` or `2.3%`) or a dropped or added
  negation (*did not recover* quoted as *did recover*) is not;
- a quote from a passage the model was not shown counts as made up.

Statements that fail are removed and counted ("2 statements were left out
because their quote was not found in the source"). A positive verdict with no
surviving quote becomes *can't tell from the text*. Each kept statement links
to its page.

**The map draws only what references support.** An edge needs the same DOI,
the same arXiv id, or the cited source's full title printed in the reference,
and says which.

**Reviews say how much they read**: how many passages and which pages.

What this does not guarantee: a quote can be real and still be taken out of
context. The quotes and page links are there so you can check in seconds.

## The citation map

Modelled on Obsidian's graph view: each source is a dot, sized by how many
citations connect it, and the layout is a live force simulation rather than a
fixed diagram. An arrow-free line means one cites the other (turn *Arrows* on
to see which way); a dashed line leads to a cited work the collection lacks.

- **Hover** a dot to light its citations and name its neighbours; everything
  else fades. **Click** to read its abstract in a card; **double-click** to
  open the source. Drag dots to rearrange; scroll to zoom. Titles fade in as
  you zoom (*Text fade threshold*).
- **Local graph** (from the card) shows one source and what is within one,
  two or three citations of it — the depth slider in the bar at the top.
- The ribbon's *Citation graph* icon opens Research straight on the graph.
- **Graph settings** (the button at the top right), in four sections that
  collapse like Obsidian's:
  - *Filters* — search by title words, an author's surname or a year
    (`okafor 2021`; Enter jumps to the first match); *Cited works*, and how
    many of your sources must cite one before it is drawn; *Orphans*, the
    sources that cite nothing here and are cited by nothing here.
  - *Groups* — colour by type (papers, books, sources outside the selection,
    works not in the collection), by publication year, or not at all.
  - *Display* — arrows, text fade threshold, node size, link thickness.
  - *Forces* — center force, repel force, link force, link distance.
  Settings are remembered in your browser; *restore defaults* resets them.
- With a selection, a source of the collection outside it appears as *in the
  collection, not selected*; a cited work with an open copy can be downloaded
  from its card.

## Without a model, or offline

- Analysis, renaming, references, links and the citation map need **no
  model**.
- *Worth my time?* without a model shows where the source discusses your
  question (Clio's own retrieval inside it), with no verdict.
- `OFFLINE_MODE` or `RESEARCH_LOOKUP_ENABLED=false` turns off catalogue
  lookups; details then come from the source's text alone. Downloading uses
  the same link fetcher as *Add link* and is off when it is
  (`LINK_INDEXING_ENABLED=false`, offline mode).

Catalogue lookups send identifiers (DOIs, arXiv ids, ISBNs) and cited titles
to `api.openalex.org`, `api.crossref.org`, `export.arxiv.org` and
`openlibrary.org`; they never send passages. The readiness check lists them as
an outbound path.

## Using it

1. Add PDFs to a collection as usual (or download them from a source's
   references). Select some in the Sources panel to work on just those.
2. Open **Research** and press **Analyze**. This reads each source's record
   and reference lists in the background. **Read again** re-reads sources
   analyzed by an earlier version.
3. Type what you are looking for and press **Screen** to sort by relevance,
   or open a source and ask *Worth my time?*.
4. **Rename to descriptive names** when the file names are unhelpful. Fields:
   `{year}`, `{authors}` (*Okafor et al*, *Hale & Ruiz*), `{first_author}`,
   `{title}`, `{venue}`, `{doi}`. Titles printed in capitals are written in
   title case.
5. In **Reviews**, choose sources, depth and an optional question, and start
   now or at a time. Close the tab; the queue will be waiting.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `RESEARCH_LOOKUP_ENABLED` | `true` | Consult OpenAlex, Crossref, arXiv and Open Library by printed identifier |
| `RESEARCH_CONTACT_EMAIL` | empty | Sent to OpenAlex/Crossref as they request (faster, politer service) |
| `RESEARCH_LOOKUP_TIMEOUT_SECONDS` | `15` | Per catalogue request |
| `RESEARCH_MAX_JOB_DOCUMENTS` | `200` | Sources one background job may cover |
| `RESEARCH_MAX_CONCURRENT_JOBS` | `1` | Review/screening jobs running at once; the rest wait |

## Permissions and limits

- Reading the library, the map and reviews needs read access. Analyzing,
  renaming, downloading and starting jobs need edit access; downloading also
  needs the acceptable-use acknowledgement every way of adding sources needs.
  A read-only user can still ask *Worth my time?*; the result is not saved.
- Research records live in the collection's own index database, so collection
  export/import and backups carry them.
- Analysis and reviews run as background jobs in the server process. A
  restart stops a running job (finished sources keep their results) and it is
  reported as stopped. A job can be scheduled up to 48 hours ahead.
- Model usage from screening and reviews counts toward the daily token budget
  (`CHAT_DAILY_TOKEN_BUDGET`); a job that reaches it stops and says so.
- The rules that read titles, authors and references are heuristics tuned for
  common journal, preprint and book layouts. Unusual layouts can yield an
  empty or partial record, never an invented one. You can always type a name.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/research/papers` | Library with records, counts and suggested names |
| GET | `/api/research/papers/{id}` | One source: provenance, references, links, cited-by, verdicts, reviews |
| POST | `/api/research/papers/{id}/analyze` | Analyze one source now |
| POST | `/api/research/analyze` | Analyze many in the background (`document_ids`, `only_new`) |
| POST | `/api/research/papers/{id}/rename` · `/restore-name` | Rename (suggested or typed name) / undo |
| POST | `/api/research/rename` | Rename many; `dry_run` previews |
| POST | `/api/research/references/resolve` | Look for open copies (catalogues) |
| POST | `/api/research/references/download` | Fetch references and links into the collection |
| GET / POST | `/api/research/graph` | Citation graph (`include_external`, `min_citations`; POST takes `document_ids`) |
| POST | `/api/research/papers/{id}/assess` | Worth my time? (model optional) |
| GET | `/api/research/assessments` | Saved verdicts for a question, ranked |
| POST | `/api/research/jobs` | Background screening (`assess`) or reviews (`brief`), optional `not_before` |
| GET/POST | `/api/research/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel` | Job progress and cancellation |
| GET/POST/DELETE | `/api/research/briefs`, `/briefs/{id}/read`, `/briefs/{id}` | The review queue |

All take `collection_id` as a query parameter. Model-backed calls take the
same provider headers as `/api/chat`.
