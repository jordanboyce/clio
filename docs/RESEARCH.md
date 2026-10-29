# Papers: a research workspace

**Papers** (under *More*, or `G` then `P`) turns a collection of PDFs into a
library a lab can work from. It was built around four requests from lab
researchers:

| Request | What Papers does |
|---|---|
| "Is this paper worth my time? Does it actually contain what I need?" | **Worth my time?** on a paper, or **Screen** the whole library against a question. Each paper gets a verdict (*worth reading*, *partly relevant*, *not about this*, *can't tell from the text*) backed by quotes from the paper, plus the passages where the paper discusses the question. |
| "Rename downloads to something descriptive" | **Rename to descriptive names** builds `2021 - Okafor et al - Root Hydraulics Predict Drought Survival.pdf` from the paper's own title, authors and year. Preview first, choose a pattern, undo per paper. |
| "Download everything a paper cites and links to" | A paper's **References** lists every entry of its reference list and every link in its text. **Download…** fetches open copies (arXiv, open-access PDFs) into the collection; references you already have are marked *In your library*. |
| "A visual map of my papers and their abstracts" | **Citation map** draws an arrow from each paper to the papers it cites, plus the works several of your papers cite that you don't have yet. Click a node to read its abstract. |
| "Prepare a review while I sleep" | **Reviews** schedules brief or thorough reviews for many papers ("start at 01:00"), runs them on the server, and leaves an unread queue to skim in the morning. |

## Why you can trust what it shows

The worry researchers raise first is fabrication: a summary that sounds right
but says something the paper never said. Papers is built so that cannot pass
silently.

**Metadata is never written by a model.** Title, authors, year, DOI, abstract
and references are read from the paper's own text by fixed rules (largest type
on page one, the text under the *Abstract* heading, the entries under
*References*), or come from a scholarly registry (OpenAlex, Crossref, arXiv)
looked up by an identifier **printed in the paper**. A registry record is used
only if its title is printed on the paper's first page; a title search for a
reference is accepted only if the found title is printed in that reference.
Anything the rules cannot find stays empty, and *Where these details came
from* names the source and page of every field.

**Every statement a model makes must quote the paper.** Verdicts and reviews
are produced from the paper's own numbered passages. The model must attach to
each statement an exact quote and the passage it came from. Clio then checks
each quote against the passage text before keeping the statement:

- extraction noise (hyphenation, ligatures, curly quotes, a dropped letter) is
  tolerated;
- a changed number (`23%` quoted as `32%` or `2.3%`) or a dropped or added
  negation (*did not recover* quoted as *did recover*) is not;
- a quote from a passage the model was not shown counts as made up.

Statements that fail are removed and counted ("2 statements were left out
because their quote was not found in the paper"). A positive verdict with no
surviving quote becomes *can't tell from the text*. Each kept statement links
to its page.

**The map draws only what references support.** An edge needs the same DOI,
the same arXiv id, or the cited paper's full title printed in the reference,
and says which.

**Reviews say how much they read.** A brief review reads the opening
passages plus what Clio's own search finds for methods, results, limitations
and conclusions; a thorough review reads the whole paper part by part. Each
review states how many passages and which pages it covered.

What this does not guarantee: a quote can be real and still be taken out of
context. The quotes and page links are there so you can check in seconds.

## Without a model, or offline

- Analysis, renaming, references, links and the citation map need **no
  model**.
- *Worth my time?* without a model shows where the paper discusses your
  question (Clio's own retrieval inside that paper), with no verdict.
- `OFFLINE_MODE` or `RESEARCH_LOOKUP_ENABLED=false` turns off registry
  lookups; details then come from the paper's text alone. Downloading uses the
  same link fetcher as *Add link* and is off when it is
  (`LINK_INDEXING_ENABLED=false`, offline mode).

Registry lookups send identifiers (DOIs, arXiv ids) and cited titles to
`api.openalex.org`, `api.crossref.org` and `export.arxiv.org`; they never send
passages. The readiness check lists them as an outbound path.

## Using it

1. Add PDFs to a collection as usual (or download them from a paper's
   references).
2. Open **Papers** and press **Analyze**. This reads each paper's record and
   reference list in the background.
3. Type what you are looking for and press **Screen** to sort the library by
   relevance, or open a paper and ask *Worth my time?*.
4. **Rename to descriptive names** when the file names are unhelpful. Fields:
   `{year}`, `{authors}` (*Okafor et al*, *Hale & Ruiz*), `{first_author}`,
   `{title}`, `{venue}`, `{doi}`.
5. In **Reviews**, choose papers, depth and an optional question, and start
   now or at a time. Close the tab; the queue will be waiting.

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `RESEARCH_LOOKUP_ENABLED` | `true` | Consult OpenAlex, Crossref and arXiv by printed identifier |
| `RESEARCH_CONTACT_EMAIL` | empty | Sent to OpenAlex/Crossref as they request (faster, politer service) |
| `RESEARCH_LOOKUP_TIMEOUT_SECONDS` | `15` | Per registry request |
| `RESEARCH_MAX_JOB_DOCUMENTS` | `200` | Papers one background job may cover |
| `RESEARCH_MAX_CONCURRENT_JOBS` | `1` | Review/screening jobs running at once; the rest wait |

## Permissions and limits

- Reading the library, the map and reviews needs read access. Analyzing,
  renaming, downloading and starting jobs need edit access; downloading also
  needs the acceptable-use acknowledgement every way of adding sources needs.
  A read-only user can still ask *Worth my time?*; the result is not saved.
- Research records live in the collection's own index database, so collection
  export/import and backups carry them.
- Background jobs run in the server process. A restart stops a running job
  (finished papers keep their reviews) and it is reported as stopped. A job
  can be scheduled up to 48 hours ahead.
- Model usage from screening and reviews counts toward the daily token budget
  (`CHAT_DAILY_TOKEN_BUDGET`); a job that reaches it stops and says so.
- The rules that read titles, authors and references are heuristics tuned
  for common journal and preprint layouts. Unusual layouts can yield an empty
  or partial record, never an invented one. You can always type a name.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/research/papers` | Library with records, counts and suggested names |
| GET | `/api/research/papers/{id}` | One paper: provenance, references, links, cited-by, verdicts, reviews |
| POST | `/api/research/papers/{id}/analyze` | Analyze one paper now |
| POST | `/api/research/analyze` | Analyze many in the background |
| POST | `/api/research/papers/{id}/rename` · `/restore-name` | Rename (suggested or typed name) / undo |
| POST | `/api/research/rename` | Rename many; `dry_run` previews |
| POST | `/api/research/references/resolve` | Look for open copies (registry) |
| POST | `/api/research/references/download` | Fetch references and links into the collection |
| GET | `/api/research/graph` | Citation graph (`include_external`, `min_citations`) |
| POST | `/api/research/papers/{id}/assess` | Worth my time? (model optional) |
| GET | `/api/research/assessments` | Saved verdicts for a question, ranked |
| POST | `/api/research/jobs` | Background screening (`assess`) or reviews (`brief`), optional `not_before` |
| GET/POST | `/api/research/jobs`, `/jobs/{id}`, `/jobs/{id}/cancel` | Job progress and cancellation |
| GET/POST/DELETE | `/api/research/briefs`, `/briefs/{id}/read`, `/briefs/{id}` | The review queue |

All take `collection_id` as a query parameter. Model-backed calls take the
same provider headers as `/api/chat`.
