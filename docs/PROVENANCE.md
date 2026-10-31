# Provenance

Clio is MIT licensed. Anyone may build on it, sell it, or rename it, and the
only thing the licence asks in return is that the copyright notice stays with
the code. This page documents the markers the code carries so that a product
derived from Clio can be recognised later, and how to look for them.

The markers prove lineage. They do not prevent copying, and none of them
contacts anyone: Clio makes no network request on their account.

## The lineage id

```
clio.helicon.58a48ab52f51
```

A string with no meaning that occurs nowhere else. It is defined once for the
backend in `services/provenance.py` and once for the frontend in
`frontend/vite.config.js`, and it must never change. A text search for it, in
a web search engine, a code search, a product's shipped bundle or its
database, is the test for descent from Clio.

## Where the markers surface

Each place survives a fork independently of the others, so a derivative has
to find and remove all of them to hide its origin.

| Where | What | Look with |
|---|---|---|
| Every HTTP response | `X-Clio-Lineage: clio.helicon.58a48ab52f51` | `curl -sI http://host:8473/health` |
| `GET /api/about` | `version`, `commit`, and an `origin` block with the id, source URL, licence and copyright | `curl -s http://host:8473/api/about` |
| MCP `health_check` tool | the same `origin` block | any MCP client |
| `data/app.db` (and Postgres) | table `clio_lineage` with the id, source URL, the version that created the database and when | `sqlite3 data/app.db 'select * from clio_lineage'` |
| Docker image | OCI labels `org.opencontainers.image.source`, `.revision`, `.version`, `.licenses`, `.authors` and `dev.clio.lineage` | `docker inspect ghcr.io/jordanboyce/clio:latest` |
| Built frontend, every JS and CSS chunk | a `/*! Clio <version> (<commit>) ... lineage:... */` banner that minification keeps | `head -c 300 dist/assets/*.js` |
| Built frontend, stylesheet | the custom property `--clio-lineage` on `html` | browser devtools |
| Served page | `<meta name="generator">` naming Clio and the id, plus `data-clio-lineage` and `data-clio-build` on `<html>` | view source |
| OpenAPI document | `info.version` and `info.license` | `/openapi.json` |

The version and commit come from `CLIO_VERSION` and `CLIO_VCS_REF` in a
Docker image (set from the `APP_VERSION` and `VCS_REF` build arguments) and
from `git describe` and `git rev-parse` in a source checkout.

## Checking a product

1. Search its shipped JavaScript, CSS and HTML for `clio.helicon`.
2. Request any URL and read the response headers.
3. If you can see its database, list the tables.
4. `docker inspect` its image and read the labels.
5. Compare its MCP tool names, its default port (8473), its data schema and
   its error messages against Clio's. Individually weak, a cluster of
   matches is strong evidence.

Finding the id shows the code descends from Clio. It does not by itself show
a licence violation: a derivative that keeps the copyright notice is in full
compliance, whatever else it changed.
