import { describe, expect, it } from 'vitest'
import {
  COMMUNITY_SLOTS, DEFAULT_SETTINGS, SIZE_OPTIONS, communityLegend, groupOf, metricIndex, metricRadius, nodeRadius, sanitize,
} from '../citationGraph.js'

const network = {
  nodes: [
    { id: 'a', pagerank: 0.04, betweenness: 0.3, community: 0 },
    { id: 'b', pagerank: 0.1, betweenness: 0, community: 1 },
    { id: 'lonely', pagerank: 0.01, betweenness: 0, community: null },
  ],
  communities: [
    { id: 1, label: 'Retrieval-Augmented', size: 4 },
    { id: 0, label: 'Retrieval', size: 4 },
  ],
}

describe('sizeBy and colorBy settings', () => {
  it('defaults to citations and accepts only the known measures', () => {
    expect(DEFAULT_SETTINGS.sizeBy).toBe('citations')
    expect(SIZE_OPTIONS.map(o => o.id)).toEqual(['citations', 'pagerank', 'betweenness'])
    expect(sanitize({ sizeBy: 'pagerank' }).sizeBy).toBe('pagerank')
    expect(sanitize({ sizeBy: 'betweenness' }).sizeBy).toBe('betweenness')
    expect(sanitize({ sizeBy: 'eigenvector' }).sizeBy).toBe('citations')
    expect(sanitize({ sizeBy: 7 }).sizeBy).toBe('citations')
  })

  it('accepts community colouring and still loads settings saved before it existed', () => {
    expect(sanitize({ colorBy: 'community' }).colorBy).toBe('community')
    expect(sanitize({ colorBy: 'rainbow' }).colorBy).toBe('type')
    const old = sanitize({ colorBy: 'year', nodeSize: 2 })
    expect(old.colorBy).toBe('year')
    expect(old.sizeBy).toBe('citations')
  })
})

describe('metricRadius', () => {
  const paper = { type: 'paper' }
  it('starts at the base and grows monotonically', () => {
    expect(metricRadius(paper, 0, 1)).toBe(3.4)
    expect(metricRadius({ type: 'external' }, 0, 1)).toBe(2.6)
    expect(metricRadius(paper, 0.25, 1)).toBeGreaterThan(metricRadius(paper, 0.1, 1))
    expect(metricRadius(paper, 1, 1)).toBeGreaterThan(metricRadius(paper, 0.25, 1))
  })

  it('gives the largest value about what a well-cited node gets', () => {
    const top = metricRadius(paper, 5, 5)
    expect(top).toBeCloseTo(nodeRadius(paper, 16), 5)
    expect(metricRadius(paper, 50, 5)).toBe(top)   // never beyond the top
  })

  it('never returns NaN', () => {
    for (const [v, m] of [[null, 1], [undefined, 1], [1, 0], [1, null], [1, undefined], [NaN, 1], ['x', 1], [-1, 1]]) {
      expect(metricRadius(paper, v, m)).toBe(3.4)
    }
    expect(metricRadius(paper, 1, 1, 2)).toBeCloseTo(2 * metricRadius(paper, 1, 1))
  })
})

describe('metricIndex', () => {
  it('indexes rows and finds each maximum', () => {
    const idx = metricIndex(network)
    expect(idx.byId.b.pagerank).toBe(0.1)
    expect(idx.max).toEqual({ pagerank: 0.1, betweenness: 0.3 })
  })
  it('is safe on missing data', () => {
    for (const bad of [null, undefined, {}, { nodes: null }]) {
      expect(metricIndex(bad)).toEqual({ byId: {}, max: { pagerank: 0, betweenness: 0 } })
    }
  })
})

describe('groupOf by community', () => {
  const node = { id: 'a', type: 'paper' }
  const communityOf = { a: 2, far: COMMUNITY_SLOTS, last: COMMUNITY_SLOTS - 1 }
  it('maps a community under the slot count to its slot', () => {
    expect(groupOf(node, 'community', {}, communityOf)).toBe('community-2')
    expect(groupOf({ id: 'last' }, 'community', {}, communityOf)).toBe('community-7')
  })
  it('puts the rest in one shared group', () => {
    expect(groupOf({ id: 'far' }, 'community', {}, communityOf)).toBe('community-other')
    expect(groupOf({ id: 'isolate' }, 'community', {}, communityOf)).toBe('community-other')
    expect(groupOf(node, 'community')).toBe('community-other')
  })
  it('leaves the other modes alone, with or without the fourth argument', () => {
    const external = { id: 'x', type: 'external', in_collection: false }
    expect(groupOf(node, 'type', {})).toBe('article')
    expect(groupOf(node, 'type', {}, communityOf)).toBe('article')
    expect(groupOf(external, 'type', {}, communityOf)).toBe('cited')
    expect(groupOf(node, 'none')).toBe('node')
    expect(groupOf(node, 'year')).toBe('year')
    expect(groupOf(node, 'relevance', { a: { verdict: 'relevant' } }, communityOf)).toBe('relevant')
  })
})

describe('communityLegend', () => {
  it('lists communities in order, plus a shared row only when some node needs it', () => {
    const rows = communityLegend(network)
    expect(rows.slice(0, 2)).toEqual([
      { group: 'community-0', label: 'Retrieval', size: 4 },
      { group: 'community-1', label: 'Retrieval-Augmented', size: 4 },
    ])
    expect(rows[2]).toEqual({ group: 'community-other', label: 'Other or unconnected', size: 1 })
  })

  it('has no shared row when every node has a slot', () => {
    const rows = communityLegend({ ...network, nodes: network.nodes.filter(n => n.id !== 'lonely') })
    expect(rows.map(r => r.group)).toEqual(['community-0', 'community-1'])
  })

  it('keeps to the colour slots and is safe on null', () => {
    const many = { communities: Array.from({ length: 10 }, (_, i) => ({ id: i, label: `c${i}`, size: 2 })), nodes: [] }
    expect(communityLegend(many)).toHaveLength(COMMUNITY_SLOTS)
    expect(communityLegend(null)).toEqual([])
  })
})
