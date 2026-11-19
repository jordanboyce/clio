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

function sseResponse(events, { hold = false } = {}) {
  const encoder = new TextEncoder()
  const chunks = events.map((e) => encoder.encode(`data: ${JSON.stringify(e)}\n\n`))
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: () => (i < chunks.length
          ? Promise.resolve({ done: false, value: chunks[i++] })
          : hold ? new Promise(() => {}) : Promise.resolve({ done: true })),
      }),
    },
  }
}

const done = { type: 'done', usage: {}, structured_results: [], sources: [], cached: false, related_questions: [], depth: 'quick' }
const answer = (text) => sseResponse([{ type: 'text_delta', delta: text }, done])
const unreachable = () => Promise.reject(new TypeError('Failed to fetch'))

describe('Ask keeps the question and the answer through failures and tab switches (C2, C3)', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
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

  const mountChat = async () => {
    const wrapper = mount(ChatTab, { global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } } })
    await flushPromises()
    return wrapper
  }

  const ask = async (wrapper, question) => {
    const input = wrapper.get('textarea')
    await input.setValue(question)
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
  }

  const thread = () => useChatStore().getMessages('default').map(m => [m.role, m.content])

  it('a failed answer keeps the question, and Retry answers it without asking it twice', async () => {
    global.fetch.mockImplementationOnce(unreachable)
    const wrapper = await mountChat()
    await ask(wrapper, 'How long are claims kept?')

    expect(thread()).toEqual([['user', 'How long are claims kept?']])
    expect(wrapper.find('[role="alert"]').exists()).toBe(true)

    global.fetch.mockResolvedValueOnce(answer('Seven years.'))
    await wrapper.get('[data-testid="chat-retry"]').trigger('click')
    await flushPromises()

    expect(thread()).toEqual([['user', 'How long are claims kept?'], ['assistant', 'Seven years.']])
    const sent = JSON.parse(global.fetch.mock.calls[1][1].body).messages
    expect(sent).toEqual([{ role: 'user', content: 'How long are claims kept?' }])
    expect(wrapper.find('[data-testid="chat-retry"]').exists()).toBe(false)
  })

  it('a regenerate that fails keeps the old answer', async () => {
    global.fetch.mockResolvedValueOnce(answer('Seven years.'))
    const wrapper = await mountChat()
    await ask(wrapper, 'How long are claims kept?')
    global.fetch.mockImplementationOnce(unreachable)

    await wrapper.get('[aria-label="Regenerate this answer"]').trigger('click')
    await flushPromises()

    expect(thread()).toEqual([['user', 'How long are claims kept?'], ['assistant', 'Seven years.']])
    global.fetch.mockResolvedValueOnce(answer('Ten years.'))
    await wrapper.get('[data-testid="chat-retry"]').trigger('click')
    await flushPromises()
    expect(thread()).toEqual([['user', 'How long are claims kept?'], ['assistant', 'Ten years.']])
  })

  it('leaving Ask does not stop the answer, and coming back shows it', async () => {
    global.fetch.mockResolvedValueOnce(sseResponse([{ type: 'text_delta', delta: 'Seven' }], { hold: true }))
    const first = await mountChat()
    await ask(first, 'How long are claims kept?')
    const signal = global.fetch.mock.calls[0][1].signal

    first.unmount()

    expect(signal.aborted).toBe(false)
    const again = await mountChat()
    expect(again.text()).toContain('Seven')
    expect(again.find('[aria-label="Stop generating"]').exists()).toBe(true)
    await again.get('[aria-label="Stop generating"]').trigger('click')
    expect(signal.aborted).toBe(true)
  })

  it('an unsent question is still there after a tab switch', async () => {
    const first = await mountChat()
    await first.get('textarea').setValue('Who approves travel?')
    first.unmount()

    const again = await mountChat()
    expect(again.get('textarea').element.value).toBe('Who approves travel?')
  })

  it('an answer saved mid-stream reads as interrupted after a reload', async () => {
    localStorage.setItem('clio_chat_history_v2', JSON.stringify({
      default: { activeSessionId: 's1', sessions: [{ id: 's1', title: 'x', messages: [
        { id: 'm1', role: 'user', content: 'How long?' },
        { id: 'm2', role: 'assistant', content: 'Sev', streaming: true },
      ] }] },
    }))
    setActivePinia(createPinia())
    useCollectionStore().collections = [{ id: 'default', name: 'Operations' }]
    useStatsStore().documents = 3
    useProviderStore().loadServerProviders = vi.fn().mockResolvedValue()

    const wrapper = await mountChat()

    expect(useChatStore().getMessages('default')[1].streaming).toBe(false)
    expect(wrapper.find('[data-testid="answer-interrupted"]').exists()).toBe(true)
  })
})
