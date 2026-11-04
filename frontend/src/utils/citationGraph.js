// The citation graph's model: what is drawn, and the settings that shape it.
//
// The view (ResearchMap.vue) borrows the reading habits of graph tools such as
// Obsidian — dots sized by how connected they are, a live force layout,
// titles that fade in as you zoom, hover to light a source's neighbourhood —
// and adds what a research library needs: works cited but not held, and
// colour by the screening verdict for a question, so the map shows what is
// worth reading next. Everything here is plain data and functions so it can
// be tested without a canvas.

export const DEFAULT_SETTINGS = Object.freeze({
  // Filters
  search: '',
  showCited: true,        // works the sources cite that the collection lacks
  minCitations: 2,
  showOrphans: true,      // sources with no citation either way
  // Groups
  colorBy: 'type',        // type | year | relevance | community | none
  // Display
  arrows: false,
  textFade: 1.6,          // zoom level at which labels are fully visible
  nodeSize: 1,
  sizeBy: 'citations',    // citations | pagerank | betweenness
  linkThickness: 1,
  // Forces
  centerForce: 0.1,
  repelForce: 220,
  linkForce: 0.6,
  linkDistance: 60,
  // Local graph
  localDepth: 1,
  // The side panel (map overview, or the selected node)
  showPanel: true,
})

export const SLIDERS = {
  minCitations: { min: 1, max: 5, step: 1 },
  textFade: { min: 0.3, max: 3, step: 0.1 },
  nodeSize: { min: 0.4, max: 3, step: 0.1 },
  linkThickness: { min: 0.2, max: 4, step: 0.1 },
  centerForce: { min: 0, max: 0.4, step: 0.01 },
  repelForce: { min: 0, max: 600, step: 10 },
  linkForce: { min: 0, max: 1, step: 0.05 },
  linkDistance: { min: 20, max: 300, step: 5 },
  localDepth: { min: 1, max: 3, step: 1 },
}

export const SIZE_OPTIONS = [
  { id: 'citations', label: 'Citations' },
  { id: 'pagerank', label: 'PageRank' },
  { id: 'betweenness', label: 'Betweenness' },
]
const SIZE_IDS = SIZE_OPTIONS.map(o => o.id)

// Community colours come from this many theme slots; the rest share one.
export const COMMUNITY_SLOTS = 8

const STORAGE_KEY = 'clio_graph_settings'

// Settings are a per-viewer preference, like Obsidian's; storage can be
// unavailable (private windows), in which case the defaults simply apply.
export function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}')
    return sanitize({ ...DEFAULT_SETTINGS, ...saved, search: '' })
  } catch {
    return { ...DEFAULT_SETTINGS }
  }
}

export function saveSettings(settings) {
  try {
    const { search: _search, ...rest } = settings
    localStorage.setItem(STORAGE_KEY, JSON.stringify(rest))
  } catch { /* a preference, not data */ }
}

export function sanitize(settings) {
  const out = { ...DEFAULT_SETTINGS }
  for (const key of Object.keys(DEFAULT_SETTINGS)) {
    const value = settings?.[key]
    if (value === undefined) continue
    const def = DEFAULT_SETTINGS[key]
    if (typeof def === 'number') {
      const n = Number(value)
      if (!Number.isFinite(n)) continue
      const range = SLIDERS[key]
      out[key] = range ? Math.min(range.max, Math.max(range.min, n)) : n
    } else if (typeof def === 'boolean') {
      out[key] = Boolean(value)
    } else if (key === 'colorBy') {
      out[key] = ['type', 'year', 'relevance', 'community', 'none'].includes(value) ? value : def
    } else if (key === 'sizeBy') {
      out[key] = SIZE_IDS.includes(value) ? value : def
    } else {
      out[key] = String(value)
    }
  }
  return out
}

// ── what is drawn ───────────────────────────────────────────────────────

export function degrees(edges) {
  const deg = {}
  for (const e of edges) {
    deg[e.source] = (deg[e.source] || 0) + 1
    deg[e.target] = (deg[e.target] || 0) + 1
  }
  return deg
}

// Everything within `depth` citations of `id`, either direction.
export function localIds(id, edges, depth = 1) {
  const seen = new Set([id])
  let frontier = [id]
  for (let d = 0; d < depth && frontier.length; d++) {
    const next = []
    for (const e of edges) {
      for (const [a, b] of [[e.source, e.target], [e.target, e.source]]) {
        if (frontier.includes(a) && !seen.has(b)) { seen.add(b); next.push(b) }
      }
    }
    frontier = next
  }
  return seen
}

export function neighborIds(id, edges) {
  return localIds(id, edges, 1)
}

/**
 * The nodes and links to draw from the API graph and the settings.
 * ``localOf`` limits it to that node's local graph.
 */
