import { describe, expect, it } from 'vitest'
import { layoutCitations, neighborhood, matchesQuery, NODE_SIZE } from '../citationLayout.js'
import { abstractLabel, authorsShort, nextLocalTime, pageUrl, sourceLabel } from '../researchApi.js'

describe('citation map layout', () => {
  const nodes = [
    { id: 'a', type: 'paper' },
    { id: 'b', type: 'paper' },
    { id: 'c', type: 'paper' },
    { id: 'x', type: 'external' },
    { id: 'lonely', type: 'paper' },
  ]
  const edges = [{ source: 'a', target: 'b' }, { source: 'c', target: 'b' }, { source: 'a', target: 'x' }]

  it('puts citing sources left of what they cite, and is stable', () => {
    const one = layoutCitations(nodes, edges)
    expect(one).toEqual(layoutCitations(nodes, edges))
    expect(one.a.x).toBeLessThan(one.b.x)
    expect(one.c.x).toBeLessThan(one.b.x)
    expect(one.a.x).toBeLessThan(one.x.x)
  })

  it('parks unconnected sources in a grid below instead of a tall column', () => {
    const pos = layoutCitations(nodes, edges)
    const bottom = Math.max(...['a', 'b', 'c', 'x'].map(id => pos[id].y + NODE_SIZE.source.height))
    expect(pos.lonely.y).toBeGreaterThan(bottom)
    const many = Array.from({ length: 9 }, (_, i) => ({ id: `n${i}`, type: 'paper' }))
    const grid = layoutCitations(many, [], { gridColumns: 4 })
    expect(new Set(Object.values(grid).map(p => p.y)).size).toBe(3)
  })

  it('finds neighbours both ways and matches what people type', () => {
    expect([...neighborhood('b', edges)].sort()).toEqual(['a', 'b', 'c'])
    const node = { title: 'Root Hydraulics Predict Drought Survival', authors: ['Mira Okafor'], year: 2021 }
    expect(matchesQuery(node, 'okafor 2021')).toBe(true)
    expect(matchesQuery(node, 'drought hydraulics')).toBe(true)
    expect(matchesQuery(node, 'okafor 2020')).toBe(false)
    expect(matchesQuery(node, '  ')).toBe(false)
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
    expect(sourceLabel({ source: 'paper', page: 1 })).toBe('From the source, page 1')
    expect(sourceLabel({ source: 'openalex' })).toBe('From OpenAlex')
    expect(sourceLabel({ source: 'openlibrary' })).toBe('From Open Library')
    expect(sourceLabel(null)).toBe('')
    expect(abstractLabel({ source: 'paper', page: 6, kind: 'opening',
      detail: 'opening of the Preface, quoted (there is no abstract)' }))
      .toBe('Opening of the Preface, page 6 — quoted; there is no abstract')
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
