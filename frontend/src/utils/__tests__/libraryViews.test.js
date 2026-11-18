import { describe, expect, it } from 'vitest'
import { librarySteps, reviewCost } from '../libraryFlow.js'
import {
  bodySections, coverageText, otherNotes, pageRanges, reviewQueue, reviewStatus, reviewSummary, uncovered,
} from '../reviewView.js'
import { SIZE_OPTIONS, bridgeWords, connections, nodeRole, panelLayout } from '../citationGraph.js'

const paper = (id, over = {}) => ({ document_id: id, analyzed: true, review_count: 0, ...over })

describe('the Library steps (R10)', () => {
  it('counts each step over the scope and says what it needs', () => {
    const papers = [paper('a', { review_count: 1 }), paper('b'), paper('c', { analyzed: false })]
    const steps = librarySteps({ papers, question: 'root depth', screened: ['a', 'zzz'], hasModel: true, canWrite: true })
    expect(steps.map(s => s.id)).toEqual(['analyze', 'screen', 'review', 'cite'])
    expect(steps[0]).toMatchObject({ done: 2, total: 3, state: 'partial', action: 'Analyze 1' })
    // Only screened sources in scope count; the rest are what Screen will cost.
    expect(steps[1]).toMatchObject({ label: 'Screened', done: 1, state: 'partial', hint: '2 model calls for the rest' })
    expect(steps[2]).toMatchObject({ done: 1, state: 'partial' })
    expect(steps[3].hint).toContain('no model')
  })

  it('says a model is needed before anyone types, and offers no action to readers', () => {
    const steps = librarySteps({ papers: [paper('a', { analyzed: false })], hasModel: false, canWrite: false })
    expect(steps[0].action).toBe('')
    expect(steps[1]).toMatchObject({ label: 'Screen', done: null, hint: 'Needs a model' })
    expect(steps[2].hint).toBe('Needs a model')
  })

  it('marks a step done when every source has it', () => {
    const steps = librarySteps({ papers: [paper('a')], question: 'q', screened: ['a'], hasModel: true })
    expect(steps[0].state).toBe('done')
    expect(steps[1]).toMatchObject({ state: 'done', hint: 'for “q”' })
  })

  it('prices a review run before it starts', () => {
    expect(reviewCost(3, 'brief')).toBe('3 model calls')
    expect(reviewCost(1, 'brief')).toBe('1 model call')
    expect(reviewCost(2, 'thorough')).toBe('up to 16 model calls')
    expect(reviewCost(0, 'brief')).toBe('')
  })
})

describe('how a review reads', () => {
  const item = (point) => ({ point, quote: point, page: 1 })
  const review = {
    depth: 'brief', rejected: 2,
    sections: [
      { key: 'tldr', title: 'In one line', items: [item('Lead.')] },
      { key: 'findings', title: 'What it finds', items: [item('One.'), item('Two.')] },
      { key: 'limitations', title: 'Limitations the authors state', items: [] },
    ],
    coverage: { passages: 3, of: 6, share: 0.62, pages: [1, 2, 4], notes: [
      '2 statement(s) were removed because their quote was not found in the source.', 'Part 3 could not be read.',
    ] },
  }

  it('leads with its one-line summary and counts what was verified', () => {
    expect(reviewSummary(review).point).toBe('Lead.')
    expect(bodySections(review).map(s => s.key)).toEqual(['findings'])
    expect(reviewStatus(review)).toMatchObject({ tone: 'mixed', label: '3 statements verified · 2 left out' })
    expect(reviewStatus({ sections: [], rejected: 4 })).toMatchObject({ tone: 'warning', label: 'Nothing could be verified' })
  })

  it('says what was not covered and how much it read, without repeating itself', () => {
    expect(uncovered(review)).toEqual(['Limitations the authors state'])
    expect(coverageText(review)).toBe('Read 3 of 6 passages (62% of the text), pages 1–2, 4')
    expect(otherNotes(review)).toEqual(['Part 3 could not be read.'])
    expect(pageRanges([3])).toBe('3')
  })

  it('queues unread reviews first, newest first within each', () => {
    const r = (id, readAt, at) => ({ id, read_at: readAt, created_at: at })
    const queue = reviewQueue([r(1, 'x', '2026-11-03'), r(2, null, '2026-11-01'), r(3, null, '2026-11-02'), r(4, 'x', '2026-11-04')])
    expect(queue.map(q => q.id)).toEqual([3, 2, 4, 1])
  })
})

describe('the map in plain words (R12)', () => {
  const shown = {
    nodes: [
      { id: 'a', type: 'paper' }, { id: 'b', type: 'paper' }, { id: 'c', type: 'paper' },
      { id: 'x', type: 'external' }, { id: 'lone', type: 'paper' },
    ],
    edges: [
      { source: 'a', target: 'b' }, { source: 'c', target: 'b' }, { source: 'a', target: 'x' }, { source: 'c', target: 'x' },
      { source: 'b', target: 'x' },
    ],
  }
  const role = (id, extra = {}) => nodeRole(shown.nodes.find(n => n.id === id), { links: connections(id, shown), shown, ...extra })
    .map(r => r.text)

  it('labels the dot sizes by what they mean', () => {
    expect(SIZE_OPTIONS.map(o => o.label)).toEqual(['Links', 'Influence', 'Bridging'])
  })

  it('says why a cited work you lack matters', () => {
    expect(role('x')).toEqual([
      '3 of your sources cite it, and it is not in your collection: a likely next read.',
      'The most cited work in this map.',
    ])
  })

  it('names a bridge and keeps the honest caveat on a small map', () => {
    const brokers = [{ id: 'b', connects: [0, 1], sentence: 'Connects A and B: cited by 1 paper in each' }]
    const lines = nodeRole(shown.nodes[1], { links: connections('b', shown), shown, brokers, reliable: false })
    expect(lines.map(l => l.key)).toEqual(['cited', 'top', 'bridge', 'caveat'])
    expect(lines[0].text).toBe('Cited by 2 of your sources.')
    expect(lines[1].text).toBe('One of the most cited works in this map.')
    expect(lines[2]).toMatchObject({ text: 'Bridges two groups that otherwise cite different work.', detail: brokers[0].sentence })
  })

  it('says when a source connects to nothing, or only cites', () => {
    expect(role('lone')).toEqual(['Cites nothing drawn here and nothing here cites it.'])
    expect(role('a')).toEqual(['Cites 2 works drawn here; none of your sources cites it.'])
  })

  it('puts a broker\'s measures in words', () => {
    expect(bridgeWords({ betweenness: 0.347, constraint: 0.17 })).toEqual([
      'On 35% of the shortest citation paths between other works in this map.', 'Its links reach several separate groups.',
    ])
    expect(bridgeWords({ betweenness: 0.004, constraint: 0.9 })).toEqual([
      'On a few of the shortest citation paths between other works in this map.', 'Its links mostly stay inside one tight group.',
    ])
    expect(bridgeWords({ betweenness: 0, constraint: null })).toEqual([])
  })

  it('turns the side panel into a bottom sheet on a phone-width map', () => {
    expect(panelLayout(343).sheet).toBe(true)
    expect(panelLayout(700)).toMatchObject({ sheet: false, docked: false })
  })
})
