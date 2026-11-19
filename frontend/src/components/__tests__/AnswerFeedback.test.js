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

// A3: "Helpful" and "Something is wrong" under an answer send the verdict, the
// cited sources and the question to this server, never the answer text.
describe('feedback on an answer', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('ai_providers_config', JSON.stringify([{ id: 'anthropic', apiKey: 'k' }]))
    localStorage.setItem('clio_chat_history_v2', JSON.stringify({
      default: { activeSessionId: 's', sessions: [{ id: 's', title: 'x', messages: [
        { id: 'q', role: 'user', content: 'How long are invoices kept?' },
        { id: 'a', role: 'assistant', content: 'Seven years [Source 1].', provider: 'anthropic',
          sources: [{ document_id: 'd1', filename: 'retention.pdf', page_number: 2, text_snippet: 'secret' }] },
      ] }] },
    }))
    setActivePinia(createPinia())
    useCollectionStore().collections = [{ id: 'default', name: 'Operations' }]
    useStatsStore().documents = 3
    useProviderStore().loadServerProviders = vi.fn().mockResolvedValue()
    Element.prototype.scrollIntoView = vi.fn()
    window.matchMedia = vi.fn(() => ({ matches: false }))
    http.post.mockReset()
    http.post.mockResolvedValue({ data: { recorded: true } })
  })

  const mountChat = async () => {
    const w = mount(ChatTab, { global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } } })
    await flushPromises()
    return w
  }

  it('reports a wrong citation with the sources and the question, without the answer', async () => {
    const w = await mountChat()
    await w.get('[data-testid="feedback-wrong"]').trigger('click')
    await w.get('[data-testid="feedback-wrong_citation"]').trigger('click')
    await flushPromises()

    const [url, body] = http.post.mock.calls.find(c => c[0] === '/api/chat/feedback')
    expect(url).toBe('/api/chat/feedback')
    expect(body).toMatchObject({ verdict: 'wrong', problem: 'wrong_citation', collection_id: 'default' })
    expect(body.sources).toEqual([{ document_id: 'd1', filename: 'retention.pdf', page_number: 2 }])
    expect(body.question).toBe('How long are invoices kept?')
    expect(JSON.stringify(body)).not.toContain('Seven years')
    expect(w.get('[data-testid="feedback-given"]').text()).toContain('Reported')
    expect(useChatStore().getMessages('default')[1].feedback).toBe('wrong_citation')
  })

  it('records helpful once', async () => {
    const w = await mountChat()
    await w.get('[data-testid="feedback-helpful"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="feedback-helpful"]').exists()).toBe(false)
    expect(w.get('[data-testid="feedback-given"]').text()).toBe('Marked helpful')
  })
})
