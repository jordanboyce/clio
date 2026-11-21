/*! Clio static search: reference client. MIT License. https://github.com/jordanboyce/clio */
// Searches a collection exported by Clio (Export, "For a website") with no
// server: load the files, embed the query in the browser with the same
// model, rank by dot product. See docs/STATIC_SEARCH.md.
//
//   import { createSearch, transformersEmbedder, passageLink } from './clio-static-search.js'
//   import { pipeline } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2'
//
//   const search = await createSearch('./search/', {
//     embedder: (manifest) => transformersEmbedder(manifest, { pipeline }),
//   })
//   const hits = await search('how much holiday do staff get', { k: 5 })
//   // [{ score, passage: { id, document_id, page, text }, document: { id, name, title, pages, link } }]
//   passageLink(hits[0])   // where the passage is on the web, or null
//
// No dependencies. The embedder is passed in, so a site decides where the
// model and the library come from (a CDN, or its own files on a closed network).
// An export made as a whole site carries both: see localTransformers below.
// For a search box to drop into a page, see clio-search.js beside this file;
// what every component shares is in clio-common.js, which this file imports.

import { FORMAT, VERSION, getListed, loadManifest, passageLink, readerLink } from './clio-common.js'

// The format this client reads (v1: searched by meaning; v2: no vectors,
// searched by keyword). Kept here for pages that imported them from this file.
export { FORMAT, VERSION, passageLink, readerLink }

// How close the site's vector for the manifest's probe sentence must be to
// Clio's. The same model at full or half precision scores above 0.9999; an
// 8-bit build of it scores about 0.99 and reorders close results.
export const MIN_PROBE_COSINE = 0.999

export class ModelMismatch extends Error {
  constructor(message) {
    super(message)
    this.name = 'ModelMismatch'
  }
}

/**
 * Load manifest.json, the passage file(s) and vectors.bin (when the export
 * has vectors) from a folder. The manifest is never read from a cache; `verify`
 * (on by default) checks each file against its checksum.
 */
export async function loadIndex(baseUrl, { fetch: fetchFn = globalThis.fetch, verify = true } = {}) {
  // Never from a cache (clio-common.js says why).
  const manifest = await loadManifest(baseUrl, { fetch: fetchFn })
  const vectorsEntry = manifest.vectors
    ? { file: manifest.vectors.file, sha256: manifest.vectors.sha256 } : null
  const [buffer, ...parts] = await Promise.all([
    vectorsEntry ? getListed(fetchFn, baseUrl, vectorsEntry, 'buffer', verify) : null,
    ...manifest.passages.files.map((f) => getListed(fetchFn, baseUrl, f, 'json', verify)),
  ])
  const index = buildIndex(manifest, parts.flat(), buffer)
  index.baseUrl = baseUrl
  return index
}

/** The same, from values already in hand (tests, or files read another way). */
export function buildIndex(manifest, passages, buffer) {
  const documents = new Map((manifest.documents || []).map((d) => [d.id, d]))
  if (!manifest.vectors) {
    // Searched by keyword: no vectors to line up.
    if (manifest.passages?.count != null && passages.length !== manifest.passages.count) {
      throw new Error(`The export has ${passages.length} passages; its manifest says ${manifest.passages.count}.`)
    }
    return { manifest, passages, vectors: null, count: passages.length, dimension: 0, scale: 1, documents }
  }
  const { count, dimension, quantization, scale } = manifest.vectors
  const vectors = quantization === 'int8' ? new Int8Array(buffer) : new Float32Array(buffer)
  if (vectors.length !== count * dimension) {
    throw new Error(`vectors.bin holds ${vectors.length} numbers, not ${count} x ${dimension}.`)
  }
  if (passages.length !== count) {
    throw new Error(`The export has ${passages.length} passages for ${count} vectors.`)
  }
  return { manifest, passages, vectors, count, dimension, scale: scale || 1, documents }
}

const dot = (a, b) => {
  let sum = 0
  for (let i = 0; i < a.length; i++) sum += a[i] * b[i]
  return sum
}

function unit(vector) {
  const norm = Math.sqrt(dot(vector, vector))
  return norm ? Array.from(vector, (x) => x / norm) : Array.from(vector)
}

/**
 * Refuse a model that is not the one the vectors came from. Throws
 * ModelMismatch. With a probe in the manifest this compares vectors, which
 * also catches wrong pooling, a missing query prefix and a quantised build;
 * without one it can only compare names.
 */
