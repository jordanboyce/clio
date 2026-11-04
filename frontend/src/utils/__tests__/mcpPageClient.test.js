import { describe, expect, it, vi } from 'vitest'
import { createMcpPageClient, parseEventStream, MCP_PROTOCOL_VERSION } from '../mcpPageClient'

const jsonResponse = (body, { status = 200, type = 'application/json' } = {}) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: (name) => (name.toLowerCase() === 'content-type' ? type : null) },
  text: async () => (body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body)),
})

describe('createMcpPageClient', () => {
  it('posts JSON-RPC to /mcp with the page session and the collection pinned in the URL', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ jsonrpc: '2.0', id: 1, result: { content: [{ type: 'text', text: 'ok' }] } }))
    const client = createMcpPageClient({ fetchImpl })
    const result = await client.callTool('search_documents', { query: 'late fee' }, { collectionId: 'research & notes' })
    expect(result.content[0].text).toBe('ok')
    const [url, init] = fetchImpl.mock.calls[0]
    expect(url).toBe('/mcp?collection_id=research+%26+notes')
    expect(init.method).toBe('POST')
    expect(init.credentials).toBe('same-origin')
    expect(init.headers.Accept).toBe('application/json, text/event-stream')
    expect(init.headers['MCP-Protocol-Version']).toBe(MCP_PROTOCOL_VERSION)
    expect(JSON.parse(init.body)).toEqual({ jsonrpc: '2.0', id: 1, method: 'tools/call', params: { name: 'search_documents', arguments: { query: 'late fee' } } })
  })

  it('leaves the URL unscoped when no collection is open', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ jsonrpc: '2.0', id: 1, result: { tools: [{ name: 'a' }] } }))
    const client = createMcpPageClient({ fetchImpl })
    expect(await client.listTools()).toEqual([{ name: 'a' }])
    expect(fetchImpl.mock.calls[0][0]).toBe('/mcp')
  })

  it('names the client on initialize and tolerates a 202 to the notification', async () => {
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ jsonrpc: '2.0', id: 1, result: { serverInfo: { name: 'clio' } } }))
      .mockResolvedValueOnce(jsonResponse(undefined, { status: 202 }))
    const client = createMcpPageClient({ fetchImpl, clientName: 'Clio page (WebMCP)', clientVersion: '3.0.1' })
    const result = await client.initialize()
    expect(result.serverInfo.name).toBe('clio')
    const init = JSON.parse(fetchImpl.mock.calls[0][1].body)
    expect(init.params.clientInfo).toEqual({ name: 'Clio page (WebMCP)', version: '3.0.1' })
    expect(JSON.parse(fetchImpl.mock.calls[1][1].body).method).toBe('notifications/initialized')
  })

  it('throws the JSON-RPC error message', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ jsonrpc: '2.0', id: 1, error: { code: -32602, message: 'Unknown tool' } }))
    await expect(createMcpPageClient({ fetchImpl }).callTool('nope', {})).rejects.toThrow('Unknown tool')
  })

  it('throws on an HTTP failure with the status attached', async () => {
    const fetchImpl = vi.fn(async () => jsonResponse('{"detail":"Not authenticated"}', { status: 401 }))
    await expect(createMcpPageClient({ fetchImpl }).listTools()).rejects.toMatchObject({ status: 401 })
  })

  it('reads a server-sent event body when the transport streams', async () => {
    const body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"x"}]}}\n\n'
    const fetchImpl = vi.fn(async () => jsonResponse(body, { type: 'text/event-stream' }))
    expect(await createMcpPageClient({ fetchImpl }).listTools()).toEqual([{ name: 'x' }])
  })
})

describe('parseEventStream', () => {
  it('returns the last complete data line and skips malformed ones', () => {
    const text = 'data: {"a":1}\r\ndata: not json\r\n\r\ndata: {"b":2}\r\n'
    expect(parseEventStream(text)).toEqual({ b: 2 })
    expect(parseEventStream('')).toBeNull()
  })
})
