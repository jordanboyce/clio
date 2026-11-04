import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import FigureStrip from '../FigureStrip.vue'
import SearchResult from '../SearchResult.vue'
import AnswerEvidence from '../AnswerEvidence.vue'
import { figureAlt, figureHref } from '../../utils/answerEvidence'

const chart = {
  url: 'https://old-host.example/documents/doc-1/figures/2?collection_id=ops',
  figure_number: 2, page_number: 12, caption: 'Figure 2: Sales by region', width: 640, height: 400,
}
const photo = {
  url: 'https://old-host.example/documents/doc-1/figures/3?collection_id=ops',
  figure_number: 3, page_number: 12, caption: null, width: 800, height: 600,
}
const source = {
  filename: 'Field manual.pdf', document_id: 'doc-1', page_number: 12, source_format: 'pdf',
  text_snippet: 'Regional totals rose in every quarter.',
  page_url: 'https://old-host.example/documents/doc-1/pdf?collection_id=ops#page=12',
  figures: [chart, photo],
}

describe('Figures on a cited page', () => {
  it('keeps figure links on this deployment and only for this document', () => {
    expect(figureHref(source, chart)).toBe('/documents/doc-1/figures/2?collection_id=ops')
    expect(figureHref(source, { ...chart, url: 'https://evil.example/documents/doc-2/figures/2' })).toBe('')
    expect(figureHref(source, { ...chart, url: '/documents/doc-1/pdf?collection_id=ops' })).toBe('')
    expect(figureHref(source, { ...chart, url: 'javascript:alert(1)' })).toBe('')
    expect(figureHref(source, { ...chart, url: null })).toBe('')
    expect(figureAlt(source, chart)).toBe('Figure 2: Sales by region')
    expect(figureAlt(source, photo)).toBe('Figure 3 on page 12 of Field manual.pdf')
  })

  it('shows each figure as a thumbnail that opens the full picture', () => {
    const wrapper = mount(FigureStrip, { props: { source } })
    const links = wrapper.findAll('a')
    expect(links).toHaveLength(2)
    expect(links[0].attributes('href')).toBe('/documents/doc-1/figures/2?collection_id=ops')
    expect(links[0].attributes('target')).toBe('_blank')
    expect(links[0].attributes('title')).toBe('Figure 2: Sales by region')
    const img = links[0].get('img')
    expect(img.attributes('src')).toBe('/documents/doc-1/figures/2?collection_id=ops')
    expect(img.attributes('alt')).toBe('Figure 2: Sales by region')
    expect(img.attributes('loading')).toBe('lazy')
    expect(wrapper.get('ul').attributes('aria-label')).toBe('Figures on page 12 of Field manual.pdf')
  })

  it('drops a figure the server cannot serve and renders nothing without figures', async () => {
    const wrapper = mount(FigureStrip, { props: { source } })
    await wrapper.findAll('img')[1].trigger('error')
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(wrapper.get('a').attributes('href')).toBe('/documents/doc-1/figures/2?collection_id=ops')

    expect(mount(FigureStrip, { props: { source: { ...source, figures: null } } }).find('ul').exists()).toBe(false)
    expect(mount(FigureStrip, { props: { source: { ...source, figures: [] } } }).find('ul').exists()).toBe(false)
    const foreign = { ...source, figures: [{ ...chart, url: 'https://evil.example/x.png' }] }
    expect(mount(FigureStrip, { props: { source: foreign } }).find('ul').exists()).toBe(false)
  })

  it('appears under the passage on a search result and in the evidence list', () => {
    const result = mount(SearchResult, { props: { result: source, index: 0, query: 'totals' } })
    const strip = result.get('ul[aria-label^="Figures on"]')
    expect(strip.findAll('img').map(i => i.attributes('src'))).toEqual([
      '/documents/doc-1/figures/2?collection_id=ops',
      '/documents/doc-1/figures/3?collection_id=ops',
    ])
    expect(mount(SearchResult, { props: { result: { ...source, figures: null }, index: 0 } })
      .find('ul[aria-label^="Figures on"]').exists()).toBe(false)

    const evidence = mount(AnswerEvidence, { props: { content: 'Totals rose. [Source 1]', sources: [source] } })
    expect(evidence.get('li ul[aria-label^="Figures on"]').findAll('a')).toHaveLength(2)
  })
})
