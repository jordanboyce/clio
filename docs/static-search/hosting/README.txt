Search for a website, exported from Clio
========================================

This folder is a search of one collection that runs entirely in the
visitor's browser: no server, no database, no account. Serve it with any
static web server, as it is, or put the search box into your own pages.

What is here
------------
  index.html              a page with the search box, ready to serve
  clio-search.js          the search box, <clio-search>, for your own pages
  clio-static-search.js   the search library the box uses
  clio-common.js          what the box and the other components share
  clio-related.js, clio-answers.js, clio-cite.js, clio-table.js
                          components for related passages, answers
                          written in advance, quotations and tables
                          (docs/STATIC_SEARCH.md in Clio says how)
  search/                 the collection: manifest.json, the passages, and
                          vectors.bin when it is searched by meaning
  vendor/, models/        the model and the library that runs it (when the
                          export includes the model); nothing is fetched
                          from Hugging Face or a CDN
  _headers, .htaccess, web.config
                          settings for Netlify/Cloudflare Pages, Apache and
                          IIS; each server reads only its own

Try it
------
  python -m http.server 8000 --bind 127.0.0.1
  then open http://127.0.0.1:8000/

Opening index.html straight from the disk does not work: browsers do not let
a page read files that way.

Put the search box in your own pages
------------------------------------
Upload the folder anywhere on the site, say /assets/handbook/, and add:

  <script type="module" src="/assets/handbook/clio-search.js"></script>
  <clio-search src="/assets/handbook/search/" label="Search the handbook"></clio-search>

The box uses the page's own font and colours. To match a design, set any of
these on it in your stylesheet:

  clio-search {
    --clio-search-accent: #0b5cad;      links and the focus ring
    --clio-search-muted: #5f6b7a;       status line and page numbers
    --clio-search-border: #c3cad3;      the box's border
    --clio-search-background: #fff;     the box's background
    --clio-search-radius: 6px;
    --clio-search-mark: #fff3b0;        highlighted words
    --clio-search-font: inherit;
  }

or style its parts: clio-search::part(input), ::part(title), ::part(snippet),
::part(status), ::part(result), ::part(meta), ::part(label).

Other attributes: results="8" (how many), placeholder="...", target="_blank"
(open results in a new tab), model="off" (keywords only, nothing to
download), load="now" (fetch the model with the page instead of when someone
first uses the box). It sends clio-search-ready and clio-search-results
events.

Where results link
------------------
Each result links to its source when the export gave the source a web
address: a PDF opens at the page, a web page scrolls to the passage, a file
in a public repository opens at its lines. Set the addresses in Clio before
exporting (Export, For a website, "Where results link"): one base address for
the files you publish on the site, and any source's own address. A source
with no address is shown without a link.

Several collections on one site
-------------------------------
Each export's vendor/ and models/ are the same for the same model, so upload
them once and point every box at them:

  <clio-search src="/assets/handbook/search/" runtime="/assets/clio-runtime/"></clio-search>

where /assets/clio-runtime/ holds vendor/ and models/. To search several
collections in one box, list them: src="/assets/a/search/ /assets/b/search/".

Updating
--------
Export again and replace the whole folder. The box reads manifest.json fresh
on every visit and asks for the other files by their checksum, so a cache or
CDN cannot mix this year's manifest with last year's passages; if the site
ever holds files from two exports, the box says so instead of showing wrong
results.

Files on another host (a CDN)
-----------------------------
The box may load the folder from another origin if that host sends
Access-Control-Allow-Origin. The settings files above carry that line, turned
off; nginx: add_header Access-Control-Allow-Origin "*";

nginx
-----
nginx knows these file types already, except perhaps .wasm and .onnx:

  types { application/wasm wasm; application/octet-stream onnx bin; }
  location = /search/manifest.json { add_header Cache-Control "no-cache"; }

Everything in this folder is public: the text of every passage goes to every
visitor.
