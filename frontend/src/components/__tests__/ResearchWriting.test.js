import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), patch: vi.fn() } }))
import http from '../../utils/http'
import ResearchWriting from '../ResearchWriting.vue'

const TEXT = 'Okafor et al. (2021) found “conductance did predict growth rate” (p. 2). Roots matter.'

const checkReply = {
  summary: { sentences: 2, citations: 1, citations_resolved: 1, quotes: 1, quotes_verified: 0, quotes_failed: 1,
    claims: 0, claims_judged: 0, claims_supported: 0, suggestions: 0, errors: 1, warnings: 0, reference_entries: 0 },
  findings: [{
    id: 1, kind: 'quote_altered', severity: 'error', start: 36, end: 73, document_id: 'd1', page: 2,
    message: 'Not an exact quote: the source says “not” and the quote drops it.',
    source_text: 'Conductance did not predict growth rate.',
  }],
  sentences: [{ index: 0, start: 0, end: 82, severity: 'error', findings: [1] }, { index: 1, start: 83, end: 96, severity: null, findings: [] }],
  references: [],
  bibliography: [{ document_id: 'd1', text: 'Okafor, M. (2021). Roots.', html: 'Okafor, M. (2021). <i>Roots</i>.', missing: [] }],
  style: 'apa', notes: [], sources: { d1: { filename: 'paper-a.pdf' } },
}

let wrapper
beforeEach(() => {
  setActivePinia(createPinia())
  http.post.mockReset()
  http.post.mockImplementation(async (url, body) => {
    if (url.startsWith('/api/research/check-draft')) return { data: { ...checkReply, _body: body } }
    return { data: {
      entries: [{ document_id: 'd1', missing: ['year'] }], bibliography: ['Okafor, M. (n.d.). Roots.'],
      bibliography_html: ['Okafor, M. (n.d.). <i>Roots</i>.'], passages: [],
    } }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null })

function mountIt(props = {}) {
  wrapper = mount(ResearchWriting, {
    props: { collectionId: 'lab', papers: [{ document_id: 'd1', title: 'Roots', filename: 'paper-a.pdf' }],
      selectedIds: [], providerId: null, ...props },
  })
  return wrapper
}

describe('Cite & check', () => {
  it('checks a draft without a model and shows what the source says', async () => {
    const w = mountIt()
    await flushPromises()
    await w.get('[data-testid="draft-input"]').setValue(TEXT)
    await w.get('[data-testid="draft-check"]').trigger('click')
    await flushPromises()
    const [, body] = http.post.mock.calls.find(([url]) => url.startsWith('/api/research/check-draft'))
    expect(body.provider).toBeNull()
    expect(body.document_ids).toBeNull()
    expect(w.get('[data-testid="draft-findings"]').text()).toContain('Conductance did not predict growth rate.')
    // The flagged sentence is marked in the draft and selecting it filters the list.
    const marked = w.get('[data-testid="draft-annotated"] button')
    expect(marked.text()).toContain('conductance did predict growth rate')
    await marked.trigger('click')
    expect(w.findAll('[data-testid="draft-findings"] li')).toHaveLength(1)
  })

  it('builds the reference list for the scope and flags what is missing', async () => {
    const w = mountIt({ selectedIds: ['d1'] })
    await new Promise(r => setTimeout(r, 200))
    await flushPromises()
    const call = http.post.mock.calls.find(([url]) => url.startsWith('/api/research/citations'))
    expect(call[1].document_ids).toEqual(['d1'])
    const bib = w.get('[data-testid="research-bibliography"]')
    expect(bib.html()).toContain('<i>Roots</i>')
    expect(bib.text()).toContain('1 source lacks details')
  })
})
