import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useChatStore } from '../chatStore'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('chatStore streaming', () => {
  it('keeps writing to the session that asked after the user opens another chat', () => {
    const store = useChatStore()
    store.addMessage('c1', { role: 'user', content: 'first question' })
    const stream = store.addStreamingMessage('c1')
    const asking = store.getActiveSessionId('c1')

    // The person starts a new chat while the answer is still arriving.
    store.newSession('c1')
    store.addMessage('c1', { role: 'user', content: 'unrelated' })
    expect(store.getActiveSessionId('c1')).not.toBe(asking)

    store.appendStreamingText(stream, 'The answer')
    store.setStreamingRelated(stream, ['Why?'])
    store.finalizeStreamingMessage(stream, { sources: [{ id: 1 }], provider: 'anthropic', scope: 'current' })

    const askingSession = store.getSessions('c1').find((s) => s.id === asking)
    const answer = askingSession.messages.find((m) => m.id === stream.messageId)
    expect(answer).toMatchObject({ content: 'The answer', streaming: false, provider: 'anthropic', scope: 'current' })
    expect(answer.relatedQuestions).toEqual(['Why?'])
    // Nothing leaked into the chat that is now open.
    expect(store.getMessages('c1').some((m) => m.role === 'assistant')).toBe(false)
  })

  it('removes exactly the in-flight message on error', () => {
    const store = useChatStore()
    store.addMessage('c1', { role: 'user', content: 'q' })
    store.addAssistantMessage('c1', { role: 'assistant', content: 'earlier answer' })
    const stream = store.addStreamingMessage('c1')
    store.removeStreamingMessage(stream)
    const msgs = store.getMessages('c1')
    expect(msgs.map((m) => m.content)).toEqual(['q', 'earlier answer'])
  })

  it('gives every message an id, including ones loaded from older storage', () => {
    localStorage.setItem('clio_chat_history_v2', JSON.stringify({
      c1: { sessions: [{ id: 's1', title: 't', messages: [{ role: 'user', content: 'old' }], createdAt: 1, updatedAt: 1 }], activeSessionId: 's1' },
    }))
    const store = useChatStore()
    const [old] = store.getMessages('c1')
    expect(old.id).toMatch(/^m_/)
    store.addMessage('c1', { role: 'user', content: 'new' })
    const ids = store.getMessages('c1').map((m) => m.id)
    expect(new Set(ids).size).toBe(ids.length)
  })
})
