import { describe, expect, it, vi } from 'vitest'
import { answerWithCitations, citeSources, mergeInText, missingLabel, sourceCollectionId } from '../citations.js'

const cited = {
  style: 'apa',
  passages: [
    { in_text: '(Okafor et al., 2021, p. 1187)' },
    { in_text: '(Hale & Ruiz, 2018, p. 3)' },
    { in_text: '(Okafor et al., 2021, p. 1187)' },
  ],
  bibliography: ['Hale, B., & Ruiz, C. (2018). Xylem.', 'Okafor, M. (2021). Roots.'],
  bibliography_html: ['Hale, B., &amp; Ruiz, C. (2018). <i>Xylem</i>.', 'Okafor, M. (2021). <i>Roots</i>.'],
}

describe('citing an answer', () => {
  it('replaces [Source N] with real citations and adds a reference list', () => {
    const { text, html } = answerWithCitations('Roots matter [Source 1]. Rain too [Source 2] [Source 3].', cited)
    expect(text).toBe(
      'Roots matter (Okafor et al., 2021, p. 1187). Rain too (Hale & Ruiz, 2018, p. 3; Okafor et al., 2021, p. 1187).'
      + '\n\nReferences\n\nHale, B., & Ruiz, C. (2018). Xylem.\n\nOkafor, M. (2021). Roots.')
    expect(html).toContain('<h3>References</h3><p>Hale, B., &amp; Ruiz, C. (2018). <i>Xylem</i>.</p>')
  })

  it('collapses one source cited twice in a row and leaves unknown numbers alone', () => {
    expect(answerWithCitations('A [Source 1][Source 3]. B [Source 9].', cited).text.split('\n')[0])
      .toBe('A (Okafor et al., 2021, p. 1187). B [Source 9].')
  })

  it('merges author-date citations and lists IEEE numbers', () => {
    expect(mergeInText(['(A, 2020)', '(B, 2021, p. 4)'], 'apa')).toBe('(A, 2020; B, 2021, p. 4)')
    expect(mergeInText(['[1]', '[2, p. 4]'], 'ieee')).toBe('[1], [2, p. 4]')
    expect(answerWithCitations('x', { ...cited, style: 'mla' }).text).toContain('Works Cited')
  })

  it('cites sources per collection and keeps them in answer order', async () => {
    const api = {
      citations: vi.fn(async (cid, body) => ({
        passages: body.passages.map(p => ({ in_text: `${cid}:${p.document_id}:${p.page}` })),
        bibliography: [`${cid} list`], bibliography_html: [`${cid} html`],
      })),
    }
    const sources = [
      { document_id: 'a', page_number: 4, page_url: '/documents/a/pdf?collection_id=lab#page=4' },
      { document_id: 'b', page_number: 2, page_url: '/documents/b/pdf?collection_id=ops#page=2' },
      { document_id: 'c', page_number: 1 },
    ]
    const out = await citeSources(sources, 'apa', 'lab', api)
    expect(out.passages.map(p => p.in_text)).toEqual(['lab:a:4', 'ops:b:2', 'lab:c:1'])
    expect(api.citations).toHaveBeenCalledTimes(2)
    expect(out.bibliography).toEqual(['lab list', 'ops list'])
  })

  it('reads helpers', () => {
    expect(sourceCollectionId({ page_url: '/documents/x/pdf?collection_id=lab%201' })).toBe('lab 1')
    expect(missingLabel(['year', 'venue', 'container_title'])).toBe('year, journal, book title')
  })
})
