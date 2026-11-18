/**
 * Plain-language facts for choosing the search model (the embedding model):
 * what one model holds, whether a collection's passages fit in it, and what
 * a switch costs. Pure functions so the Settings form and its tests agree.
 */

// English prose averages about four characters per token. Chunk sizes are
// set in characters, model limits in tokens, so the comparison is approximate
// and the wording says "about".
export const CHARS_PER_TOKEN = 4

/** About how many tokens a passage of `chars` characters holds. */
export function approxTokens(chars) {
  const n = Number(chars)
  return Number.isFinite(n) && n > 0 ? Math.ceil(n / CHARS_PER_TOKEN) : 0
}

/**
 * Whether passages of `chunkChars` characters fit in a model that reads at
 * most `maxInputTokens` tokens per input. Unknown limits never warn.
 * Returns { fits, passageTokens, maxInputTokens }.
 */
export function chunkFit(chunkChars, maxInputTokens) {
  const passageTokens = approxTokens(chunkChars)
  const limit = Number(maxInputTokens) || 0
  return { fits: !limit || !passageTokens || passageTokens <= limit, passageTokens, maxInputTokens: limit || null }
}

/**
 * The short facts line under the model picker:
 * ["384 dimensions", "reads up to 256 tokens", "English", "on this server"].
 */
export function embeddingModelFacts(model, provider) {
  if (!model) return []
  const out = []
  if (model.dimensions) out.push(`${Number(model.dimensions).toLocaleString()} dimensions`)
  if (model.max_input_tokens) out.push(`reads up to ${Number(model.max_input_tokens).toLocaleString()} tokens`)
  if (model.language) out.push(model.language)
  if (provider) out.push(embeddingLocation(provider))
  if (model.adds_prefixes) out.push('search prefixes added for you')
  return out
}

/** Where a provider's model runs, as a reader would say it. */
export function embeddingLocation(provider) {
  if (!provider) return ''
  if (provider.id === 'local') return 'on this server'
  if (provider.id === 'ollama') return 'on your Ollama server'
  if (provider.hosted) return `hosted by ${provider.label}`
  return 'at the endpoint you enter'
}

// Passages embedded per second, by where the model runs. Deliberately
// cautious figures for a small server: the built-in CPU models slow down
// sharply with size, Ollama depends on its hardware, and hosted APIs take
// batches of 64 at network speed.
function passagesPerSecond(kind, dimensions) {
  if (kind === 'local') {
    if (!dimensions || dimensions <= 384) return 40
    if (dimensions <= 768) return 12
    return 4
  }
  if (kind === 'ollama') return 25
  return 150
}

/** Minutes to embed `passages` with a model of this kind and size (at least 1). */
export function estimateEmbedMinutes(passages, { kind = 'hosted', dimensions = null } = {}) {
  const n = Number(passages) || 0
  if (n <= 0) return 0
  return Math.max(1, Math.ceil(n / passagesPerSecond(kind, dimensions) / 60))
}

/** "under a minute", "about 12 minutes", "about 3 hours". */
export function describeMinutes(minutes) {
  if (!minutes || minutes < 1) return 'under a minute'
  if (minutes === 1) return 'about a minute'
  if (minutes < 90) return `about ${minutes} minutes`
  const hours = Math.round(minutes / 60)
  return `about ${hours} hour${hours === 1 ? '' : 's'}`
}

const plural = (n, one, many) => `${Number(n).toLocaleString()} ${n === 1 ? one : many}`

/**
 * The sentence shown before a switch, from the server's real counts
 * (GET /api/embedding/switch-cost). Returns '' while the counts are unknown.
 */
export function switchCostSentence(cost, { kind, dimensions } = {}) {
  if (!cost) return ''
  if (!cost.passages) return 'Nothing is indexed yet, so this change costs nothing.'
  const minutes = estimateEmbedMinutes(cost.passages, { kind, dimensions })
  const where = cost.collections > 1 ? ` in ${plural(cost.collections, 'collection', 'collections')}` : ''
  return `Changing the search model means re-indexing ${plural(cost.sources, 'source', 'sources')}${where} `
    + `(${plural(cost.passages, 'passage', 'passages')}). Embedding them with this model takes ${describeMinutes(minutes)}, `
    + 'plus the time to read the files again. Search keeps using the current index until each collection is re-indexed.'
}

/** Ollama's own rule: "nomic-embed-text" is "nomic-embed-text:latest" (B2). */
export function ollamaBaseName(name) {
  const s = String(name || '').trim()
  return s.endsWith(':latest') ? s.slice(0, -':latest'.length) : s
}

export function sameOllamaModel(a, b) {
  return Boolean(a) && Boolean(b) && ollamaBaseName(a) === ollamaBaseName(b)
}
