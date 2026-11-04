// A minimal MCP client for the page itself.
//
// The page calls Clio's own `/mcp` endpoint over JSON-RPC, exactly as a
// desktop client would, so a browser agent given these tools goes through
// the same code path as every other agent: the same tool list and schemas,
// the same collection scoping, the same audit trail and the same live view
// on the Connect tab. Nothing is re-implemented against the REST API.
//
// The request rides the page's own session (`credentials: 'same-origin'`):
// the Basic credentials the browser holds for a password deployment, or the
// proxy's identity header. No token is minted, so nothing can leak.
//
// Clio's transport is stateless with JSON responses (services/mcp_server.py),
// so every call is one POST and no session id is tracked. `initialize` is
// still sent once: it names the client in the Connect tab's activity view.
// A server that answers with an event stream (a different deployment behind
// a proxy that rewrites Accept) is read too: the last `data:` line wins.

export const MCP_PROTOCOL_VERSION = '2025-06-18'

export function createMcpPageClient({
  baseUrl = '/mcp',
  fetchImpl = (...args) => globalThis.fetch(...args),
  clientName = 'Clio page (WebMCP)',
  clientVersion = 'dev',
} = {}) {
  let seq = 0

  const url = (collectionId) => {
    if (!collectionId) return baseUrl
    const params = new URLSearchParams({ collection_id: collectionId })
    return `${baseUrl}?${params}`
  }

  async function post(body, { collectionId, signal } = {}) {
    const response = await fetchImpl(url(collectionId), {
      method: 'POST',
      credentials: 'same-origin',
      signal,
      headers: {
        Accept: 'application/json, text/event-stream',
        'Content-Type': 'application/json',
        'MCP-Protocol-Version': MCP_PROTOCOL_VERSION,
      },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const detail = await response.text().catch(() => '')
      throw Object.assign(new Error(detail || `MCP endpoint answered ${response.status}`), { status: response.status })
    }
    // A notification is acknowledged with 202 and no body.
    if (response.status === 202) return null
    const type = response.headers?.get?.('content-type') || ''
    const text = await response.text()
    if (!text) return null
    return type.includes('text/event-stream') ? parseEventStream(text) : JSON.parse(text)
  }

  async function rpc(method, params = {}, options = {}) {
    const id = ++seq
    const message = await post({ jsonrpc: '2.0', id, method, params }, options)
    if (!message) throw new Error(`No response to ${method}`)
    if (message.error) {
      throw Object.assign(new Error(message.error.message || `${method} failed`), { code: message.error.code, data: message.error.data })
    }
    return message.result
  }

  async function notify(method, params = {}, options = {}) {
    await post({ jsonrpc: '2.0', method, params }, options)
  }

  return {
    url,
    rpc,
    /** Names the client to the server. Failure is not fatal: the transport is stateless. */
    async initialize(options) {
      const result = await rpc('initialize', {
        protocolVersion: MCP_PROTOCOL_VERSION,
        capabilities: {},
        clientInfo: { name: clientName, version: clientVersion },
      }, options)
      await notify('notifications/initialized', {}, options).catch(() => {})
      return result
    },
    async listTools(options) {
      const result = await rpc('tools/list', {}, options)
      return Array.isArray(result?.tools) ? result.tools : []
    },
    async callTool(name, args, options) {
      return rpc('tools/call', { name, arguments: args || {} }, options)
    },
  }
}

/** The last JSON-RPC message in a server-sent event body. */
export function parseEventStream(text) {
  let last = null
  for (const line of text.split(/\r?\n/)) {
    if (!line.startsWith('data:')) continue
    const payload = line.slice(5).trim()
    if (!payload) continue
    try {
      last = JSON.parse(payload)
    } catch {
      // A partial or non-JSON data line; keep the previous complete message.
    }
  }
  return last
}
