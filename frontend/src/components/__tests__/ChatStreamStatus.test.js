import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

// A fetch() answer shaped like the backend's SSE stream. With `hold`, the
// stream stays open after its events, as it does while the model thinks.
function sseResponse(events, { hold = false } = {}) {
  const encoder = new TextEncoder()
  const chunks = events.map((e) => encoder.encode(typeof e === 'string' ? e : `data: ${JSON.stringify(e)}\n\n`))
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

const doneEvent = { type: 'done', usage: {}, structured_results: [], sources: [], cached: false, related_questions: [], depth: 'quick' }

describe('What the Ask tab shows while an answer is on its way', () => {
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

  afterEach(() => {
    vi.useRealTimers()
  })

  const ask = async (question = 'How long?') => {
    const wrapper = mount(ChatTab, { global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } } })
    await flushPromises()
    const input = wrapper.get('textarea')
    await input.setValue(question)
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    return wrapper
  }

  it('says what the server is doing, and how long the step has taken', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    global.fetch.mockResolvedValue(sseResponse([
      { type: 'status', stage: 'search', text: 'Searching Operations…' },
      ': keepalive\n\n',
      { type: 'status', stage: 'model', text: 'Waiting for Anthropic (claude)…' },
    ], { hold: true }))
    const wrapper = await ask()

    const status = () => wrapper.get('[data-testid="stream-status"]').text()
    expect(status()).toBe('Waiting for Anthropic (claude)…')

    vi.advanceTimersByTime(42_000)
    await flushPromises()
    expect(status()).toBe('Waiting for Anthropic (claude)… · 42 s')
    wrapper.unmount()
  })

  it('shows a retry as a warning', async () => {
    global.fetch.mockResolvedValue(sseResponse([
      { type: 'status', stage: 'retry', text: "Anthropic didn't reply within 180 s. Trying once more…" },
    ], { hold: true }))
    const wrapper = await ask()

    const line = wrapper.get('[data-testid="stream-status"] span:last-child')
    expect(line.text()).toBe("Anthropic didn't reply within 180 s. Trying once more…")
    expect(line.classes()).toContain('text-warning')
    wrapper.unmount()
  })

  it('reports a stream that closes without an answer instead of thinking forever', async () => {
    global.fetch.mockResolvedValue(sseResponse([{ type: 'status', stage: 'model', text: 'Waiting for Anthropic…' }]))
    const wrapper = await ask()

    expect(wrapper.find('[data-testid="stream-status"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('The connection to the server closed before an answer arrived.')
    const messages = useChatStore().getMessages('default')
    expect(messages.map((m) => m.role)).toEqual(['user'])
    wrapper.unmount()
  })

  it('keeps a partial answer when the stream is cut mid-way, and says so', async () => {
    global.fetch.mockResolvedValue(sseResponse([{ type: 'text_delta', delta: 'Five working' }]))
    const wrapper = await ask()

    const last = useChatStore().getMessages('default').at(-1)
    expect(last.content).toBe('Five working')
    expect(last.streaming).toBe(false)
    expect(last.status).toBeUndefined()
    expect(wrapper.text()).toContain('may be cut short')
    wrapper.unmount()
  })

  it('a finished answer carries no status and no error', async () => {
    global.fetch.mockResolvedValue(sseResponse([
      { type: 'status', stage: 'model', text: 'Waiting for Anthropic…' },
      { type: 'text_delta', delta: 'Five days.' },
      doneEvent,
    ]))
    const wrapper = await ask()

    const last = useChatStore().getMessages('default').at(-1)
    expect(last.content).toBe('Five days.')
    expect(last.status).toBeUndefined()
    expect(wrapper.text()).not.toContain('connection')
    wrapper.unmount()
  })
})
