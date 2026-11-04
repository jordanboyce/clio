import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWebMcp, WEBMCP_PREF_KEY } from '../useWebMcp'
import { useCollectionStore } from '../../stores/collectionStore'

const readTool = (name) => ({ name, title: name, description: '', inputSchema: { type: 'object' }, annotations: { readOnlyHint: true } })
const writeTool = (name) => ({ name, title: name, description: '', inputSchema: { type: 'object' }, annotations: { readOnlyHint: false } })

// A ModelContext whose registerTool promise never settles on its own, as
// some Chrome builds behave (it rejects with AbortError on the signal).
function fakeModelContext() {
  const registered = new Map()
  return {
    registered,
    registerTool: vi.fn((tool, { signal } = {}) => {
      registered.set(tool.name, tool)
      return new Promise((_, reject) => {
        signal?.addEventListener('abort', () => {
          registered.delete(tool.name)
          reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))
        })
      })
    }),
  }
}

function fakeClient(tools) {
  return {
    initialize: vi.fn(async () => ({})),
    listTools: vi.fn(async () => tools),
    callTool: vi.fn(async () => ({ content: [{ type: 'text', text: 'hit' }] })),
  }
}

const chromeWindow = (document = {}) => ({
  document,
  navigator: { userAgent: 'Mozilla/5.0 Chrome/157.0.0.0 Safari/537.36' },
  isSecureContext: true,
})

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  useWebMcp()._reset()
})

afterEach(() => {
  useWebMcp()._reset()
})

describe('useWebMcp', () => {
  it('reports an unsupported browser and registers nothing', async () => {
    const { state, start } = useWebMcp()
    await start({ win: { document: {}, navigator: { userAgent: 'Mozilla/5.0 Firefox/146.0' }, isSecureContext: true } })
    expect(state.support).toBe('unsupported')
    expect(state.status).toBe('unsupported')
    expect(state.browser.name).toBe('Firefox')
  })

  it('offers only the read-only tools and routes calls through /mcp with the open collection', async () => {
    const context = fakeModelContext()
    const client = fakeClient([readTool('search_documents'), writeTool('write_document'), readTool('list_collections')])
    const { state, start } = useWebMcp()
    await start({ modelContext: context, client, win: chromeWindow() })

    expect(state.support).toBe('available')
    expect(state.status).toBe('ready')
    expect(state.tools.map((t) => t.name)).toEqual(['search_documents', 'list_collections'])
    expect(context.registerTool).toHaveBeenCalledTimes(2)
    expect(client.initialize).toHaveBeenCalled()

    useCollectionStore().currentCollectionId = 'papers'
    const tool = context.registered.get('search_documents')
    expect(tool.annotations).toMatchObject({ readOnlyHint: true, untrustedContentHint: true })
    const result = await tool.execute({ query: 'drought' })
    expect(client.callTool).toHaveBeenCalledWith('search_documents', { query: 'drought' }, expect.objectContaining({ collectionId: 'papers' }))
    expect(result.content[0].text).toBe('hit')
    expect(state.calls[0]).toMatchObject({ name: 'search_documents', collectionId: 'papers', ok: true })
  })

  it('records a failed call and surfaces the error to the agent', async () => {
    const context = fakeModelContext()
    const client = fakeClient([readTool('search_documents')])
    client.callTool.mockResolvedValue({ isError: true, content: [{ type: 'text', text: 'Collection not found' }] })
    const { state, start } = useWebMcp()
    await start({ modelContext: context, client, win: chromeWindow() })
    await expect(context.registered.get('search_documents').execute({ query: 'x' })).rejects.toThrow('Collection not found')
    expect(state.calls[0].ok).toBe(false)
  })

  it('treats a duplicate registration as already offered', async () => {
    const context = fakeModelContext()
    context.registerTool.mockImplementationOnce(() => { throw new Error("Failed to execute 'registerTool' on 'ModelContext': Duplicate tool name") })
    const { state, start } = useWebMcp()
    await start({ modelContext: context, client: fakeClient([readTool('a'), readTool('b')]), win: chromeWindow() })
    expect(state.status).toBe('ready')
    expect(state.tools.map((t) => t.name)).toEqual(['a', 'b'])
    expect(state.failed).toEqual([])
  })

  it('keeps a registration failure out of the offered list', async () => {
    const context = fakeModelContext()
    context.registerTool.mockImplementationOnce(() => { throw new Error('invalid schema') })
    const { state, start } = useWebMcp()
    await start({ modelContext: context, client: fakeClient([readTool('a'), readTool('b')]), win: chromeWindow() })
    expect(state.tools.map((t) => t.name)).toEqual(['b'])
    expect(state.failed).toEqual([{ name: 'a', message: 'invalid schema' }])
  })

  it('reports an unreachable server as an error, not as unsupported', async () => {
    const client = fakeClient([])
    client.listTools.mockRejectedValue(Object.assign(new Error('Not authenticated'), { status: 401 }))
    const { state, start } = useWebMcp()
    await start({ modelContext: fakeModelContext(), client, win: chromeWindow() })
    expect(state.support).toBe('available')
    expect(state.status).toBe('error')
    expect(state.error).toBe('Not authenticated')
  })

  it('honours the remembered switch and withdraws the tools when turned off', async () => {
    localStorage.setItem(WEBMCP_PREF_KEY, '0')
    useWebMcp()._reset()
    const context = fakeModelContext()
    const { state, start, setEnabled } = useWebMcp()
    await start({ modelContext: context, client: fakeClient([readTool('a')]), win: chromeWindow() })
    expect(state.enabled).toBe(false)
    expect(state.status).toBe('off')
    expect(context.registerTool).not.toHaveBeenCalled()

    // Turning it on registers against the live document.modelContext.
    Object.defineProperty(document, 'modelContext', { value: context, configurable: true })
    try {
      await setEnabled(true)
      expect(localStorage.getItem(WEBMCP_PREF_KEY)).toBe('1')
      expect(state.status).toBe('ready')
      expect(context.registered.has('a')).toBe(true)

      await setEnabled(false)
      expect(state.status).toBe('off')
      expect(context.registered.has('a')).toBe(false)
      expect(state.tools).toEqual([])
    } finally {
      Reflect.deleteProperty(document, 'modelContext')
    }
  })

  it('starts once per page', async () => {
    const context = fakeModelContext()
    const client = fakeClient([readTool('a')])
    const { start } = useWebMcp()
    await start({ modelContext: context, client, win: chromeWindow() })
    await start()
    expect(client.listTools).toHaveBeenCalledTimes(1)
  })
})
