import { describe, expect, it } from 'vitest'
import { filterReviewItems, injectionEvidence, priorityBadge, reviewSummaryText } from '../governance'

const items = [
  { document_id: 'a', kinds: ['policy'], policy_status: 'quarantined' },
  { document_id: 'b', kinds: ['policy'], policy_status: 'flagged' },
  { document_id: 'c', kinds: ['injection'], policy_status: 'clear' },
  { document_id: 'd', kinds: ['policy', 'report'], policy_status: 'flagged' },
]

describe('review queue helpers', () => {
  it('filters by why a document is in the queue', () => {
    const ids = (f) => filterReviewItems(items, f).map((i) => i.document_id)
    expect(ids('all')).toEqual(['a', 'b', 'c', 'd'])
    expect(ids('held')).toEqual(['a'])
    expect(ids('flagged')).toEqual(['b', 'd'])
    expect(ids('injection')).toEqual(['c'])
    expect(ids('report')).toEqual(['d'])
    expect(ids('nonsense')).toEqual(['a', 'b', 'c', 'd'])
  })

  it('describes the queue on one line and skips zero counts', () => {
    expect(reviewSummaryText({ held: 2, flagged: 0, injection: 1, reports: 3 }, 1))
      .toBe('2 held · 1 injection · 3 reported · 1 access request')
    expect(reviewSummaryText({}, 2)).toBe('2 access requests')
    expect(reviewSummaryText(null)).toBe('')
  })

  it('maps priorities to badges and unknowns to low', () => {
    expect(priorityBadge('critical').cls).toContain('badge-error')
    expect(priorityBadge('whatever').text).toBe('low')
  })

  it('turns stored injection evidence into readable rows', () => {
    const rows = injectionEvidence({ top: [{ page: '2', category: 'instruction_override', severity: 'high', matched_text: 'x' }] })
    expect(rows[0].category).toBe('instruction override')
    expect(injectionEvidence(null)).toEqual([])
  })
})
