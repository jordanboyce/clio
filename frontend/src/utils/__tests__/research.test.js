import { describe, expect, it } from 'vitest'
import {
  DEFAULT_SETTINGS, SLIDERS, groupOf, labelAlpha, localIds, matchesQuery, nodeRadius, sanitize, visibleGraph,
  yearRange, yearStrength,
} from '../citationGraph.js'
import { abstractLabel, authorsShort, nextLocalTime, pageUrl, sourceLabel } from '../researchApi.js'

describe('citation graph model', () => {
  const graph = {
    nodes: [
      { id: 'a', type: 'paper', title: 'Root Hydraulics Predict Drought Survival', authors: ['Mira Okafor'], year: 2021 },
      { id: 'b', type: 'paper', kind: 'book', title: 'The Quiet Forest', year: 2014 },
      { id: 'c', type: 'paper', year: 2018 },
      { id: 'x', type: 'external', in_collection: false },
      { id: 'lonely', type: 'paper' },
    ],
    edges: [{ source: 'a', target: 'b' }, { source: 'c', target: 'b' }, { source: 'a', target: 'x', external: true }],
  }

  it('filters cited works and orphans like Obsidian', () => {
    const all = visibleGraph(graph, { ...DEFAULT_SETTINGS })
    expect(all.nodes.map(n => n.id)).toEqual(['a', 'b', 'c', 'x', 'lonely'])
    expect(all.degree).toEqual({ a: 2, b: 2, c: 1, x: 1 })
    const noCited = visibleGraph(graph, { ...DEFAULT_SETTINGS, showCited: false })
    expect(noCited.nodes.map(n => n.id)).not.toContain('x')
    expect(noCited.edges).toHaveLength(2)
    const noOrphans = visibleGraph(graph, { ...DEFAULT_SETTINGS, showOrphans: false })
    expect(noOrphans.nodes.map(n => n.id)).not.toContain('lonely')
  })

  it('builds a local graph to the requested depth', () => {
    expect([...localIds('c', graph.edges, 1)].sort()).toEqual(['b', 'c'])
    expect([...localIds('c', graph.edges, 2)].sort()).toEqual(['a', 'b', 'c'])
    expect([...localIds('c', graph.edges, 3)].sort()).toEqual(['a', 'b', 'c', 'x'])
    const local = visibleGraph(graph, { ...DEFAULT_SETTINGS, localDepth: 1 }, 'a')
    expect(local.nodes.map(n => n.id).sort()).toEqual(['a', 'b', 'x'])
  })

  it('sizes nodes by links and fades labels in with zoom', () => {
    expect(nodeRadius({ type: 'paper' }, 4)).toBeGreaterThan(nodeRadius({ type: 'paper' }, 0))
    expect(nodeRadius({ type: 'paper' }, 1, 2)).toBeCloseTo(2 * nodeRadius({ type: 'paper' }, 1))
    expect(labelAlpha(0.2, 0.9)).toBe(0)
    expect(labelAlpha(0.9, 0.9)).toBe(1)
    expect(labelAlpha(0.7, 0.9)).toBeGreaterThan(0)
  })

  it('groups by type or year', () => {
    expect(groupOf(graph.nodes[0], 'type')).toBe('article')
    expect(groupOf(graph.nodes[1], 'type')).toBe('book')
    expect(groupOf(graph.nodes[3], 'type')).toBe('cited')
    expect(groupOf({ type: 'external', in_collection: true }, 'type')).toBe('outside')
    const range = yearRange(graph.nodes)
    expect(range).toEqual({ min: 2014, max: 2021 })
    expect(yearStrength(2014, range)).toBeCloseTo(0.3)
    expect(yearStrength(2021, range)).toBeCloseTo(1)
  })

  it('matches what people type and keeps settings in range', () => {
    expect(matchesQuery(graph.nodes[0], 'okafor 2021')).toBe(true)
    expect(matchesQuery(graph.nodes[0], 'okafor 2020')).toBe(false)
    expect(matchesQuery(graph.nodes[0], ' ')).toBe(false)
    const clean = sanitize({ repelForce: 99999, colorBy: 'rainbow', arrows: 1, nodeSize: 'big' })
    expect(clean.repelForce).toBe(SLIDERS.repelForce.max)
    expect(clean.colorBy).toBe('type')
    expect(clean.arrows).toBe(true)
    expect(clean.nodeSize).toBe(DEFAULT_SETTINGS.nodeSize)
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
