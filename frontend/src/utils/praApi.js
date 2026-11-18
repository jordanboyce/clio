// The PRA routes (api/pra.py, shapes in services/pra/queries.py). Names may
// hold any character the formats allow, "/" included; the routes take the
// rest of the path, so each name is one encoded segment.
import { http } from './http'

const enc = encodeURIComponent
const base = (documentId) => `/api/pra/models/${enc(documentId)}`

// The server stops a cut set search at its time budget (20 s) and answers
// then; the client waits a little longer than that before calling it lost.
export const COMPUTE_TIMEOUT_MS = 60000

function analysisParams(collectionId, { limit, compute, truncation, maxOrder, successTerms, eventTree, sort } = {}) {
  const params = { collection_id: collectionId }
  if (limit) params.limit = limit
  if (compute) params.compute = true
  if (compute && truncation) params.truncation = truncation
  if (compute && maxOrder) params.max_order = maxOrder
  if (compute && successTerms) params.success_terms = successTerms
  if (eventTree) params.event_tree = eventTree
  if (sort) params.sort = sort
  return params
}

// Opening a model fires a few reads at once, and opening Clio straight onto
// one (a link) adds the app's own: a burst can meet the deployment's rate
// limiter. A read waits the second or two it is asked to, once, quietly.
async function read(url, params) {
  try {
    return (await http.get(url, { params, quiet: true })).data
  } catch (err) {
    if (!err?.isRateLimit) throw err
    await new Promise(resolve => setTimeout(resolve, Math.min(5, Number(err.retryAfterSeconds) || 1) * 1000))
    return (await http.get(url, { params })).data
  }
}

export const praApi = {
  model: (collectionId, documentId) =>
    read(base(documentId), { collection_id: collectionId }),
  faultTree: (collectionId, documentId, tree, depth = 4) =>
    read(`${base(documentId)}/fault-trees/${enc(tree)}`, { collection_id: collectionId, depth }),
  eventTree: (collectionId, documentId, name) =>
    read(`${base(documentId)}/event-trees/${enc(name)}`, { collection_id: collectionId }),
  event: (collectionId, documentId, name) =>
    read(`${base(documentId)}/events/${enc(name)}`, { collection_id: collectionId }),
  // quiet: a busy server (429) is reported in the pane, not as a toast.
  cutSets: (collectionId, documentId, context, opts = {}, { signal } = {}) =>
    http.get(`${base(documentId)}/cut-sets/${enc(context)}`, {
      params: analysisParams(collectionId, opts), signal, quiet: true,
      timeout: opts.compute ? COMPUTE_TIMEOUT_MS : 30000,
    }).then(r => r.data),
  importance: (collectionId, documentId, context, opts = {}, { signal } = {}) =>
    http.get(`${base(documentId)}/importance/${enc(context)}`, {
      params: analysisParams(collectionId, opts), signal, quiet: true,
      timeout: opts.compute ? COMPUTE_TIMEOUT_MS : 30000,
    }).then(r => r.data),
}

// What a failed analysis request means for the person waiting on it.
export function analysisError(err) {
  if (err?.status === 429) return { kind: 'busy', message: 'Two other cut set computations are running on the server. Try again in a few seconds.' }
  if (err?.status === 409) return { kind: 'stopped', message: err.message }
  if (err?.isTimeout) return { kind: 'timeout', message: 'The server did not answer in time. It may still be working; try again in a minute.' }
  if (err?.status === 404) return { kind: 'missing', message: err.message }
  return { kind: 'error', message: err?.message || 'The request failed.' }
}