export async function checkModel(manifest, embedder, { minProbeCosine = MIN_PROBE_COSINE } = {}) {
  const embedding = manifest.embedding
  if (!manifest.vectors || !embedding.browser) {
    throw new ModelMismatch(embedding.reason || 'This export has no vectors, so it is searched by keyword.')
  }
  const probe = embedding.probe
  if (!probe) {
    if (embedder.model && embedder.model !== embedding.browser.model && embedder.model !== embedding.model) {
      throw new ModelMismatch(
        `This index was built with ${embedding.model}; the page loaded ${embedder.model}.`)
    }
    return { checked: 'name' }
  }
  const mine = unit(await embedder.embed(probe.text))
  if (mine.length !== embedding.dimension) {
    throw new ModelMismatch(
      `This index was built with ${embedding.model} (${embedding.dimension} dimensions); ` +
      `the page's model gives ${mine.length}.`)
  }
  const cosine = dot(mine, probe.vector)
  if (cosine < minProbeCosine) {
    throw new ModelMismatch(
      `The page's model does not reproduce the vectors of ${embedding.model} ` +
      `(agreement ${cosine.toFixed(4)}, needs ${minProbeCosine}). Search by meaning is off.`)
  }
  return { checked: 'probe', cosine }
}

/** Scores of every passage for a query vector, on Clio's cosine scale. */
function scoresFor(index, queryVector) {
  const { vectors, dimension, count, scale } = index
  if (!vectors) throw new ModelMismatch('This export has no vectors, so it is searched by keyword.')
  if (queryVector.length !== dimension) {
    throw new ModelMismatch(`The query vector has ${queryVector.length} dimensions; the index has ${dimension}.`)
  }
  const query = unit(queryVector)
  const scores = new Float32Array(count)
  for (let row = 0, offset = 0; row < count; row++, offset += dimension) {
    let sum = 0
    for (let i = 0; i < dimension; i++) sum += vectors[offset + i] * query[i]
    scores[row] = sum / scale
  }
  return scores
}

/** The k passages closest to a query vector, best first. */
export function rank(index, queryVector, { k = 10 } = {}) {
  return top(index, scoresFor(index, queryVector), k)
}

function top(index, scores, k) {
  const order = Array.from(scores.keys()).sort((a, b) => scores[b] - scores[a] || a - b).slice(0, k)
  return order.map((row) => {
    const passage = index.passages[row]
    return { score: scores[row], passage, document: index.documents.get(passage.document_id) || null, index }
  })
}

export const words = (text) => text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || []

function keywordScores(index, query) {
  if (!index.terms) index.terms = index.passages.map((p) => new Set(words(p.text)))
  const terms = [...new Set(words(query))]
  const weights = terms.map((t) => {
    const holding = index.terms.reduce((n, set) => n + (set.has(t) ? 1 : 0), 0)
    return holding ? Math.log(1 + index.count / holding) : 0
  })
  const scores = new Float32Array(index.count)
  index.terms.forEach((set, row) => {
    terms.forEach((t, i) => { if (set.has(t)) scores[row] += weights[i] })
  })
  return scores
}

/**
 * Plain keyword ranking, for when search by meaning is off. Counts how many
 * of the query's words a passage holds, rarer words weighing more. It is a
 * fallback, not Clio's keyword search.
 */
export function keywordRank(index, query, { k = 10 } = {}) {
  return top(index, keywordScores(index, query), k).filter((hit) => hit.score > 0)
}

// ── Where a passage is on the web ──────────────────────────────────────────
// passageLink and readerLink live in clio-common.js, shared with the other
// components; they are exported from here too, as before.

// ── The model ──────────────────────────────────────────────────────────────

/**
 * transformers.js from the site's own files, set up to load models only from
 * the site: the layout an export made as a whole site has (vendor/ holds the
 * library and its WebAssembly runtime, models/<model>/ the model). Returns
 * the library's module; pass its `pipeline` to transformersEmbedder with
 * `model: manifest.embedding.browser.bundled.model`.
 *
 * `base` is the folder holding vendor/ and models/; by default the page's.
 * It may be on another origin (an assets host or CDN that sends CORS
 * headers): the model is then read from there as a remote host.
 */
