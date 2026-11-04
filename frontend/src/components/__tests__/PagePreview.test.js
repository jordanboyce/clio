import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PagePreview from '../PagePreview.vue'
import SearchResult from '../SearchResult.vue'
import AnswerEvidence from '../AnswerEvidence.vue'
import { previewAlt, previewHref } from '../../utils/answerEvidence'

const pdfSource = {
  filename: 'Field manual.pdf', document_id: 'doc-1', page_number: 12, source_format: 'pdf',
  text_snippet: 'Figure 4 shows the regional totals.',
  page_url: 'https://old-host.example/documents/doc-1/pdf?collection_id=ops#page=12',
  preview_url: 'https://old-host.example/documents/doc-1/preview?collection_id=ops&page=12',
}

describe('Page previews', () => {
  it('keeps the thumbnail on this deployment and only for this document', () => {
    expect(previewHref(pdfSource)).toBe('/documents/doc-1/preview?collection_id=ops&page=12')
    expect(previewHref({ ...pdfSource, preview_url: null })).toBe('')
    expect(previewHref({ ...pdfSource, preview_url: 'https://evil.example/documents/doc-2/preview' })).toBe('')
    expect(previewHref({ ...pdfSource, preview_url: 'javascript:alert(1)' })).toBe('')
    expect(previewAlt(pdfSource)).toBe('Page 12 of Field manual.pdf')
    expect(previewAlt({ filename: 'whiteboard.png', page_number: 1, source_format: 'png' })).toBe('Preview of whiteboard.png')
  })

  it('shows the page as a picture that opens the original at that page', () => {
    const wrapper = mount(PagePreview, { props: { source: pdfSource, href: '/documents/doc-1/pdf?collection_id=ops#page=12' } })
    const link = wrapper.get('a')
    expect(link.attributes('href')).toBe('/documents/doc-1/pdf?collection_id=ops#page=12')
    expect(link.attributes('target')).toBe('_blank')
    const img = wrapper.get('img')
    expect(img.attributes('src')).toBe('/documents/doc-1/preview?collection_id=ops&page=12')
    expect(img.attributes('alt')).toBe('Page 12 of Field manual.pdf')
    expect(img.attributes('loading')).toBe('lazy')
  })

  it('disappears when the server has no picture for the page', async () => {
    const wrapper = mount(PagePreview, { props: { source: pdfSource } })
    await wrapper.get('img').trigger('error')
    expect(wrapper.find('a').exists()).toBe(false)
    expect(mount(PagePreview, { props: { source: { ...pdfSource, preview_url: null } } }).find('a').exists()).toBe(false)
  })

  it('appears on a search result and in the evidence list, never for sources without one', () => {
    const result = mount(SearchResult, { props: { result: pdfSource, index: 0, query: 'totals' } })
    expect(result.get('img').attributes('src')).toBe('/documents/doc-1/preview?collection_id=ops&page=12')
    expect(result.text()).toContain('Open page 12')

    const plain = mount(SearchResult, { props: { result: { ...pdfSource, source_format: 'md', preview_url: null }, index: 0 } })
    expect(plain.find('img').exists()).toBe(false)

    const evidence = mount(AnswerEvidence, { props: { content: 'Totals rose. [Source 1]', sources: [pdfSource] } })
    expect(evidence.get('li img').attributes('src')).toBe('/documents/doc-1/preview?collection_id=ops&page=12')
    expect(evidence.get('li a[aria-label^="Open Field manual.pdf, page"]').attributes('href'))
      .toBe('/documents/doc-1/pdf?collection_id=ops#page=12')
  })
})
