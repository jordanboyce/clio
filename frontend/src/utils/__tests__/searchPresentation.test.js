import { describe, it, expect } from 'vitest'
import { highlightSnippet, highlightTerms } from '../searchPresentation'

const marked = html => [...html.matchAll(/<mark[^>]*>(.*?)<\/mark>/g)].map(m => m[1])

describe('highlightSnippet', () => {
  it('never marks the middle of another word', () => {
    const html = highlightSnippet('Approval from the desk before the return date.', 'return deadline for field teams')
    expect(marked(html)).toEqual(['return'])
  })

  it('marks a term with a common ending', () => {
    const html = highlightSnippet('Teams returned it; the team returns it.', 'team return')
    expect(marked(html)).toEqual(['Teams', 'returned', 'team', 'returns'])
  })

  it('marks across hyphens and accents as word edges', () => {
    expect(marked(highlightSnippet('Field-team exception', 'field'))).toEqual(['Field'])
    expect(marked(highlightSnippet('Le délai de retour', 'délai'))).toEqual(['délai'])
  })

  it('leaves stopwords alone when the query has anything else', () => {
    expect(highlightTerms('For field teams, what is the deadline?')).toEqual(['field', 'teams', 'deadline'])
    // A query of nothing but stopwords still marks them.
    expect(highlightTerms('the who')).toEqual(['the', 'who'])
  })

  it('strips quotes and punctuation from query words', () => {
    expect(highlightTerms('"EQ-17" amendment?')).toEqual(['eq-17', 'amendment'])
    expect(marked(highlightSnippet('Under EQ-17, shared equipment', '"EQ-17"'))).toEqual(['EQ-17'])
  })

  it('escapes the passage, including text that looks like markup', () => {
    const html = highlightSnippet('<script>alert(1)</script> return', 'return')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&lt;script&gt;')
    expect(marked(html)).toEqual(['return'])
  })

  it('returns the escaped text when nothing in the query is worth marking', () => {
    expect(highlightSnippet('a & b', 'a b')).toBe('a &amp; b')
  })
})
