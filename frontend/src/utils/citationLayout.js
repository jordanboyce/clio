// A small, dependency-free force layout for the citation map.
//
// Deterministic: initial positions come from a seeded spiral, not
// Math.random, so the same library draws the same map every time and a
// person's mental picture of "where that paper sits" survives a reload.
// Forces: every pair repels, every citation pulls its two ends together,
// and a weak gravity keeps disconnected papers from drifting off.

export function layoutGraph(nodes, edges, { width = 800, height = 600, iterations = 300 } = {}) {
  const n = nodes.length
  const index = new Map(nodes.map((node, i) => [node.id, i]))
  const cx = width / 2
  const cy = height / 2
  const golden = Math.PI * (3 - Math.sqrt(5))
  const spread = Math.min(width, height) * 0.42
  const pos = nodes.map((_, i) => {
    const r = spread * Math.sqrt((i + 0.5) / Math.max(n, 1))
    return { x: cx + r * Math.cos(i * golden), y: cy + r * Math.sin(i * golden), vx: 0, vy: 0 }
  })
  const links = edges
    .map(e => [index.get(e.source), index.get(e.target)])
    .filter(([a, b]) => a !== undefined && b !== undefined && a !== b)

  const area = width * height
  const k = Math.sqrt(area / Math.max(n, 1)) * 0.6   // ideal edge length
  for (let step = 0; step < iterations; step++) {
    const cooling = 1 - step / iterations
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = pos[i].x - pos[j].x
        let dy = pos[i].y - pos[j].y
        let d2 = dx * dx + dy * dy
        if (d2 < 0.01) { dx = 0.1 * (i - j); dy = 0.1; d2 = dx * dx + dy * dy }
        const d = Math.sqrt(d2)
        const force = (k * k) / d
        const fx = (dx / d) * force
        const fy = (dy / d) * force
        pos[i].vx += fx; pos[i].vy += fy
        pos[j].vx -= fx; pos[j].vy -= fy
      }
    }
    for (const [a, b] of links) {
      const dx = pos[a].x - pos[b].x
      const dy = pos[a].y - pos[b].y
      const d = Math.max(Math.sqrt(dx * dx + dy * dy), 0.01)
      const force = (d * d) / k
      const fx = (dx / d) * force
      const fy = (dy / d) * force
      pos[a].vx -= fx; pos[a].vy -= fy
      pos[b].vx += fx; pos[b].vy += fy
    }
    const limit = Math.max(k * cooling, 1)
    for (const p of pos) {
      p.vx += (cx - p.x) * 0.02
      p.vy += (cy - p.y) * 0.02
      const v = Math.sqrt(p.vx * p.vx + p.vy * p.vy)
      if (v > limit) { p.vx = (p.vx / v) * limit; p.vy = (p.vy / v) * limit }
      p.x = Math.min(width - 20, Math.max(20, p.x + p.vx))
      p.y = Math.min(height - 20, Math.max(20, p.y + p.vy))
      p.vx = 0; p.vy = 0
    }
  }
  return Object.fromEntries(nodes.map((node, i) => [node.id, { x: pos[i].x, y: pos[i].y }]))
}

export function nodeRadius(node) {
  const base = node.type === 'external' ? 5 : 7
  return base + Math.min(10, Math.sqrt(node.cited_by || 0) * 3)
}

// Shorten an edge so its arrowhead stops at the target circle's rim.
export function edgePath(from, to, targetRadius) {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const d = Math.sqrt(dx * dx + dy * dy) || 1
  const ex = to.x - (dx / d) * (targetRadius + 3)
  const ey = to.y - (dy / d) * (targetRadius + 3)
  return `M${from.x.toFixed(1)},${from.y.toFixed(1)} L${ex.toFixed(1)},${ey.toFixed(1)}`
}
