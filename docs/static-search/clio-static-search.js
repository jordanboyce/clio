/*! Clio static search: reference client. MIT License. https://github.com/jordanboyce/clio */
// Searches a collection exported by Clio (Export, "For a website") with no
// server: load the three files, embed the query in the browser with the same
// model, rank by dot product. See docs/STATIC_SEARCH.md.
//
//   import { createSearch, transformersEmbedder } from './clio-static-search.js'
//   import { pipeline } from 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2'
//
//   const search = await createSearch('./search/', {
//     embedder: (manifest) => transformersEmbedder(manifest, { pipeline }),
//   })
//   const hits = await search('how much holiday do staff get', { k: 5 })
//   // [{ score, passage: { id, document_id, page, text }, document: { id, name, pages } }]
//
// No dependencies. The embedder is passed in, so a site decides where the
// model and the library come from (a CDN, or its own files on a closed network).

export const FORMAT = 'clio-static-search'
export const VERSION = 1

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

const join = (base, file) => (base.endsWith('/') ? base : base + '/') + file

async function get(fetchFn, url, kind) {
  const response = await fetchFn(url)
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`)
  return kind === 'json' ? response.json() : response.arrayBuffer()
}

/** Load manifest.json, the passage file(s) and vectors.bin from a folder. */
export async function loadIndex(baseUrl, { fetch: fetchFn = globalThis.fetch } = {}) {
  const manifest = await get(fetchFn, join(baseUrl, 'manifest.json'), 'json')
  if (manifest.format !== FORMAT) throw new Error('That folder is not a Clio static search export.')
  if (manifest.version > VERSION) {
    throw new Error(`This export is format v${manifest.version}; this client reads up to v${VERSION}.`)
  }
  const [buffer, ...parts] = await Promise.all([
    get(fetchFn, join(baseUrl, manifest.vectors.file), 'buffer'),
    ...manifest.passages.files.map((f) => get(fetchFn, join(baseUrl, f.file), 'json')),
  ])
  return buildIndex(manifest, parts.flat(), buffer)
}

/** The same, from values already in hand (tests, or files read another way). */
export function buildIndex(manifest, passages, buffer) {
  const { count, dimension, quantization, scale } = manifest.vectors
  const vectors = quantization === 'int8' ? new Int8Array(buffer) : new Float32Array(buffer)
  if (vectors.length !== count * dimension) {
    throw new Error(`vectors.bin holds ${vectors.length} numbers, not ${count} x ${dimension}.`)
  }
  if (passages.length !== count) {
    throw new Error(`The export has ${passages.length} passages for ${count} vectors.`)
  }
  const documents = new Map((manifest.documents || []).map((d) => [d.id, d]))
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

/** The k passages closest to a query vector, best first. */
export function rank(index, queryVector, { k = 10 } = {}) {
  const { vectors, dimension, count, scale } = index
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
  return top(index, scores, k)
}

function top(index, scores, k) {
  const order = Array.from(scores.keys()).sort((a, b) => scores[b] - scores[a] || a - b).slice(0, k)
  return order.map((row) => {
    const passage = index.passages[row]
    return { score: scores[row], passage, document: index.documents.get(passage.document_id) || null }
  })
}

const words = (text) => text.toLowerCase().match(/[\p{L}\p{N}]+/gu) || []

/**
 * Plain keyword ranking, for when search by meaning is off. Counts how many
 * of the query's words a passage holds, rarer words weighing more. It is a
 * fallback, not Clio's keyword search.
 */
export function keywordRank(index, query, { k = 10 } = {}) {
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
  return top(index, scores, k).filter((hit) => hit.score > 0)
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

/**
 * Load an export and return `search(query, { k })`. `embedder` is an object
 * with `embed(text)`, or a function of the manifest that returns one (so the
 * model is only fetched once the manifest says which). When the model is
 * missing, fails to load or is not the right one, `search.semantic` is false,
 * `search.reason` says why, and searches fall back to keywords.
 */
export async function createSearch(baseUrl, { embedder, fetch: fetchFn, minProbeCosine } = {}) {
  const index = await loadIndex(baseUrl, { fetch: fetchFn })
  let ready = null
  let reason = 'No embedding model was given.'
  if (embedder) {
    try {
      const built = typeof embedder === 'function' ? await embedder(index.manifest) : embedder
      await checkModel(index.manifest, built, { minProbeCosine })
      ready = built
      reason = ''
    } catch (error) {
      reason = error.message
    }
  }
  const search = async (query, { k = 10 } = {}) => {
    if (!ready) return keywordRank(index, query, { k })
    return rank(index, await ready.embed(query), { k })
  }
  search.index = index
  search.semantic = Boolean(ready)
  search.reason = reason
  return search
}
