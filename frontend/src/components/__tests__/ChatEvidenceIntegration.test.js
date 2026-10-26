import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import ChatTab from '../ChatTab.vue'
import { useChatStore } from '../../stores/chatStore'
import { useCollectionStore } from '../../stores/collectionStore'
import { useSelectionStore } from '../../stores/selectionStore'
import { useStatsStore } from '../../stores/statsStore'
import { useProviderStore } from '../../stores/providerStore'

describe('Evidence in the research workspace', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    setActivePinia(createPinia())
    useCollectionStore().collections = [{ id: 'default', name: 'Operations' }]
    useStatsStore().documents = 3
    useStatsStore().loaded = true
    useProviderStore().loadServerProviders = vi.fn().mockResolvedValue()
    Element.prototype.scrollIntoView = vi.fn()
    window.matchMedia = vi.fn(() => ({ matches: false }))
  })

  const mountChat = () => mount(ChatTab, {
    global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } },
  })

  it('shows effective scope even without a source selection', async () => {
    const wrapper = mountChat()
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('Operations · All 3 sources')
    wrapper.unmount()
  })

  it('offers a direct return from all collections to selected sources', async () => {
    localStorage.setItem('chat_scope', 'all')
    useSelectionStore().set(['a'])
    const wrapper = mountChat()
    await flushPromises()
    expect(wrapper.get('[role="status"]').text()).toContain('Your source selection does not apply')
    await wrapper.findAll('button').find(button => button.text() === 'Use selection').trigger('click')
    expect(wrapper.get('[role="status"]').text()).toContain('selected source')
    expect(localStorage.getItem('chat_scope')).toBe('current')
    wrapper.unmount()
  })

  it('keeps completed activity secondary, and copies the answer with evidence', async () => {
    const writeText = vi.fn().mockResolvedValue()
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    useChatStore().addAssistantMessage('default', { role: 'assistant', content: 'Five days. [Source 1]' }, [
      { filename: 'policy.md', document_id: 'a', page_number: 1, text_snippet: 'Return in five days.', page_url: '/documents/a/pdf?collection_id=default#page=1' },
    ], null, [{ tool: 'search_documents', result: { results: [], total_results: 0 } }])
    const wrapper = mountChat()
    await flushPromises()
    const activity = wrapper.findAll('details').find(details => details.text().includes('Research activity'))
    expect(activity.element.open).toBe(false)
    await wrapper.get('button[data-source-number="1"]').trigger('click')
    expect(wrapper.findAll('details').find(details => details.text().includes('Review evidence')).element.open).toBe(true)
    await wrapper.get('[aria-label="Copy answer with evidence to clipboard"]').trigger('click')
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining('[Source 1] policy.md'))
    wrapper.unmount()
  })

  it('shows research coverage and keeps partial search failures visible', async () => {
    useChatStore().addAssistantMessage('default', { role: 'assistant', content: 'Evidence is incomplete.' }, [], null, [
      { tool: 'research_documents', args: { query: 'Return policy' }, result: {
        total_results: 0, results: [], partial_failure: true,
        coverage: [{ query: 'Exceptions', evidence_ids: [], status: 'search_failed' }],
      } },
    ])
    const wrapper = mountChat()
    await flushPromises()
    const activity = wrapper.findAll('details').find(details => details.text().includes('Research activity'))
    expect(activity.element.open).toBe(true)
    expect(activity.text()).toContain('Some steps failed')
    expect(activity.text()).toContain('Exceptions · Search failed')
    expect(activity.text()).not.toContain('NaN')
    wrapper.unmount()
  })
})
