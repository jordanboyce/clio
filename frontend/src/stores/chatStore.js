import { defineStore } from 'pinia'
import { ref } from 'vue'

const STORAGE_KEY = 'clio_chat_history_v2'
const LEGACY_STORAGE_KEY = 'clio_chat_history_v1'
const MAX_MESSAGES_PER_SESSION = 100
const MAX_SESSIONS_PER_COLLECTION = 50

const newSessionId = () =>
  `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

// Every message carries an id so lists can be keyed by identity rather than
// position (messages are cut, replaced and swapped between sessions), and so
// a stream can keep writing to the message it started, wherever the user
// navigates meanwhile.
const newMessageId = () =>
  `m_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`

const ensureMessageIds = (conversations) => {
  for (const conv of Object.values(conversations || {})) {
    for (const session of conv?.sessions || []) {
      for (const msg of session?.messages || []) {
        if (msg && !msg.id) msg.id = newMessageId()
      }
    }
  }
}

const makeSession = (overrides = {}) => ({
  id: newSessionId(),
  title: 'New chat',
  messages: [],
  createdAt: Date.now(),
  updatedAt: Date.now(),
  ...overrides,
})

const deriveTitle = (text) => {
  if (!text) return 'New chat'
  const trimmed = text.trim().replace(/\s+/g, ' ')
  return trimmed.length > 60 ? trimmed.slice(0, 57) + '…' : trimmed
}

export const useChatStore = defineStore('chat', () => {
  // { collectionId: { sessions: [Session], activeSessionId: string } }
  const conversations = ref({})

  const loadFromStorage = () => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (saved) {
        const parsed = JSON.parse(saved)
        ensureMessageIds(parsed)
        conversations.value = parsed
        return
      }
      // Migrate from v1 format: { collectionId: { messages: [...] } }
      const legacy = localStorage.getItem(LEGACY_STORAGE_KEY)
      if (legacy) {
        const parsed = JSON.parse(legacy)
        const migrated = {}
        for (const [colId, conv] of Object.entries(parsed)) {
          const msgs = conv?.messages || []
          for (const m of msgs) if (m && !m.id) m.id = newMessageId()
          const session = makeSession({
            messages: msgs,
            title: deriveTitle(msgs.find((m) => m.role === 'user')?.content),
          })
          migrated[colId] = {
            sessions: [session],
            activeSessionId: session.id,
          }
        }
        conversations.value = migrated
        saveToStorage()
      }
    } catch (e) {
      console.error('Failed to load chat history:', e)
    }
  }

  const saveToStorage = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations.value))
    } catch (e) {
      console.error('Failed to save chat history:', e)
    }
  }

  const ensureConversation = (collectionId) => {
    if (!conversations.value[collectionId]) {
      const session = makeSession()
      conversations.value[collectionId] = {
        sessions: [session],
        activeSessionId: session.id,
      }
    } else if (!conversations.value[collectionId].sessions?.length) {
      const session = makeSession()
      conversations.value[collectionId].sessions = [session]
      conversations.value[collectionId].activeSessionId = session.id
    } else if (!conversations.value[collectionId].activeSessionId) {
      conversations.value[collectionId].activeSessionId =
        conversations.value[collectionId].sessions[0].id
    }
  }

  const getActiveSession = (collectionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]
    return conv.sessions.find((s) => s.id === conv.activeSessionId) || conv.sessions[0]
  }

  const getSessions = (collectionId) => {
    ensureConversation(collectionId)
    return conversations.value[collectionId].sessions
  }

  const getActiveSessionId = (collectionId) => {
    ensureConversation(collectionId)
    return conversations.value[collectionId].activeSessionId
  }

  const getMessages = (collectionId) => {
    return getActiveSession(collectionId)?.messages || []
  }

  const addMessage = (collectionId, message) => {
    const session = getActiveSession(collectionId)
    session.messages.push({ id: newMessageId(), ...message, timestamp: Date.now() })
    if (session.messages.length > MAX_MESSAGES_PER_SESSION) {
      session.messages = session.messages.slice(session.messages.length - MAX_MESSAGES_PER_SESSION)
    }
    // Auto-title from first user message
    if (message.role === 'user' && (session.title === 'New chat' || !session.title)) {
      session.title = deriveTitle(message.content)
    }
    session.updatedAt = Date.now()
    saveToStorage()
  }

  const addAssistantMessage = (collectionId, message, sources, aiUsage, structuredResults) => {
    const session = getActiveSession(collectionId)
    session.messages.push({
      id: newMessageId(),
      ...message,
      sources: sources || [],
      aiUsage: aiUsage || null,
      structuredResults: structuredResults || null,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    saveToStorage()
  }

  // --- Streaming support ---
  // The stream is bound to the session and message that started it, not to
  // "whatever is active now": while an answer streams the user may open
  // another chat or start a new one, and the text must still land in the
  // right place. addStreamingMessage returns that binding; every other
  // streaming call takes it.
  const addStreamingMessage = (collectionId) => {
    const session = getActiveSession(collectionId)
    const id = newMessageId()
    session.messages.push({
      id,
      role: 'assistant',
      content: '',
      streaming: true,
      structuredResults: [],
      sources: [],
      aiUsage: null,
      timestamp: Date.now(),
    })
    session.updatedAt = Date.now()
    // Don't persist to storage while streaming — wait for finalizeStreamingMessage
    return { collectionId, sessionId: session.id, messageId: id }
  }

  const _streamSession = (stream) => {
    if (!stream) return null
    const conv = conversations.value[stream.collectionId]
    return conv?.sessions.find((s) => s.id === stream.sessionId) || null
  }

  const getStreamingMessage = (stream) => {
    const session = _streamSession(stream)
    return session?.messages.find((m) => m.id === stream.messageId) || null
  }

  const appendStreamingText = (stream, delta) => {
    const msg = getStreamingMessage(stream)
    if (msg) msg.content += delta
  }

  const addStreamingToolCall = (stream, tool, args) => {
    const msg = getStreamingMessage(stream)
    if (msg) {
      if (!msg.structuredResults) msg.structuredResults = []
      msg.structuredResults.push({ tool, args, result: null, pending: true })
    }
  }

  const resolveStreamingToolCall = (stream, tool, result) => {
    const msg = getStreamingMessage(stream)
    if (!msg || !msg.structuredResults) return
    // Find the most recent pending entry for this tool and resolve it
    for (let i = msg.structuredResults.length - 1; i >= 0; i--) {
      const sr = msg.structuredResults[i]
      if (sr.tool === tool && sr.pending) {
        msg.structuredResults[i] = { tool, args: sr.args, ...result, pending: false }
        break
      }
    }
  }

  const addStreamingThinking = (stream, text) => {
    const msg = getStreamingMessage(stream)
    if (msg) {
      if (!msg.structuredResults) msg.structuredResults = []
      msg.structuredResults.push({ tool: '_thinking', args: {}, result: { text }, pending: false })
    }
  }

  // Suggested follow-ups arrive as their own event after the answer text.
  const setStreamingRelated = (stream, questions) => {
    const msg = getStreamingMessage(stream)
    if (msg) msg.relatedQuestions = Array.isArray(questions) ? questions : []
  }

  const finalizeStreamingMessage = (stream, { sources, usage, structuredResults, cached, cachedQuestion, cachedSimilarity, relatedQuestions, depth, provider, scope } = {}) => {
    const msg = getStreamingMessage(stream)
    if (!msg) return
    msg.streaming = false
    if (sources) msg.sources = sources
    if (Array.isArray(relatedQuestions)) msg.relatedQuestions = relatedQuestions
    if (depth) msg.depth = depth
    if (provider) msg.provider = provider
    if (scope) msg.scope = scope
    if (usage) msg.aiUsage = { synthesis: { ...usage }, total_input_tokens: usage.input_tokens || 0, total_output_tokens: usage.output_tokens || 0 }
    if (cached) {
      msg.cached = true
      msg.cachedQuestion = cachedQuestion || ''
      msg.cachedSimilarity = typeof cachedSimilarity === 'number' ? cachedSimilarity : null
    }
    // Replace structuredResults with the server's authoritative list if provided
    if (structuredResults && structuredResults.length > 0) msg.structuredResults = structuredResults
    const session = _streamSession(stream)
    if (session) session.updatedAt = Date.now()
    saveToStorage()
  }

  const removeStreamingMessage = (stream) => {
    const session = _streamSession(stream)
    if (!session) return
    const i = session.messages.findIndex((m) => m.id === stream.messageId)
    if (i !== -1) session.messages.splice(i, 1)
    session.updatedAt = Date.now()
  }

  const clearMessages = (collectionId) => {
    const session = getActiveSession(collectionId)
    if (session) {
      session.messages = []
      session.title = 'New chat'
      session.updatedAt = Date.now()
      saveToStorage()
    }
  }

  const newSession = (collectionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]

    // If the active session is already empty, just reuse it instead of piling up empties
    const active = conv.sessions.find((s) => s.id === conv.activeSessionId)
    if (active && active.messages.length === 0) {
      saveToStorage()
      return active.id
    }

    const session = makeSession()
    conv.sessions.unshift(session)
    conv.activeSessionId = session.id

    // Trim oldest sessions if over the cap
    if (conv.sessions.length > MAX_SESSIONS_PER_COLLECTION) {
      conv.sessions = conv.sessions.slice(0, MAX_SESSIONS_PER_COLLECTION)
    }
    saveToStorage()
    return session.id
  }

  const selectSession = (collectionId, sessionId) => {
    ensureConversation(collectionId)
    const conv = conversations.value[collectionId]
    if (conv.sessions.some((s) => s.id === sessionId)) {
      conv.activeSessionId = sessionId
      saveToStorage()
    }
  }

  const deleteSession = (collectionId, sessionId) => {
    const conv = conversations.value[collectionId]
    if (!conv) return
    conv.sessions = conv.sessions.filter((s) => s.id !== sessionId)
    if (conv.sessions.length === 0) {
      const session = makeSession()
      conv.sessions = [session]
      conv.activeSessionId = session.id
    } else if (conv.activeSessionId === sessionId) {
      conv.activeSessionId = conv.sessions[0].id
    }
    saveToStorage()
  }

  const renameSession = (collectionId, sessionId, title) => {
    const conv = conversations.value[collectionId]
    if (!conv) return
    const session = conv.sessions.find((s) => s.id === sessionId)
    if (session) {
      session.title = deriveTitle(title) || 'Untitled'
      session.updatedAt = Date.now()
      saveToStorage()
    }
  }

  const clearCollectionChat = (collectionId) => {
    delete conversations.value[collectionId]
    saveToStorage()
  }

  // Initialize from storage
  loadFromStorage()

  return {
    conversations,
    getMessages,
    getSessions,
    getActiveSessionId,
    getActiveSession,
    addMessage,
    addAssistantMessage,
    clearMessages,
    newSession,
    selectSession,
    deleteSession,
    renameSession,
    clearCollectionChat,
    // Streaming
    addStreamingMessage,
    getStreamingMessage,
    appendStreamingText,
    addStreamingToolCall,
    resolveStreamingToolCall,
    addStreamingThinking,
    setStreamingRelated,
    finalizeStreamingMessage,
    removeStreamingMessage,
  }
})
