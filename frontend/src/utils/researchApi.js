import http from './http'
import { buildProviderHeaders, getAPIProviderName } from './aiProviders.js'

// The Research view's calls to /api/research. Every call is scoped to one
// collection; the ones that run a model carry the active provider's headers
// exactly as Ask does, so a team key or the deployment's model just works.

const q = (collectionId, extra = {}) => {
  const params = new URLSearchParams({ collection_id: collectionId })
  for (const [k, v] of Object.entries(extra)) {
    if (v !== undefined && v !== null && v !== '') params.set(k, String(v))
  }
  return params.toString()
}

const modelArgs = (providerId) => providerId
  ? { provider: getAPIProviderName(providerId), headers: buildProviderHeaders(providerId) }
  : { provider: null, headers: {} }

export const researchApi = {
  papers: (cid) => http.get(`/api/research/papers?${q(cid)}`).then(r => r.data),
  paper: (cid, id) => http.get(`/api/research/papers/${encodeURIComponent(id)}?${q(cid)}`).then(r => r.data),
  analyzeOne: (cid, id) =>
    http.post(`/api/research/papers/${encodeURIComponent(id)}/analyze?${q(cid)}`, {}, { timeout: 120000 }).then(r => r.data),
  analyzeMany: (cid, body = {}) => http.post(`/api/research/analyze?${q(cid)}`, body).then(r => r.data),
  rename: (cid, id, body = {}) =>
    http.post(`/api/research/papers/${encodeURIComponent(id)}/rename?${q(cid)}`, body).then(r => r.data),
  restoreName: (cid, id) =>
    http.post(`/api/research/papers/${encodeURIComponent(id)}/restore-name?${q(cid)}`).then(r => r.data),
  renameMany: (cid, documentIds, pattern, dryRun = false) =>
    http.post(`/api/research/rename?${q(cid)}`, { document_ids: documentIds, pattern: pattern || null, dry_run: dryRun })
      .then(r => r.data),
  resolve: (cid, body) =>
    http.post(`/api/research/references/resolve?${q(cid)}`, body, { timeout: 180000 }).then(r => r.data),
  download: (cid, body) =>
    http.post(`/api/research/references/download?${q(cid)}`, body, { timeout: 180000 }).then(r => r.data),
  graph: (cid, { includeExternal = true, minCitations = 2, documentIds = null } = {}) =>
    http.post(`/api/research/graph?${q(cid)}`, {
      include_external: includeExternal, min_citations: minCitations, document_ids: documentIds?.length ? documentIds : null,
    }).then(r => r.data),
  assess: (cid, id, question, providerId) => {
    const { provider, headers } = modelArgs(providerId)
    return http.post(`/api/research/papers/${encodeURIComponent(id)}/assess?${q(cid)}`,
      { question, provider }, { headers, timeout: 180000 }).then(r => r.data)
  },
  readingList: (cid, question) => http.get(`/api/research/assessments?${q(cid, { question })}`).then(r => r.data),
  startJob: (cid, body, providerId) => {
    const { provider, headers } = modelArgs(providerId)
    return http.post(`/api/research/jobs?${q(cid)}`, { ...body, provider }, { headers }).then(r => r.data)
  },
  jobs: (cid) => http.get(`/api/research/jobs?${q(cid)}`).then(r => r.data),
  cancelJob: (cid, jobId) => http.post(`/api/research/jobs/${jobId}/cancel?${q(cid)}`).then(r => r.data),
  briefs: (cid, { unread = false, documentId = null } = {}) =>
    http.get(`/api/research/briefs?${q(cid, { unread: unread || null, document_id: documentId })}`).then(r => r.data),
  markBrief: (cid, id, read = true) => http.post(`/api/research/briefs/${id}/read?${q(cid)}`, { read }).then(r => r.data),
  deleteBrief: (cid, id) => http.delete(`/api/research/briefs/${id}?${q(cid)}`).then(r => r.data),
}

export const VERDICTS = {
  relevant: { label: 'Worth reading', tone: 'badge-success' },
  partial: { label: 'Partly relevant', tone: 'badge-info' },
  not_relevant: { label: 'Not about this', tone: 'badge-ghost' },
  not_established: { label: 'Can’t tell from the text', tone: 'badge-warning' },
}

export function pageUrl(collectionId, documentId, page) {
  const base = `/documents/${encodeURIComponent(documentId)}/pdf?collection_id=${encodeURIComponent(collectionId)}`
  return page ? `${base}#page=${page}` : base
}

export function authorsShort(authors = []) {
  if (!authors.length) return ''
  const surname = (name) => name.trim().split(/\s+/).pop()
  if (authors.length === 1) return surname(authors[0])
  if (authors.length === 2) return `${surname(authors[0])} & ${surname(authors[1])}`
  return `${surname(authors[0])} et al.`
}

// provenance.source 'paper' means "read from the document itself" (the
// stored value predates books; it is not shown as the word "paper").
export function sourceLabel(provenance) {
  if (!provenance) return ''
  if (provenance.source === 'paper') return provenance.page ? `From the source, page ${provenance.page}` : 'From the source'
  const names = { openalex: 'OpenAlex', crossref: 'Crossref', arxiv: 'arXiv', openlibrary: 'Open Library' }
  return `From ${names[provenance.source] || provenance.source}`
}

// A book has no abstract; its opening (preface, introduction) is shown in
// its place, quoted, and must not be presented as an abstract.
export function isOpening(provenance) {
  return provenance?.kind === 'opening'
}

export function abstractLabel(provenance) {
  if (!provenance) return ''
  if (isOpening(provenance)) {
    const where = provenance.page ? `, page ${provenance.page}` : ''
    const heading = (provenance.detail || '').match(/opening of the (\w+)/)?.[1]
    return `Opening of the ${heading || 'text'}${where} — quoted; there is no abstract`
  }
  return sourceLabel(provenance)
}

// "Tonight at 01:00" as an ISO time the server can wait for.
export function nextLocalTime(hhmm, now = new Date()) {
  const [h, m] = String(hhmm || '').split(':').map(Number)
  if (!Number.isFinite(h) || !Number.isFinite(m)) return null
  const at = new Date(now)
  at.setHours(h, m, 0, 0)
  if (at <= now) at.setDate(at.getDate() + 1)
  return at.toISOString()
}
