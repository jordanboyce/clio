// The citation graph's model: what is drawn, and the settings that shape it.
//
// The view (ResearchMap.vue) follows Obsidian's graph: small nodes sized by
// how connected they are, a force simulation instead of a fixed layout,
// labels that fade in as you zoom, hover to light a node's neighbourhood,
// and a settings drawer in four sections — Filters, Groups, Display, Forces —
// plus a local graph around one source. Everything here is plain data and
// functions so it can be tested without a canvas.

export const DEFAULT_SETTINGS = Object.freeze({
  // Filters
  search: '',
  showCited: true,        // works the sources cite that the collection lacks
  minCitations: 2,
  showOrphans: true,      // sources with no citation either way
  // Groups
  colorBy: 'type',        // type | year | none
  // Display
  arrows: false,
  textFade: 1.6,          // zoom level at which labels are fully visible
  nodeSize: 1,
  linkThickness: 1,
  // Forces
  centerForce: 0.1,
  repelForce: 220,
  linkForce: 0.6,
  linkDistance: 60,
  // Local graph
  localDepth: 1,
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
      out[key] = ['type', 'year', 'none'].includes(value) ? value : def
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

export function groupOf(node, colorBy) {
  if (colorBy === 'none') return 'node'
  if (colorBy === 'year') return 'year'
  if (node.type === 'external') return node.in_collection ? 'outside' : 'cited'
  return node.kind === 'book' ? 'book' : 'article'
}

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