export function visibleGraph(graph, settings, localOf = null) {
  const allNodes = graph?.nodes || []
  const allEdges = graph?.edges || []
  let nodes = settings.showCited ? allNodes : allNodes.filter(n => n.type !== 'external')
  let ids = new Set(nodes.map(n => n.id))
  let edges = allEdges.filter(e => ids.has(e.source) && ids.has(e.target))
  if (localOf && ids.has(localOf)) {
    const keep = localIds(localOf, edges, settings.localDepth)
    nodes = nodes.filter(n => keep.has(n.id))
    ids = keep
    edges = edges.filter(e => keep.has(e.source) && keep.has(e.target))
  } else if (!settings.showOrphans) {
    const linked = new Set(edges.flatMap(e => [e.source, e.target]))
    nodes = nodes.filter(n => linked.has(n.id))
  }
  return { nodes, edges, degree: degrees(edges) }
}

// Obsidian sizes a node by how many links it has; a hub reads at a glance.
export function nodeRadius(node, degree, nodeSize = 1) {
  const base = node.type === 'external' ? 2.6 : 3.4
  return (base + Math.sqrt(degree || 0) * 1.6) * nodeSize
}

// A dot sized by a network measure. The largest value reaches about what a
// well-cited node (16 links) gets from nodeRadius; a missing value is the base.
const METRIC_RADIUS_SPAN = 6.4
export function metricRadius(node, value, maxValue, nodeSize = 1) {
  const base = node?.type === 'external' ? 2.6 : 3.4
  const v = Number(value)
  const m = Number(maxValue)
  const share = Number.isFinite(v) && Number.isFinite(m) && m > 0 && v > 0 ? Math.min(1, v / m) : 0
  return (base + Math.sqrt(share) * METRIC_RADIUS_SPAN) * nodeSize
}

// Network rows by node id, and the largest value of each measure.
export function metricIndex(network) {
  const byId = {}
  const max = { pagerank: 0, betweenness: 0 }
  for (const row of network?.nodes || []) {
    byId[row.id] = row
    for (const key of Object.keys(max)) {
      const v = Number(row[key])
      if (Number.isFinite(v) && v > max[key]) max[key] = v
    }
  }
  return { byId, max }
}

// Labels fade in as you zoom past the threshold (Obsidian's "text fade").
export function labelAlpha(zoom, threshold) {
  const span = 0.4
  return Math.max(0, Math.min(1, (zoom - (threshold - span)) / span))
}

export function matchesQuery(node, query) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return false
  const hay = [node.title, node.label, node.filename, (node.authors || []).join(' '), node.year]
    .map(v => String(v || '').toLowerCase()).join(' ')
  return q.split(/\s+/).every(term => hay.includes(term))
}

// ── groups ──────────────────────────────────────────────────────────────

export function groupOf(node, colorBy, verdicts = {}, communityOf = null) {
  if (colorBy === 'none') return 'node'
  if (colorBy === 'community') {
    const c = communityOf?.[node.id]
    return Number.isInteger(c) && c >= 0 && c < COMMUNITY_SLOTS ? `community-${c}` : 'community-other'
  }
  if (colorBy === 'year') return 'year'
  if (colorBy === 'relevance') {
    if (node.type === 'external') return 'cited'
    return verdicts[node.id]?.verdict || 'unscreened'
  }
  if (node.type === 'external') return node.in_collection ? 'outside' : 'cited'
  return node.kind === 'book' ? 'book' : 'article'
}

// One legend row per community that has a colour slot, then the shared one.
export function communityLegend(network) {
  const communities = network?.communities || []
  const rows = communities.filter(c => Number.isInteger(c.id) && c.id >= 0 && c.id < COMMUNITY_SLOTS)
    .sort((a, b) => a.id - b.id)
    .map(c => ({ group: `community-${c.id}`, label: c.label, size: c.size }))
  const other = (network?.nodes || []).filter(n => !(Number.isInteger(n.community) && n.community >= 0 && n.community < COMMUNITY_SLOTS)).length
  if (other) rows.push({ group: 'community-other', label: 'Other or unconnected', size: other })
  return rows
}

// The screening verdicts of Research → Screen, for one question.
export const RELEVANCE_LEGEND = [
  { group: 'relevant', label: 'Worth reading' },
  { group: 'partial', label: 'Partly relevant' },
  { group: 'not_established', label: 'Can’t tell from the text' },
  { group: 'not_relevant', label: 'Not about this' },
  { group: 'unscreened', label: 'Not screened yet' },
]

export const TYPE_LEGEND = [
  { group: 'article', label: 'Papers and documents' },
  { group: 'book', label: 'Books' },
  { group: 'outside', label: 'In the collection, not selected' },
  { group: 'cited', label: 'Cited, not in the collection' },
]

// Year groups run from faint (oldest) to full strength (newest) of one
// colour, so the scheme holds in any theme.
export function yearStrength(year, range) {
  if (!year || !range || range.min === range.max) return 0.75
  return 0.3 + 0.7 * ((year - range.min) / (range.max - range.min))
}

export function yearRange(nodes) {
  const years = nodes.map(n => n.year).filter(y => Number.isFinite(y))
  return years.length ? { min: Math.min(...years), max: Math.max(...years) } : null
}

