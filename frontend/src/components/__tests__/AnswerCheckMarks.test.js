import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AnswerEvidence from '../AnswerEvidence.vue'

// C4: a sentence the server found unsupported by its cited passage is marked
// beside its citation, and the mark opens that passage. Nothing else is.
const sources = [
  { filename: 'policy.pdf', page_number: 2, text_snippet: 'They must reach accounts payable within 60 days.' },
]

describe('marks on unsupported citations', () => {
  it('marks only the failing sentence and opens its passage', async () => {
    const w = mount(AnswerEvidence, {
      props: {
        content: 'Claims need two signatures [Source 1]. They are due within 90 days [Source 1].',
        sources,
        checks: [{ group: 1, sentence: 1, sources: [1], missing: ['90'], reason: 'not_in_passage' }],
        answer: true,
      },
    })
    const marks = w.findAll('.answer-unsupported')
    expect(marks).toHaveLength(1)
    expect(marks[0].text()).toBe('not found in the cited passage')
    expect(marks[0].attributes('title')).toContain('90')
    // After the second sentence's citation, not the first's.
    const html = w.html()
    expect(html.indexOf('answer-unsupported')).toBeGreaterThan(html.indexOf('90 days'))

    await marks[0].trigger('click')
    expect(w.find('details').element.open).toBe(true)
  })

  it('shows no marks while the answer is still streaming', () => {
    const w = mount(AnswerEvidence, {
      props: { content: 'Due within 90 days [Source 1].', sources, streaming: true,
        checks: [{ group: 0, sentence: 0, sources: [1], missing: ['90'], reason: 'not_in_passage' }] },
    })
    expect(w.findAll('.answer-unsupported')).toHaveLength(0)
  })
})
