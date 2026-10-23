import { describe, expect, it } from 'vitest'

import { MCP_CLIENTS, MCP_CLIENT_GROUPS, buildClientConfig, findClient } from '../mcpClients'

const ctx = {
  id: 'clio-default',
  url: 'https://clio.example.org/mcp?collection_id=default',
  sseUrl: 'https://clio.example.org/mcp/sse?collection_id=default',
  token: 'clio_abc123',
  oauthEnabled: false,
}

describe('mcpClients', () => {
  it('has unique keys across groups', () => {
    const keys = MCP_CLIENTS.map((c) => c.key)
    expect(new Set(keys).size).toBe(keys.length)
    expect(MCP_CLIENT_GROUPS.length).toBeGreaterThanOrEqual(3)
  })

  it('every recipe names the endpoint and the credential', () => {
    for (const client of MCP_CLIENTS) {
      const meta = buildClientConfig(client.key, ctx)
      expect(meta.content, client.key).toContain(ctx.url)
      // Hosted connectors sign in with OAuth from the vendor's dialog; they
      // are the only recipes that carry no bearer credential.
      if (meta.filename !== 'connector') expect(meta.content, client.key).toContain('clio_abc123')
      expect(meta.filename, client.key).toBeTruthy()
      expect(typeof meta.hint, client.key).toBe('string')
      expect(meta.hint.length, client.key).toBeGreaterThan(20)
    }
  })

  it('JSON recipes are valid JSON with the server under the client\'s own key', () => {
    const expectations = {
      claude: ['mcpServers', 'clio-default', 'url'],
      codex: null,
      pi: ['mcpServers', 'clio-default', 'url'],
      gemini: ['mcpServers', 'clio-default', 'httpUrl'],
      cursor: ['mcpServers', 'clio-default', 'url'],
      copilot: ['servers', 'clio-default', 'url'],
      'copilot-cli': ['mcpServers', 'clio-default', 'url'],
      windsurf: ['mcpServers', 'clio-default', 'serverUrl'],
      zed: ['context_servers', 'clio-default', 'url'],
      cline: ['mcpServers', 'clio-default', 'url'],
      roo: ['mcpServers', 'clio-default', 'url'],
      kiro: ['mcpServers', 'clio-default', 'url'],
      opencode: ['mcp', 'clio-default', 'url'],
      anythingllm: ['mcpServers', 'clio-default', 'url'],
      desktop: ['mcpServers', 'clio-default', 'args'],
    }
    for (const [key, path] of Object.entries(expectations)) {
      if (!path) continue
      const parsed = JSON.parse(buildClientConfig(key, ctx).content)
      let node = parsed
      for (const step of path) {
        expect(node, `${key}: ${step}`).toHaveProperty(step)
        node = node[step]
      }
      if (path[2] !== 'args') expect(node).toBe(ctx.url)
    }
  })

  it('transport types match each client\'s documented spelling', () => {
    expect(JSON.parse(buildClientConfig('cline', ctx).content).mcpServers['clio-default'].type).toBe('streamableHttp')
    expect(JSON.parse(buildClientConfig('roo', ctx).content).mcpServers['clio-default'].type).toBe('streamable-http')
    expect(JSON.parse(buildClientConfig('pi', ctx).content).mcpServers['clio-default'].type).toBe('streamable-http')
    expect(JSON.parse(buildClientConfig('opencode', ctx).content).mcp['clio-default'].type).toBe('remote')
    expect(JSON.parse(buildClientConfig('copilot', ctx).content).servers['clio-default'].type).toBe('http')
    expect(buildClientConfig('goose', ctx).content).toContain('type: streamable_http')
    expect(buildClientConfig('librechat', ctx).content).toContain('type: streamable-http')
    expect(buildClientConfig('langchain', ctx).content).toContain('"transport": "streamable_http"')
  })

  it('the API recipes carry both halves the vendors require', () => {
    const anthropic = buildClientConfig('anthropic-api', ctx).content
    expect(anthropic).toContain('mcp_servers=')
    expect(anthropic).toContain('"type": "mcp_toolset"')
    expect(anthropic).toContain('mcp-client-2025-11-20')
    const openai = buildClientConfig('openai-api', ctx).content
    expect(openai).toContain('"type": "mcp"')
    expect(openai).toContain('"server_url"')
  })

  it('hosted connectors explain the OAuth requirement when sign-in is off', () => {
    const off = buildClientConfig('claude-ai', ctx)
    expect(off.hint).toMatch(/identity provider/i)
    const on = buildClientConfig('claude-ai', { ...ctx, oauthEnabled: true })
    expect(on.hint).not.toMatch(/no identity provider configured/i)
  })

  it('the generic entry mentions the legacy SSE endpoint', () => {
    expect(buildClientConfig('generic', ctx).content).toContain(ctx.sseUrl)
    expect(findClient('no-such-client').key).toBe(MCP_CLIENTS[0].key)
  })
})
