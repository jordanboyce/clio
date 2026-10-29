import { describe, expect, it } from 'vitest'
import { layoutGraph, nodeRadius, edgePath } from '../citationLayout.js'
import { authorsShort, nextLocalTime, pageUrl, sourceLabel } from '../researchApi.js'

describe('citation map layout', () => {
  const nodes = [
    { id: 'a', type: 'paper', cited_by: 0 },
    { id: 'b', type: 'paper', cited_by: 3 },
    { id: 'c', type: 'paper', cited_by: 0 },
    { id: 'x', type: 'external', cited_by: 2 },
  ]
  const edges = [{ source: 'a', target: 'b' }, { source: 'c', target: 'b' }, { source: 'a', target: 'x' }]

  it('is deterministic and stays inside the canvas', () => {
    const one = layoutGraph(nodes, edges, { width: 400, height: 300 })
    const two = layoutGraph(nodes, edges, { width: 400, height: 300 })
    expect(one).toEqual(two)
    for (const p of Object.values(one)) {
      expect(p.x).toBeGreaterThanOrEqual(20)
      expect(p.x).toBeLessThanOrEqual(380)
      expect(p.y).toBeGreaterThanOrEqual(20)
      expect(p.y).toBeLessThanOrEqual(280)
    }
  })

  it('pulls citing papers closer than unrelated ones', () => {
    const pos = layoutGraph(nodes, edges, { width: 600, height: 600 })
    const d = (m, n) => Math.hypot(pos[m].x - pos[n].x, pos[m].y - pos[n].y)
    expect(d('a', 'b')).toBeLessThan(d('c', 'x'))
  })

  it('sizes nodes by citations and stops arrows at the rim', () => {
    expect(nodeRadius({ type: 'paper', cited_by: 4 })).toBeGreaterThan(nodeRadius({ type: 'paper', cited_by: 0 }))
    expect(nodeRadius({ type: 'external', cited_by: 0 })).toBeLessThan(nodeRadius({ type: 'paper', cited_by: 0 }))
    expect(edgePath({ x: 0, y: 0 }, { x: 100, y: 0 }, 10)).toBe('M0.0,0.0 L87.0,0.0')
  })
})

describe('research helpers', () => {
  it('formats authors the way citations are spoken', () => {
    expect(authorsShort([])).toBe('')
    expect(authorsShort(['Mira Okafor'])).toBe('Okafor')
    expect(authorsShort(['Bianca Hale', 'Carlos Ruiz'])).toBe('Hale & Ruiz')
    expect(authorsShort(['A B', 'C D', 'E F'])).toBe('B et al.')
  })

  it('says where a detail came from', () => {
    expect(sourceLabel({ source: 'paper', page: 1 })).toBe('From the paper, page 1')
    expect(sourceLabel({ source: 'openalex' })).toBe('From OpenAlex')
    expect(sourceLabel(null)).toBe('')
  })

  it('links to a page of the paper', () => {
    expect(pageUrl('lab notes', 'doc 1', 4)).toBe('/documents/doc%201/pdf?collection_id=lab%20notes#page=4')
  })

  it('schedules "at 01:00" for the next 01:00', () => {
    const evening = new Date(2026, 9, 29, 22, 30)
    const at = new Date(nextLocalTime('01:00', evening))
    expect(at.getDate()).toBe(30)
    expect(at.getHours()).toBe(1)
    const morning = new Date(2026, 9, 29, 0, 30)
    expect(new Date(nextLocalTime('01:00', morning)).getDate()).toBe(29)
    expect(nextLocalTime('nope')).toBeNull()
  })
})
