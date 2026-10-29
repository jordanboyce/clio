import dagre from '@dagrejs/dagre'

// Layout for the citation map (drawn with Vue Flow).
//
// Citations are close to a hierarchy — a work cites older works — so a
// layered layout (dagre) reads far better than a force layout: citing
// sources on the left, what they cite to the right, edges mostly flowing one
// way and rarely crossing. Sources with no citation either way would form one
// tall column in the first layer; they go in a compact grid underneath
// instead, where they cost no width.

export const NODE_SIZE = {
  source: { width: 232, height: 68 },
  external: { width: 200, height: 56 },
}

export function layoutCitations(nodes, edges, { direction = 'LR', gridColumns = 4 } = {}) {
  const linked = new Set()
  for (const e of edges) { linked.add(e.source); linked.add(e.target) }
  const connected = nodes.filter(n => linked.has(n.id))
  const loose = nodes.filter(n => !linked.has(n.id))

  const g = new dagre.graphlib.Graph({ multigraph: false })
  g.setGraph({ rankdir: direction, nodesep: 18, ranksep: 90, marginx: 20, marginy: 20 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of connected) g.setNode(n.id, { ...sizeOf(n) })
  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target) && e.source !== e.target) g.setEdge(e.source, e.target)
  }
  if (connected.length) dagre.layout(g)

  const positions = {}
  let maxY = 0
  for (const n of connected) {
    const p = g.node(n.id)
    const { width, height } = sizeOf(n)
    // dagre gives centres; Vue Flow wants the top-left corner.
    positions[n.id] = { x: p.x - width / 2, y: p.y - height / 2 }
    maxY = Math.max(maxY, p.y + height / 2)
  }
  const top = connected.length ? maxY + 60 : 20
  const cell = { width: NODE_SIZE.source.width + 18, height: NODE_SIZE.source.height + 18 }
  loose.forEach((n, i) => {
    positions[n.id] = { x: 20 + (i % gridColumns) * cell.width, y: top + Math.floor(i / gridColumns) * cell.height }
  })
  return positions
}

function sizeOf(node) {
  return node.type === 'external' ? NODE_SIZE.external : NODE_SIZE.source
}

// A node and everything one citation away from it, either direction.
export function neighborhood(id, edges) {
  const ids = new Set([id])
  for (const e of edges) {
    if (e.source === id) ids.add(e.target)
    if (e.target === id) ids.add(e.source)
  }
  return ids
}

// Case-insensitive match on what a person would type: title words, an
// author's surname, a year.
export function matchesQuery(node, query) {
  const q = String(query || '').trim().toLowerCase()
  if (!q) return false
  const hay = [node.title, node.label, node.filename, (node.authors || []).join(' '), node.year]
    .map(v => String(v || '').toLowerCase()).join(' ')
  return q.split(/\s+/).every(term => hay.includes(term))
}
