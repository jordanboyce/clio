# Research metadata benchmark

How often is a paper's record in the Research tab right? `scripts/research_bench.py`
answers that for title, authors, year, arXiv id, DOI and ISBN, so that changes to the
record extraction (roadmap R2-R4) can be judged by numbers instead of anecdotes.

Each document goes through the app's own path: it is indexed into a throwaway
collection with `DocumentIndexer.index_document` (what an upload does), and its record
is read with `services.research.library.analyze_document` (what the Analyze button
calls). The analysis runs twice on the same indexed files:

- **offline**: catalogue lookups are on, as shipped, but every request to arXiv,
  OpenAlex, Crossref or Open Library is refused at once, and so is any other socket to
  a non-loopback address. This is a site where the catalogues are blocked.
- **online**: the catalogues are reached for real (`--lookups live`, the default).
  Requests are spaced per host (3 s for arXiv, 1 s elsewhere) and carry a generic
  User-Agent, `clio-research-bench/1.0 (document metadata accuracy benchmark)`, with no
  contact address. Every response is recorded in the cache, and `--lookups replay`
  repeats that online pass later with the network blocked.

Indexing always runs with the network blocked and needs no embedding model. OCR follows
the normal settings (`ENABLE_OCR`, off by default), so on a host without OCR an
image-only scan cannot be read, which is what a user sees there too.

## Running it

From the repository root, with the project's venv:

```
venv/Scripts/python scripts/research_bench.py --download      # first time: fetches about 80 MB
venv/Scripts/python scripts/research_bench.py                 # offline + online, cached files only
venv/Scripts/python scripts/research_bench.py --lookups replay   # online pass from the recording
venv/Scripts/python scripts/research_bench.py --lookups off      # offline pass only
venv/Scripts/python scripts/research_bench.py --set smoke --lookups off   # committed files, no download
venv/Scripts/python scripts/research_bench.py --json out.json --out out.md
```

Files are cached in `data/research_bench/` (gitignored; `--cache DIR` or
`CLIO_RESEARCH_BENCH_CACHE` to move it): `files/<id>.pdf`, and the recorded catalogue
answers in `lookups-<set>-v<version>.json`. A download is checked against the
manifest's sha256; a file that does not match is kept as `<id>.mismatch` and left out.
Without `--download` the runner uses whatever is cached and says what is missing; with
nothing cached it runs the smoke set.

Under pytest the label and matching checks always run; the benchmark itself is skipped
unless asked for:

```
CLIO_RESEARCH_BENCH=1 venv/Scripts/python -m pytest tests/benchmark/test_research_bench.py -s     # smoke set
CLIO_RESEARCH_BENCH=full venv/Scripts/python -m pytest tests/benchmark/test_research_bench.py -s  # cached manifest
```

## The documents

`manifest.json` lists 59 real documents: 32 arXiv preprints (one- and two-column, four
with old-style ids such as `hep-th/9711200`), 8 two-column ACL Anthology papers
(including the conference versions of four of the preprints), 6 open-access journal
articles (PLOS, Scientific Data, JOSS), 6 US government reports (NIST and NASA; one
has no identifier at all), 5 books (two with ISBNs), a 1953 NACA report that is a scan
with an OCR text layer, and an image-only scan with no text layer, which the runner
derives from a NASA report by rendering its first three pages to images.

The set includes the shapes the 2026-11 live check found: the HyDE paper whose four
authors came back as one name (`2212.10496`, and its ACL version), the RAG survey whose
title came back as run-together body text (`2312.10997`), and the other arXiv papers of
that check. arXiv files are indexed as `<arXiv id>.pdf`, as they were in the live check.

Nothing in the manifest is committed. Each entry records its source URL, sha256, size
and licence. Most arXiv papers carry arXiv's non-exclusive distribution licence, which
does not allow redistribution, so the runner downloads every file from its source on
request. The ACL papers are CC BY 4.0 (CC BY-NC-SA 3.0 before 2016), the journals CC BY,
the NIST and NASA reports are US government works, and the books are CC BY, CC BY-NC,
CC BY-NC-SA or GNU FDL as noted per entry.

`smoke/` holds eight small PDFs that are committed. Everything in them is invented (CC0),
written by `make_smoke_pdfs.py`: a clean article, a two-column paper, text whose words
are separated by kerning rather than spaces, a quotation above the title, a title that
is a picture, an image-only scan, a book with an ISBN, and a report with no
identifiers. They let the benchmark run with no download and no network, and their
numbers say nothing about real accuracy.

