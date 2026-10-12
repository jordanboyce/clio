import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => {
  const http = { get: vi.fn(() => new Promise(() => {})), post: vi.fn() }
  return { default: http, http }
})

import http from '../../utils/http'
import ChatTab from '../ChatTab.vue'
import { useChatStore } from '../../stores/chatStore'
import { useCollectionStore } from '../../stores/collectionStore'
import { useStatsStore } from '../../stores/statsStore'
import { useProviderStore } from '../../stores/providerStore'

// A fetch() answer shaped like the backend's SSE stream.
function sseResponse(events) {
  const encoder = new TextEncoder()
  const chunks = events.map((e) => encoder.encode(`data: ${JSON.stringify(e)}\n\n`))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: { getReader: () => ({ read: async () => (i < chunks.length ? { done: false, value: chunks[i++] } : { done: true }) }) },
  }
}

const doneEvent = (extra = {}) => ({
  type: 'done', usage: { input_tokens: 1, output_tokens: 1, model: 'm' }, structured_results: [],
  sources: [], cached: false, related_questions: [], depth: 'quick', ...extra,
})

const sentBody = (call = 0) => JSON.parse(global.fetch.mock.calls[call][1].body)

describe('Follow-ups, depth and the chat library', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    // A browser with a key on file: chat is available, starters can be asked for.
    localStorage.setItem('ai_providers_config', JSON.stringify([{ id: 'anthropic', apiKey: 'k' }]))
    setActivePinia(createPinia())
    useCollectionStore().collections = [{ id: 'default', name: 'Operations' }]
    useStatsStore().documents = 3
    useProviderStore().loadServerProviders = vi.fn().mockResolvedValue()
    Element.prototype.scrollIntoView = vi.fn()
    window.matchMedia = vi.fn(() => ({ matches: false }))
    http.post.mockReset()
    http.post.mockResolvedValue({ data: { questions: [] } })
    global.fetch = vi.fn()
  })

  const mountChat = () => mount(ChatTab, {
    global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } },
  })

  it('asks a related question with one tap and shows the next answer’s related questions', async () => {
    useChatStore().addAssistantMessage('default', { role: 'assistant', content: 'Five days.', relatedQuestions: ['Any exceptions?'] }, [], null)
    global.fetch.mockResolvedValue(sseResponse([
      { type: 'text_delta', delta: 'No exceptions.' },
      { type: 'related', questions: ['Who approves it?'] },
      doneEvent({ related_questions: ['Who approves it?'] }),
    ]))
    const wrapper = mountChat()
    await flushPromises()

    const related = wrapper.get('[data-testid="related-questions"]')
    expect(related.text()).toContain('Related')
    await related.get('button').trigger('click')
    await flushPromises()

    expect(global.fetch).toHaveBeenCalledTimes(1)
    const body = sentBody()
    expect(body.messages.at(-1)).toEqual({ role: 'user', content: 'Any exceptions?' })
    expect(body.depth).toBe('quick')
    expect(body.related).toBe(true)

    const messages = useChatStore().getMessages('default')
    expect(messages.at(-1).content).toBe('No exceptions.')
    expect(messages.at(-1).relatedQuestions).toEqual(['Who approves it?'])
    expect(messages.at(-1).depth).toBe('quick')
    expect(wrapper.findAll('[data-testid="related-questions"]').at(-1).text()).toContain('Who approves it?')
    wrapper.unmount()
  })

  it('remembers the chosen depth and lets a quick answer be taken deeper', async () => {
    useChatStore().addMessage('default', { role: 'user', content: 'How long?' })
    useChatStore().addAssistantMessage('default', { role: 'assistant', content: 'Five days.', depth: 'quick' }, [], null)
    global.fetch.mockResolvedValue(sseResponse([{ type: 'text_delta', delta: 'Five working days.' }, doneEvent({ depth: 'research' })]))
    const wrapper = mountChat()
    await flushPromises()

    const radios = wrapper.findAll('[role="radiogroup"][aria-label="Answer depth"] [role="radio"]')
    expect(radios[0].attributes('aria-checked')).toBe('true')
    await radios[1].trigger('click')
    expect(localStorage.getItem('chat_depth')).toBe('research')
    await radios[0].trigger('click')

    await wrapper.findAll('button').find((b) => b.text() === 'Go deeper').trigger('click')
    await flushPromises()
    const body = sentBody()
    expect(body.depth).toBe('research')
    expect(body.use_cache).toBe(false)
    expect(body.messages).toEqual([{ role: 'user', content: 'How long?' }])
    const last = useChatStore().getMessages('default').at(-1)
    expect(last.content).toBe('Five working days.')
    expect(last.depth).toBe('research')
    // A research answer offers a plain regenerate, not another "deeper".
    expect(wrapper.findAll('button').some((b) => b.text() === 'Go deeper')).toBe(false)
    expect(wrapper.findAll('button').some((b) => b.text() === 'Regenerate')).toBe(true)
    wrapper.unmount()
  })

  it('suggests starter questions written from the collection and asks one on tap', async () => {
    http.post.mockResolvedValue({ data: { questions: ['What is the return deadline?', 'Who approves exceptions?'], cached: false } })
    global.fetch.mockResolvedValue(sseResponse([{ type: 'text_delta', delta: 'Five days.' }, doneEvent()]))
    const wrapper = mountChat()
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith(
      '/api/chat/starters?collection_id=default',
      { provider: 'anthropic', document_ids: null },
      expect.objectContaining({ headers: expect.any(Object) }),
    )
    expect(wrapper.text()).toContain('From your sources')
    const starter = wrapper.findAll('button').find((b) => b.text().includes('Who approves exceptions?'))
    await starter.trigger('click')
    await flushPromises()
    expect(sentBody().messages.at(-1).content).toBe('Who approves exceptions?')
    wrapper.unmount()
  })

  it('falls back to generic suggestions when starters are unavailable', async () => {
    http.post.mockRejectedValue(new Error('no model'))
    const wrapper = mountChat()
    await flushPromises()
    expect(wrapper.text()).not.toContain('From your sources')
    expect(wrapper.text()).toContain('Summarize the key points in these documents')
    wrapper.unmount()
  })

  it('renames a chat in place and exports it as Markdown with its evidence', async () => {
    const store = useChatStore()
    store.addMessage('default', { role: 'user', content: 'How long?' })
    store.addAssistantMessage('default', { role: 'assistant', content: 'Five days. [Source 1]' }, [
      { filename: 'policy.md', document_id: 'a', page_number: 1, text_snippet: 'Return in five days.', page_url: '/documents/a/pdf?collection_id=default#page=1' },
    ], null)
    const wrapper = mountChat()
    await flushPromises()

    await wrapper.get('[aria-label^="Rename chat"]').trigger('click')
    const input = wrapper.get('#chat-title-input')
    await input.setValue('Return policy')
    await input.trigger('blur')
    expect(store.getSessions('default')[0].title).toBe('Return policy')
    expect(wrapper.text()).toContain('Return policy')

    let saved = null
    global.URL.createObjectURL = vi.fn(() => 'blob:chat')
    global.URL.revokeObjectURL = vi.fn()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () { saved = this })
    await wrapper.get('[aria-label="Export this chat as Markdown"]').trigger('click')
    expect(saved.download).toBe('return-policy.md')
    const blob = global.URL.createObjectURL.mock.calls[0][0]
    const text = await blob.text()
    expect(text).toContain('# Return policy')
    expect(text).toContain('## How long?')
    expect(text).toContain('[Source 1] policy.md')
    click.mockRestore()
    wrapper.unmount()
  })
})
