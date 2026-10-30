import { describe, expect, it } from 'vitest'
import { filterReviewItems, injectionBadge, injectionEvidence, priorityBadge, reviewSummaryText } from '../governance'

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
    expect(reviewSummaryText({ held: 2, flagged: 0, injection: 1, reports: 3 }))
      .toBe('2 held · 1 injection · 3 reported')
    expect(reviewSummaryText({})).toBe('')
    expect(reviewSummaryText(null)).toBe('')
  })

  it('maps priorities to badges and unknowns to low', () => {
    expect(priorityBadge('critical').cls).toContain('badge-error')
    expect(priorityBadge('whatever').text).toBe('low')
  })

  it('keeps reviewed and low-confidence injection notes quiet', () => {
    const warnings = { '1': { findings: [{ severity: 'low' }], risk_score: 0.15 } }
    expect(injectionBadge({ injection_warnings: warnings }).text).toBe('note')
    expect(injectionBadge({ injection_warnings: warnings }).cls).toBe('badge-ghost')
    expect(injectionBadge({ injection_warnings: { '2': { findings: [{ severity: 'high' }] } } }).text).toBe('injection')
    expect(injectionBadge({ injection_warnings: warnings, injection_review: 'dismissed' }).text).toBe('reviewed')
    expect(injectionBadge({ injection_warnings: null })).toBeNull()
  })

  it('turns stored injection evidence into readable rows', () => {
    const rows = injectionEvidence({ top: [{ page: '2', category: 'instruction_override', severity: 'high', matched_text: 'x' }] })
    expect(rows[0].category).toBe('instruction override')
    expect(injectionEvidence(null)).toEqual([])
  })
})

import { categoryLabel, instructionTextNote } from '../governance'

describe('scanner category labels', () => {
  it('names personal data and keeps the rest readable', () => {
    expect(categoryLabel('pii')).toBe('personal data')
    expect(categoryLabel('instruction_override')).toBe('instruction override')
    expect(categoryLabel('')).toBe('')
  })
})

describe('instruction-like passages', () => {
  it('is quiet for ordinary passages', () => {
    expect(instructionTextNote({ text_snippet: 'The trustee shall act as a fiduciary.' })).toBeNull()
    expect(instructionTextNote(null)).toBeNull()
  })

  it('labels a marked passage as quoted data, strongest first', () => {
    const note = instructionTextNote({
      instruction_text: { level: 'high', categories: ['instruction_override', 'exfiltration'], excerpts: ['ignore all previous instructions'], note: 'x' },
    })
    expect(note.text).toBe('Instruction-like text')
    expect(note.cls).toBe('badge-warning')
    expect(note.categories).toEqual(['instruction override', 'exfiltration'])
    expect(note.title).toContain('quoted from the source')
    expect(note.excerpts).toEqual(['ignore all previous instructions'])
    expect(instructionTextNote({ instruction_text: { level: 'medium', categories: [] } }).text).toBe('Possible instruction-like text')
  })
})
