import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: {
    get: vi.fn(() => Promise.resolve({ data: { documents: [] } })),
    post: vi.fn(() => Promise.resolve({ data: { results: [
      { chunk_id: 'c1', document_id: 'doc-b', filename: 'b.pdf', page_number: 1, text_snippet: 'Seven years.',
        similarity_score: 0.8, pdf_url: '', page_url: '' },
    ] } })),
  },
}))
import http from '../../utils/http'
import SearchTab from '../SearchTab.vue'
import { useStatsStore } from '../../stores/statsStore'
import { useSearchStore } from '../../stores/searchStore'
import { useSelectionStore } from '../../stores/selectionStore'

// C7: Find stays inside the sources ticked in the Sources panel, as Ask does,
// and says so above the results.
describe('Find and the source selection', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    http.post.mockClear()
  })

  async function search(selected) {
    const pinia = createPinia()
    setActivePinia(pinia)
    useStatsStore().chunks = 10
    if (selected) useSelectionStore().set(selected)
    const w = mount(SearchTab, { global: { plugins: [pinia], stubs: { SearchSettings: true, SlashCommandPicker: true } } })
    await flushPromises()
    useSearchStore().setQuery('How long are claims kept?')
    await flushPromises()
    await w.findAll('button').find(b => b.text() === 'Search').trigger('click')
    await flushPromises()
    return w
  }

  it('sends the selected sources with the search and says so', async () => {
    const w = await search(['doc-b', 'doc-a'])

    const [, body] = http.post.mock.calls.at(-1)
    expect(body.filters).toEqual({ document_ids: ['doc-a', 'doc-b'] })
    expect(w.text()).toContain('only in the 2 selected sources')
  })

  it('searches everything when nothing is selected', async () => {
    const w = await search(null)

    const [, body] = http.post.mock.calls.at(-1)
    expect(body.filters).toBeUndefined()
    expect(w.text()).not.toContain('selected source')
  })
})