export async function localTransformers({ vendor = './vendor/', models = './models/', base, load } = {}) {
  const root = base || globalThis.document?.baseURI || globalThis.location?.href
  const at = (path, file = '') => new URL(file, new URL(path, root)).href
  const library = await (load || ((url) => import(url)))(at(vendor, 'transformers.min.js'))
  const { env } = library
  const modelsUrl = new URL(models, root)
  const here = globalThis.location?.origin
  if (here && modelsUrl.origin !== here) {
    // Another origin: transformers.js reads it as a model hub of its own.
    env.allowLocalModels = false
    env.allowRemoteModels = true
    env.remoteHost = modelsUrl.href
    env.remotePathTemplate = '{model}/'
  } else {
    env.allowRemoteModels = false
    env.allowLocalModels = true
    // A path on this site, never a full http(s) URL: given a URL, transformers.js
    // 4.3.2 skips its check that a local file exists, decides the model has no
    // tokenizer, and every query fails (the model itself still loads).
    env.localModelPath = modelsUrl.pathname
  }
  // Both files named outright: the loader ships as .js, not the .mjs the
  // library would look for, because many servers send .mjs as text/plain.
  env.backends.onnx.wasm.wasmPaths = {
    mjs: at(vendor, 'ort-wasm-simd-threaded.asyncify.js'),
    wasm: at(vendor, 'ort-wasm-simd-threaded.asyncify.wasm'),
  }
  return library
}

/**
 * An embedder built on transformers.js. Pass its `pipeline` function; the
 * model, pooling and query prefix come from the manifest. `dtype` should stay
 * 'fp32' or 'fp16': 'q8' is a third of the download and fails the probe.
 */
export async function transformersEmbedder(manifest, { pipeline, model, dtype, options = {} }) {
  const embedding = manifest.embedding
  const name = model || embedding.browser.model
  const extractor = await pipeline('feature-extraction', name, { dtype: dtype || embedding.browser.dtype, ...options })
  return {
    model: name,
    async embed(text) {
      const output = await extractor((embedding.query_prefix || '') + text,
        { pooling: embedding.pooling, normalize: true })
      return Array.from(output.data)
    },
  }
}

// ── Searching ──────────────────────────────────────────────────────────────

/**
 * Load an export and return `search(query, { k })`. `embedder` is an object
 * with `embed(text)`, or a function of the manifest that returns one (so the
 * model is only fetched once the manifest says which). When the model is
 * missing, fails to load or is not the right one, `search.semantic` is false,
 * `search.reason` says why, and searches fall back to keywords.
 *
 * Leave `embedder` out to start with keywords at once, and call
 * `search.upgrade(embedder)` later (when the visitor first uses the box, say)
 * to switch to search by meaning without loading the files again.
 */
export async function createSearch(baseUrl, { embedder, fetch: fetchFn, minProbeCosine, verify } = {}) {
  const index = await loadIndex(baseUrl, { fetch: fetchFn, verify })
  let ready = null
  const search = async (query, { k = 10 } = {}) => {
    if (!ready) return keywordRank(index, query, { k })
    return rank(index, await ready.embed(query), { k })
  }
  search.index = index
  search.semantic = false
  search.canUseMeaning = Boolean(index.vectors && index.manifest.embedding.browser)
  search.reason = search.canUseMeaning ? 'No embedding model was given.'
    : (index.manifest.embedding.reason || 'This export has no vectors, so it is searched by keyword.')
  search.embedder = null
  search.upgrade = async (next) => {
    if (!search.canUseMeaning) return false
    try {
      const built = typeof next === 'function' ? await next(index.manifest) : next
      await checkModel(index.manifest, built, { minProbeCosine })
      ready = built
      search.embedder = built
      search.semantic = true
      search.reason = ''
    } catch (error) {
      search.reason = error.message
    }
    return search.semantic
  }
  if (embedder) await search.upgrade(embedder)
  return search
}

/**
 * Several exports searched as one (each made with createSearch). When every
 * one is searched by meaning with the same model, the query is embedded once
 * and the scores are compared directly; otherwise all are searched by keyword,
 * since a keyword score and a similarity cannot be compared.
 */
export function combineSearches(searches) {
  const sameModel = () => {
    const models = new Set(searches.map((s) => s.index.manifest.embedding.signature || s.index.manifest.embedding.model))
    return models.size === 1 && searches.every((s) => s.semantic)
  }
  const combined = async (query, { k = 10 } = {}) => {
    let hits
    if (searches.length && sameModel()) {
      const vector = await searches[0].embedder.embed(query)
      hits = searches.flatMap((s) => rank(s.index, vector, { k }))
    } else {
      hits = searches.flatMap((s) => keywordRank(s.index, query, { k }))
    }
    return hits.sort((a, b) => b.score - a.score).slice(0, k)
  }
  Object.defineProperty(combined, 'semantic', { get: () => searches.length > 0 && sameModel() })
  combined.searches = searches
  return combined
}
