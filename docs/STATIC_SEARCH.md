# Search on a static website

A collection can be exported as three files that a static website searches by meaning, with no Clio and no server behind it. The page downloads the files, embeds the visitor's query in the browser, and ranks the passages itself.

It can also be exported as a **whole site**, ready to serve: the three files, a search page, and the embedding model and library the page runs, so the site loads nothing from Hugging Face or a CDN. Use that on a network that blocks either, or for a site whose visitors' browsers should contact no one else ([below](#a-site-that-carries-its-own-model)).

Use it for a small, public body of text: a handbook, a course catalogue, a set of published guidance. For anything private, or anything large, share the collection or use the [App API](APP_API.md) instead.

## What you get

| File | Holds |
|---|---|
| `manifest.json` | The embedding model and how it pools, the vector size and quantization, the passage count, the export date, a checksum for each file, and the list of sources. |
| `passages.json` | One passage per line: `id`, `document_id`, `page`, `text`. Above 2,000 passages it is split into `passages-0001.json`, `passages-0002.json`, and so on; the manifest lists them in order. |
| `vectors.bin` | One vector per passage, in the same order as the passages, as signed 8-bit integers with no header. A browser reads it straight into an `Int8Array`. |

A passage's score for a query is `dot(row, query) / manifest.vectors.scale`, where `query` is the query's unit-length vector. That is cosine similarity, the score Clio's own semantic search reports, to within about 0.003.

Sizes: 384 bytes of vectors per passage with either supported model, plus the passage text and about 90 bytes of JSON around it. A collection of 5,000 passages of 800 characters comes to roughly 2 MB of vectors and 4.5 MB of text, before the web server compresses it. The Export dialog states both figures before you download.

## Exporting

**In the app.** Use the **Export** button beside the collection, then the **For a website** tab. It names the model the site will have to use, says how many sources are left out and why, and gives the sizes. **Include the model** is ticked whenever this server can include it, and gives `<name>.static-site.zip`, a whole site; untick it for `<name>.static-search.zip`, the three files alone. Tick the confirmation and download.

**From the command line**, which suits a site kept in a repository:

```bash
python scripts/export_static.py "Staff handbook" --check
python scripts/export_static.py "Staff handbook" --out ../site/public/search --confirm-public
python scripts/export_static.py "Staff handbook" --out ../site/public --site --confirm-public
```

Run it with the server's environment (`DATA_DIR` and the rest of `.env`). It only reads, needs no network, and replaces a previous export in `--out` while leaving the folder's other files alone. If the export is refused, last time's files stay as they were. `--float32` writes full-precision vectors, four times the size, for anyone who wants scores identical to Clio's. `--site` writes the whole site into `--out`, with the export in `search/`.

**Over HTTP.** `GET /api/collections/{id}/export/static/preview` returns the counts, sizes, model and any `blockers`. `GET /api/collections/{id}/export/static?confirm_public=true` returns the zip (`quantization=int8|float32`, `passages_per_file`, and `site=true` for the whole site, which the preview's `site.available` says this server can make). Both are for the collection's owner.

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

## The same model on both sides

A query vector is only comparable with passage vectors from the same model, pooled and normalised the same way, with the same query prefix. The browser has to run the model itself, so the export is offered only for a collection embedded **on this machine** with a model that has a browser build whose vectors were compared with Clio's:

| Collection's model | Browser build | Pooling | Query prefix |
|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | `Xenova/bge-small-en-v1.5` | CLS | `Represent this sentence for searching relevant passages: ` |
| `all-MiniLM-L6-v2` | `Xenova/all-MiniLM-L6-v2` | mean | none |

Both give 384 dimensions. Measured with transformers.js 4.3.2 against Clio's fastembed backend: at `fp32` and `fp16` the browser's query vector agrees with Clio's to a cosine of 0.99999 or better. The `q8` builds, a third of the download, agree only to 0.991 to 0.995, which moved scores by up to 0.02 and reordered close results. Use `fp32` (about 130 MB for bge-small, 90 MB for MiniLM) or `fp16` (half that).

A collection embedded with any other model, or through Ollama or a hosted embedding service, is refused with a message saying so. Clone it or re-index it with one of the two models above, then export the copy. Clio does not re-embed during export: that would publish vectors nobody has searched against.

The manifest also carries a **probe**: one fixed sentence and the vector Clio's model gives it. A page embeds the sentence itself and compares. If the result disagrees, the page has the wrong model, the wrong pooling, a missing prefix or a quantised build, and it must not search by meaning. The reference client does this check and falls back to keyword matching. When the embedding model could not be loaded at export time the probe is `null`, and a page has only the model's name to go on.

## The reference client

[static-search/clio-static-search.js](static-search/clio-static-search.js) is a single JavaScript module with no dependencies, and [static-search/index.html](static-search/index.html) is a page that uses it. Copy both beside a `search/` folder holding the export and serve the folder with any static web server.

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

It loads the files, checks the model against the probe, embeds the query with the manifest's prefix and pooling, and ranks every passage. Ranking is a plain loop: 5,000 passages take about 3 ms. `loadIndex`, `checkModel`, `rank` and `keywordRank` are exported for a site that wants to assemble these differently.

### A site that carries its own model

The example page, given only the three files, fetches transformers.js from jsDelivr and the model from the Hugging Face hub. An export made as a whole site carries both instead, and the same page loads them from beside itself:

```
index.html                 the reference page
clio-static-search.js      the reference client
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
- Every visitor downloads the whole index and the model. Past a few tens of thousands of passages, use a server.
- Spreadsheets are not in the export: Clio keeps them as tables, not passages.
- A source marked as replaced by a newer version is exported like any other. Remove it from the collection first if the site should not show it.