## Labels

`labels` describe **the file in hand**, checked against its own pages on 2026-11-16:

- `title`: as printed on the title page (subtitle included). For arXiv papers this is
  the arXiv listing's title unless the PDF prints something else.
- `authors`: the people the document credits, in order. An empty list means the
  document credits no person (a corporate report).
- `year`: the year the version in hand was first made public: arXiv v1 for a preprint,
  the conference or issue year for a published paper, the edition's copyright year for
  a book, the publication date for a report.
- `arxiv_id`: only when the file is the arXiv version. `doi`: the file's own DOI, or for
  an arXiv preprint the published version's DOI when arXiv lists one. `isbn`: one the
  book prints.

`accept` lists other values a careful reader would also accept for this file: a venue
year printed on the page ("Published as a conference paper at ICLR 2015"), the published
version's DOI or arXiv's own DataCite DOI for a preprint, a catalogue's variant title, the
eBook ISBN beside the print one, a catalogue's author for a report that prints none.

## Matching rules

- **Title**: both sides are NFKD-normalised (ligatures split), accents dropped,
  casefolded, and every run of non-alphanumeric characters becomes one space. The record
  is right when the result equals the label's (or an accepted title's). Word boundaries
  count, so run-together text is wrong. The report also shows how many titles are within
  0.9 `difflib` similarity, which separates near misses (lost spaces, one dropped word)
  from wrong lines.
- **Authors**: each name is reduced to a family name (the part before a comma in
  "Family, Given", otherwise the last word, skipping Jr./Sr./II-IV), normalised as above.
  The score is the F1 of the two multisets of family names, taking the best over the
  label and any accepted list. No authors expected and none given scores 1; none expected
  and some given scores 0. The report gives the mean F1 and the share of whole lists
  that are right (F1 = 1).
- **Year**: exact match with the label or an accepted year.
- **Identifiers**: arXiv ids without `arXiv:` or a version suffix; DOIs lowercased
  without a `https://doi.org/` or `doi:` prefix; ISBNs as ISBN-13 digits (an ISBN-10 is
  converted). For a document that has the identifier the report counts it **found**
  (right value) or wrong. For one that does not, a recorded value is **invented** unless
  it is an accepted one.
- A document that cannot be indexed or analyzed counts as a miss on every field it has.

The failure patterns (for example "title: a sentence of body or abstract text",
"authors: several names run together as one") are a rough classification of each miss,
meant to point at the next fix, not part of the score.

## Comparing numbers

Numbers compare **only within one manifest version** (`version` in `manifest.json`), and
only with every file cached (the report says when some are missing). Change an entry, a
label or a file and bump the version; say so where new numbers are quoted. Online
numbers also depend on what the catalogues answer that day and on rate limits; compare
online numbers across code changes with `--lookups replay` on one recording.

To add a document: put its URL, sha256 (`sha256sum` of the file), licence, kind and
labels in `manifest.json`, check every label against the file's own pages, and bump the
version.

## Baseline (manifest v1, 2026-11-16)

Recorded on the dev machine (Windows 11, 12 CPUs, OCR off) before any R2 work. Online
numbers are from a live run whose answers are in the recording; OpenAlex answered 12 of
its 26 requests with 429 (rate limited), so several title searches never happened.

| field | offline | online |
|---|---|---|
| title (exact after normalising) | 38/59 (64%) | 53/59 (90%) |
| title within 0.9 similarity | 41/59 (69%) | 53/59 (90%) |
| authors, mean F1 | 0.36 | 0.80 |
| authors, whole list right | 10/59 (17%) | 44/59 (75%) |
| year | 48/59 (81%) | 48/59 (81%) |
| arXiv id found (of 32) | 32/32 | 32/32 |
| DOI found (of 22) | 10/22, 1 wrong | 15/22, 1 wrong |
| ISBN found (of 2) | 2/2 | 2/2 |
| identifiers invented | none | none |

A whole run took about 10.5 minutes: indexing 217 s, the offline analysis 200 s and the
online analysis 210 s (about 3.5 s a document; the online pass includes 26 s of waiting
between arXiv requests). The offline pass refuses its 144 catalogue requests at once; at a
site where they are silently dropped, each would wait out the 15 s lookup timeout instead.
