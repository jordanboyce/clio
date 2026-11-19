import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AnswerEvidence from '../AnswerEvidence.vue'
import { answerWithReferences, renderEvidenceMarkdown, sourceHref } from '../../utils/answerEvidence'

const source = {
  filename: 'Equipment policy.pdf', document_id: 'doc-1', page_number: 3,
  text_snippet: 'Equipment must be returned within five working days.',
  page_url: 'https://old-host.example/documents/doc-1/pdf?collection_id=operations#page=3',
}

describe('Answer evidence review', () => {
  it('opens the cited passage in place and moves keyboard focus to it', async () => {
    const wrapper = mount(AnswerEvidence, {
      props: { content: 'Return it within five days. [Source 1]', sources: [source] },
      attachTo: document.body,
    })
    expect(wrapper.get('details').element.open).toBe(false)
    await wrapper.get('button[data-source-number="1"]').trigger('click')
    expect(wrapper.get('details').element.open).toBe(true)
    expect(document.activeElement).toBe(wrapper.get('li').element)
    expect(wrapper.get('li').text()).toContain(source.text_snippet)
    expect(wrapper.get('li a').attributes('href')).toBe('/documents/doc-1/pdf?collection_id=operations#page=3')
    wrapper.unmount()
  })

  it('lists every source as a numbered chip that opens its passage', async () => {
    const sources = Array.from({ length: 10 }, (_, i) => ({
      ...source, document_id: `doc-${i + 1}`, page_number: i + 1, text_snippet: `Passage ${i + 1}`,
    }))
    const wrapper = mount(AnswerEvidence, { props: { content: 'Answer without inline citations.', sources }, attachTo: document.body })
    const strip = wrapper.get('[aria-label="Sources"]')
    const chips = strip.findAll('button[data-source-chip]')
    expect(chips).toHaveLength(8)
    expect(chips[2].text()).toContain('3')
    expect(chips[2].text()).toContain('p.3')
    expect(chips[2].attributes('title')).toBe('Passage 3')
    expect(strip.text()).toContain('+2 more')
    // Chips are not citations: the citation control count is untouched.
    expect(wrapper.findAll('button[data-source-number]')).toHaveLength(0)

    await chips[2].trigger('click')
    expect(wrapper.get('details').element.open).toBe(true)
    expect(document.activeElement.textContent).toContain('Passage 3')
    wrapper.unmount()
  })

  it('hides the source strip while the answer is still streaming', () => {
    const wrapper = mount(AnswerEvidence, { props: { content: 'Partial', sources: [source], streaming: true } })
    expect(wrapper.find('[aria-label="Sources"]').exists()).toBe(false)
  })

  it('does not manufacture evidence for invalid citations or code examples', () => {
    const wrapper = mount(AnswerEvidence, { props: {
      content: '[Source 8] and `[Source 1]` and [Source 1]', sources: [source],
    } })
    expect(wrapper.findAll('button[data-source-number]')).toHaveLength(1)
    expect(wrapper.get('code').text()).toBe('[Source 1]')
    expect(wrapper.text()).toContain('[Source 8]')
  })

  it('links the citation shapes models actually emit, without inventing evidence', () => {
    const sources = [source, { ...source, filename: 'Field manual.pdf', document_id: 'doc-2', page_number: 56 }]
    const html = renderEvidenceMarkdown(
      'Linked on save [Source 2: Field manual.pdf, page 56]. Both agree [Source 1, Source 2]. Unknown [Source 1, Source 9] and bare [2].',
      sources,
    )
    const root = document.createElement('div')
    root.innerHTML = html
    const buttons = [...root.querySelectorAll('button[data-source-number]')]
    expect(buttons.map(b => b.dataset.sourceNumber)).toEqual(['2', '1', '2'])
    expect(buttons[0].textContent).toBe('[Source 2]')
    expect(buttons[0].title).toBe('[Source 2: Field manual.pdf, page 56]')
    expect(root.textContent).toContain('[Source 1, Source 9]')
    expect(root.textContent).toContain('bare [2]')
  })

  it('removes executable markup and model-authored citation controls', () => {
    const html = renderEvidenceMarkdown('<img src=x onerror="alert(1)"><button data-source-number="99">Fake</button> [Source 1]', [source])
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('data-source-number="99"')
    expect(html).toContain('data-source-number="1"')
    expect(sourceHref({ ...source, page_url: 'javascript:alert(1)' })).toBe('')
    expect(sourceHref({ ...source, page_url: 'https://elsewhere.example/collect' })).toBe('')
  })

  it('copies the answer with matching source numbers, excerpts, and current-host links', () => {
    const copied = answerWithReferences({ content: 'Five days. [Source 1]', sources: [source] })
    expect(copied).toContain('[Source 1] Equipment policy.pdf, page / section 3')
    expect(copied).toContain(source.text_snippet)
    expect(copied).not.toContain('old-host.example')
    expect(copied).toContain('collection_id=operations#page=3')
  })

  it('distinguishes missing document passages from verified evidence and streaming', async () => {
    const wrapper = mount(AnswerEvidence, { props: { content: 'An answer', streaming: true } })
    expect(wrapper.text()).not.toContain('No passages attached')
    await wrapper.setProps({ streaming: false })
    expect(wrapper.text()).toContain('No passages attached')
  })
})