export function shortLabel(node, max = 48) {
  const text = node.label || node.title || node.filename || ''
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

// A cited work with no verified title is labelled with the reference as
// printed; the view says so rather than presenting it as a title.
export function isAsCited(node) {
  return node?.type === 'external' && !node.title
}

// ── labels ──────────────────────────────────────────────────────────────

/**
 * Break ``text`` into at most ``maxLines`` lines no wider than ``maxWidth``
 * (``measure(text)`` returns a width). Runs with no spaces — PDF text that
 * lost them — are broken by character, so a label never runs past its box;
 * the last kept line ends in an ellipsis when text was cut.
 */
export function wrapLabel(text, maxWidth, measure, maxLines = 2) {
  const words = String(text || '').replace(/\s+/g, ' ').trim().split(' ').filter(Boolean)
  const lines = []
  let line = ''
  const breakWord = (word) => {
    let chunk = ''
    for (const ch of word) {
      if (chunk && measure(chunk + ch) > maxWidth) { lines.push(chunk); chunk = ch } else chunk += ch
    }
    return chunk
  }
  for (const word of words) {
    const candidate = line ? `${line} ${word}` : word
    if (measure(candidate) <= maxWidth) { line = candidate; continue }
    if (line) lines.push(line)
    line = measure(word) <= maxWidth ? word : breakWord(word)
    if (lines.length > maxLines) break
  }
  if (line) lines.push(line)
  if (lines.length <= maxLines) return lines
  const kept = lines.slice(0, maxLines)
  let last = kept[maxLines - 1]
  while (last.length > 1 && measure(`${last}…`) > maxWidth) last = last.slice(0, -1)
  kept[maxLines - 1] = `${last.trimEnd()}…`
  return kept
}

// Which labels speak first when they would collide: the node under the
// pointer or selected, then its neighbours, then search matches, then the
// best-connected nodes.
export function labelRank(node, { anchorId = null, focusIds = null, matchIds = null, degree = {} } = {}) {
  if (node.id === anchorId) return 0
  if (focusIds?.has(node.id)) return 1 - Math.min(degree[node.id] || 0, 99) / 100
  if (matchIds?.has(node.id)) return 2 - Math.min(degree[node.id] || 0, 99) / 100
  return 3 - Math.min(degree[node.id] || 0, 99) / 100
}

const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom

/**
 * Lay out labels in screen space, most important first. ``items`` are
 * ``{ id, x, y, w, h, force }`` with (x, y) the top-centre anchor and
 * ``force`` for a label that must show even over another. A label whose
 * node is outside ``bounds`` is dropped; the rest are nudged inside the
 * bounds, and one that would overlap a label already placed is skipped.
 */
export function placeLabels(items, bounds, pad = 4) {
  const placed = []
  for (const it of items) {
    if (it.x < bounds.left || it.x > bounds.right || it.y < bounds.top - it.h || it.y > bounds.bottom) continue
    const left = Math.min(Math.max(it.x - it.w / 2, bounds.left + pad), bounds.right - pad - it.w)
    const top = Math.min(Math.max(it.y, bounds.top + pad), bounds.bottom - pad - it.h)
    const rect = { left, top, right: left + it.w, bottom: top + it.h }
    if (!it.force && placed.some(p => overlaps(p.rect, rect))) continue
    placed.push({ ...it, rect })
  }
  return placed
}

// ── reading the map ─────────────────────────────────────────────────────

const byLabel = (a, b) => shortLabel(a.node, 200).localeCompare(shortLabel(b.node, 200))

// What a node cites and what cites it, among the nodes drawn.
export function connections(id, shown) {
  const byId = Object.fromEntries((shown?.nodes || []).map(n => [n.id, n]))
  const cites = []
  const citedBy = []
  for (const e of shown?.edges || []) {
    if (e.source === id && byId[e.target]) cites.push({ node: byId[e.target], reason: e.reason || '' })
    if (e.target === id && byId[e.source]) citedBy.push({ node: byId[e.source], reason: e.reason || '' })
  }
  return { cites: cites.sort(byLabel), citedBy: citedBy.sort(byLabel) }
}

/**
 * What the drawn map says at a glance: the sources the others cite most,
 * the works cited most that the collection lacks (what to read next), and
 * how many sources connect to nothing.
 */
export function mapInsights(shown, limit = 5) {
  const nodes = shown?.nodes || []
  const edges = shown?.edges || []
  const inDegree = {}
  const linked = new Set()
  for (const e of edges) {
    inDegree[e.target] = (inDegree[e.target] || 0) + 1
    linked.add(e.source)
    linked.add(e.target)
  }
  const rank = (list) => list
    .map(node => ({ node, count: inDegree[node.id] || 0 }))
    .filter(r => r.count > 0)
    .sort((a, b) => b.count - a.count || byLabel(a, b))
    .slice(0, limit)
  const papers = nodes.filter(n => n.type === 'paper')
  return {
    papers: papers.length,
    links: edges.length,
    hubs: rank(papers),
    missing: rank(nodes.filter(n => n.type === 'external' && !n.in_collection)),
    outside: rank(nodes.filter(n => n.type === 'external' && n.in_collection)),
    missingTotal: nodes.filter(n => n.type === 'external' && !n.in_collection).length,
    orphans: papers.filter(n => !linked.has(n.id)).length,
  }
}
