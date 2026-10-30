import { describe, expect, it } from 'vitest'

import { MCP_CLIENTS, MCP_CLIENT_GROUPS, bearerFor, buildClientConfig, findClient, formatsFor } from '../mcpClients'

const server = (collectionId = 'default') => ({
  id: collectionId ? `clio-${collectionId}` : 'clio',
  collectionId,
  url: collectionId ? `https://clio.example.org/mcp?collection_id=${collectionId}` : 'https://clio.example.org/mcp',
  sseUrl: collectionId ? `https://clio.example.org/mcp/sse?collection_id=${collectionId}` : 'https://clio.example.org/mcp/sse',
})

const ctx = {
  servers: [server()],
  transport: 'http',
  credential: { mode: 'token', token: 'clio_abc123', envVar: 'CLIO_TOKEN' },
  oauthEnabled: false,
}
const envCtx = { ...ctx, credential: { mode: 'env', token: 'clio_abc123', envVar: 'CLIO_TOKEN' } }
const noneCtx = { ...ctx, credential: { mode: 'none', token: 'clio_abc123', envVar: 'CLIO_TOKEN' } }

// Recipes that do not key anything by the server name (dialogs, SDK
// samples that only need the URL).
const NO_ID = new Set(['openwebui', 'n8n', 'python-sdk', 'vercel-ai', 'pydantic-ai', 'google-adk', 'generic'])
// Hosted connectors sign in from the vendor's dialog: no bearer anywhere.
const CONNECTOR_ONLY = new Set(['claude-ai', 'chatgpt', 'grok'])
// Clients whose documented remote form has no header field: the token
// travels through the mcp-remote bridge format instead.
const BRIDGE_FOR_TOKEN = new Set(['jetbrains', 'continue'])

const parse = (meta) => JSON.parse(meta.content)

describe('mcpClients catalog', () => {
  it('has unique keys across groups and a decent spread of clients', () => {
    const keys = MCP_CLIENTS.map((c) => c.key)
    expect(new Set(keys).size).toBe(keys.length)
    expect(MCP_CLIENT_GROUPS.length).toBe(3)
    expect(keys.length).toBeGreaterThanOrEqual(40)
    for (const want of ['claude', 'codex', 'gemini', 'cursor', 'copilot', 'amp', 'droid', 'crush', 'vibe', 'qwen', 'jetbrains', 'continue',
      'lmstudio', 'warp', 'claude-agent-sdk', 'openai-agents', 'vercel-ai', 'pydantic-ai', 'google-adk']) {
      expect(keys, want).toContain(want)
    }
  })

  it('every recipe and every format names the endpoint, and has a hint and a docs link', () => {
    for (const client of MCP_CLIENTS) {
      expect(client.docs, client.key).toMatch(/^https:\/\//)
      for (const format of client.formats) {
        const meta = buildClientConfig(client.key, ctx, { format: format.key })
        expect(meta.format, `${client.key}/${format.key}`).toBe(format.key)
        // Cursor's install link carries the entry base64-encoded; read it back.
        const visible = format.key === 'link'
          ? Buffer.from(new URL(meta.content).searchParams.get('config'), 'base64').toString('utf8')
          : meta.content
        expect(visible, `${client.key}/${format.key}`).toContain('https://clio.example.org/mcp')
        if (!NO_ID.has(client.key)) expect(meta.content, `${client.key}/${format.key}`).toContain('clio-default')
        expect(typeof meta.hint, client.key).toBe('string')
        expect(meta.hint.length, client.key).toBeGreaterThan(20)
        expect(meta.filename, client.key).toBeTruthy()
      }
    }
  })

  it('the token reaches every client that can carry one, and never one that cannot', () => {
    for (const client of MCP_CLIENTS) {
      const first = buildClientConfig(client.key, ctx)
      if (CONNECTOR_ONLY.has(client.key)) {
        expect(first.content, client.key).not.toContain('clio_abc123')
      } else if (BRIDGE_FOR_TOKEN.has(client.key)) {
        expect(first.content, client.key).not.toContain('clio_abc123')
        expect(buildClientConfig(client.key, ctx, { format: 'bridge' }).content, client.key).toContain('clio_abc123')
      } else {
        expect(first.content, client.key).toContain('clio_abc123')
      }
    }
  })

  it('with no credential, no recipe carries an Authorization header', () => {
    for (const client of MCP_CLIENTS) {
      for (const format of client.formats) {
        if (format.key === 'prompt') continue // VS Code's prompting variant exists to ask for a token
        const meta = buildClientConfig(client.key, noneCtx, { format: format.key })
        expect(meta.content, `${client.key}/${format.key}`).not.toContain('Bearer ')
        expect(meta.content, `${client.key}/${format.key}`).not.toContain('clio_abc123')
      }
    }
  })
})

describe('JSON config files', () => {
  it('are valid JSON with the server under the client\'s own key and URL field', () => {
    const expectations = {
      claude: ['mcpServers', 'clio-default', 'url'],
      pi: ['mcpServers', 'clio-default', 'url'],
      gemini: ['mcpServers', 'clio-default', 'httpUrl'],
      qwen: ['mcpServers', 'clio-default', 'httpUrl'],
      cursor: ['mcpServers', 'clio-default', 'url'],
      copilot: ['servers', 'clio-default', 'url'],
      'copilot-cli': ['mcpServers', 'clio-default', 'url'],
      windsurf: ['mcpServers', 'clio-default', 'serverUrl'],
      zed: ['context_servers', 'clio-default', 'url'],
      cline: ['mcpServers', 'clio-default', 'url'],
      roo: ['mcpServers', 'clio-default', 'url'],
      kiro: ['mcpServers', 'clio-default', 'url'],
      opencode: ['mcp', 'clio-default', 'url'],
      amp: ['amp.mcpServers', 'clio-default', 'url'],
      droid: ['mcpServers', 'clio-default', 'url'],
      lmstudio: ['mcpServers', 'clio-default', 'url'],
      warp: ['mcpServers', 'clio-default', 'url'],
      anythingllm: ['mcpServers', 'clio-default', 'url'],
      jetbrains: ['mcpServers', 'clio-default', 'url'],
    }
    for (const [key, path] of Object.entries(expectations)) {
      let node = parse(buildClientConfig(key, ctx))
      for (const step of path) {
        expect(node, `${key}: ${step}`).toHaveProperty(step)
        node = node[step]
      }
      expect(node, key).toBe(ctx.servers[0].url)
    }
    expect(parse(buildClientConfig('desktop', ctx)).mcpServers['clio-default'].args).toContain('mcp-remote')
    expect(parse(buildClientConfig('crush', ctx, { format: 'json' })).mcp['clio-default'].type).toBe('http')
  })

  it('transport types match each client\'s documented spelling', () => {
    expect(parse(buildClientConfig('claude', ctx)).mcpServers['clio-default'].type).toBe('http')
    expect(parse(buildClientConfig('cline', ctx)).mcpServers['clio-default'].type).toBe('streamableHttp')
    expect(parse(buildClientConfig('roo', ctx)).mcpServers['clio-default'].type).toBe('streamable-http')
    expect(parse(buildClientConfig('pi', ctx)).mcpServers['clio-default'].type).toBe('streamable-http')
    expect(parse(buildClientConfig('opencode', ctx)).mcp['clio-default'].type).toBe('remote')
    expect(parse(buildClientConfig('copilot', ctx)).servers['clio-default'].type).toBe('http')
    expect(parse(buildClientConfig('droid', ctx)).mcpServers['clio-default'].type).toBe('http')
    expect(parse(buildClientConfig('anythingllm', ctx)).mcpServers['clio-default'].type).toBe('streamable')
    expect(buildClientConfig('goose', ctx).content).toContain('type: streamable_http')
    expect(buildClientConfig('librechat', ctx).content).toContain('type: streamable-http')
    expect(buildClientConfig('continue', ctx).content).toContain('type: streamable-http')
    expect(buildClientConfig('vibe', ctx).content).toContain('transport = "streamable-http"')
    expect(buildClientConfig('langchain', ctx).content).toContain('"transport": "streamable_http"')
  })

  it('"server entry only" yields the members to paste into an existing file', () => {
    const entry = buildClientConfig('claude', ctx, { entryOnly: true })
    expect(entry.entryOnly).toBe(true)
    expect(entry.content.startsWith('"clio-default": {')).toBe(true)
    expect(entry.content).not.toContain('mcpServers')
    // Wrapping the members back in braces gives the same server object.
    expect(JSON.parse(`{${entry.content}}`)['clio-default']).toEqual(parse(buildClientConfig('claude', ctx)).mcpServers['clio-default'])
    // Formats without a file around them ignore the toggle.
    const cliMeta = buildClientConfig('claude', ctx, { format: 'cli', entryOnly: true })
    expect(cliMeta.entryOnly).toBe(false)
    expect(cliMeta.entryOnlySupported).toBe(false)
  })

  it('several collections become several entries, one per server, in every file format', () => {
    const multi = { ...ctx, servers: [server('research'), server('runbooks')] }
    const claude = parse(buildClientConfig('claude', multi)).mcpServers
    expect(Object.keys(claude)).toEqual(['clio-research', 'clio-runbooks'])
    expect(claude['clio-runbooks'].url).toContain('collection_id=runbooks')
    const codex = buildClientConfig('codex', multi).content
    expect(codex).toContain('[mcp_servers.clio-research]')
    expect(codex).toContain('[mcp_servers.clio-runbooks]')
    const goose = buildClientConfig('goose', multi).content
    expect(goose.match(/uri: /g)).toHaveLength(2)
    expect(buildClientConfig('claude', multi, { format: 'cli' }).content.split('\n').filter((l) => l.startsWith('claude mcp add'))).toHaveLength(2)
    const sdk = buildClientConfig('claude-agent-sdk', multi).content
    expect(sdk).toContain('"mcp__clio-research__*", "mcp__clio-runbooks__*"')
  })

  it('no collection means an unscoped URL the agent scopes itself', () => {
    const unscoped = { ...ctx, servers: [server(null)] }
    expect(parse(buildClientConfig('cursor', unscoped)).mcpServers.clio.url).toBe('https://clio.example.org/mcp')
  })
})

describe('credentials', () => {
  it('environment references use each client\'s own syntax', () => {
    expect(parse(buildClientConfig('claude', envCtx)).mcpServers['clio-default'].headers.Authorization).toBe('Bearer ${CLIO_TOKEN}')
    expect(parse(buildClientConfig('cursor', envCtx)).mcpServers['clio-default'].headers.Authorization).toBe('Bearer ${env:CLIO_TOKEN}')
    expect(parse(buildClientConfig('windsurf', envCtx)).mcpServers['clio-default'].headers.Authorization).toBe('Bearer ${env:CLIO_TOKEN}')
    expect(parse(buildClientConfig('copilot', envCtx)).servers['clio-default'].headers.Authorization).toBe('Bearer ${env:CLIO_TOKEN}')
    expect(parse(buildClientConfig('opencode', envCtx)).mcp['clio-default'].headers.Authorization).toBe('Bearer {env:CLIO_TOKEN}')
    expect(buildClientConfig('codex', envCtx).content).toContain('bearer_token_env_var = "CLIO_TOKEN"')
    expect(buildClientConfig('codex', envCtx).content).not.toContain('clio_abc123')
    expect(buildClientConfig('vibe', envCtx).content).toContain('api_key_env = "CLIO_TOKEN"')
    expect(buildClientConfig('crush', envCtx).content).toContain('"Bearer $CLIO_TOKEN"')
    expect(buildClientConfig('librechat', envCtx).content).toContain('Bearer ${CLIO_TOKEN}')
    expect(buildClientConfig('python-sdk', envCtx).content).toContain('os.environ[\'CLIO_TOKEN\']')
    expect(buildClientConfig('typescript-sdk', envCtx).content).toContain('process.env.CLIO_TOKEN')
    expect(buildClientConfig('anthropic-api', envCtx).content).toContain('"authorization_token": os.environ["CLIO_TOKEN"]')
  })

  it('a client without variable syntax gets the token and says so', () => {
    const gemini = buildClientConfig('gemini', envCtx)
    expect(parse(gemini).mcpServers['clio-default'].headers.Authorization).toBe('Bearer clio_abc123')
    expect(gemini.note).toMatch(/no variable substitution/i)
    expect(buildClientConfig('claude', envCtx).note).toBe('')
    expect(bearerFor(envCtx, null).note).toContain('CLIO_TOKEN')
    expect(bearerFor(noneCtx, null).value).toBeNull()
  })

  it('the Codex and Pi commands never put the token on the command line in env mode', () => {
    const codex = buildClientConfig('codex', envCtx, { format: 'cli' }).content
    expect(codex).toContain('--bearer-token-env-var CLIO_TOKEN')
    expect(codex).not.toContain('clio_abc123')
    const pi = buildClientConfig('pi', envCtx, { format: 'cli' }).content
    expect(pi).toContain('--bearer-token-env-var CLIO_TOKEN')
    expect(pi).not.toContain('clio_abc123')
    // In token mode the command is preceded by an export so it still works as pasted.
    expect(buildClientConfig('codex', ctx, { format: 'cli' }).content).toMatch(/^export CLIO_TOKEN="clio_abc123"\n/)
  })
})

describe('formats and transports', () => {
  it('Claude Code offers a file and a command; the command carries header and scope', () => {
    const meta = buildClientConfig('claude', ctx, { format: 'cli' })
    expect(meta.formats.map((f) => f.key)).toEqual(['file', 'cli'])
    expect(meta.content).toContain('claude mcp add --transport http --scope user clio-default https://clio.example.org/mcp?collection_id=default')
    expect(meta.content).toContain('--header "Authorization: Bearer clio_abc123"')
  })

  it('VS Code can prompt for the token instead of storing it', () => {
    const meta = buildClientConfig('copilot', ctx, { format: 'prompt' })
    const parsed = parse(meta)
    expect(parsed.inputs[0]).toMatchObject({ type: 'promptString', id: 'clio-token', password: true })
    expect(parsed.servers['clio-default'].headers.Authorization).toBe('Bearer ${input:clio-token}')
    expect(meta.content).not.toContain('clio_abc123')
    const cliMeta = buildClientConfig('copilot', ctx, { format: 'cli' })
    expect(cliMeta.content.startsWith('code --add-mcp \'{"name":"clio-default","type":"http"')).toBe(true)
  })

  it('Cursor\'s install link carries the base64 of the server entry', () => {
    const link = buildClientConfig('cursor', ctx, { format: 'link' }).content
    const url = new URL(link)
    expect(url.protocol).toBe('cursor:')
    expect(url.searchParams.get('name')).toBe('clio-default')
    const config = JSON.parse(Buffer.from(url.searchParams.get('config'), 'base64').toString('utf8'))
    expect(config.url).toBe(ctx.servers[0].url)
    expect(config.headers.Authorization).toBe('Bearer clio_abc123')
  })

  it('legacy SSE switches the endpoint and the type where the client documents it', () => {
    const sse = { ...ctx, transport: 'sse' }
    expect(parse(buildClientConfig('claude', sse)).mcpServers['clio-default']).toMatchObject({ type: 'sse', url: ctx.servers[0].sseUrl })
    expect(parse(buildClientConfig('cline', sse)).mcpServers['clio-default'].type).toBe('sse')
    expect(parse(buildClientConfig('gemini', sse)).mcpServers['clio-default']).toHaveProperty('url', ctx.servers[0].sseUrl)
    expect(parse(buildClientConfig('gemini', sse)).mcpServers['clio-default']).not.toHaveProperty('httpUrl')
    expect(buildClientConfig('goose', sse).content).toContain('type: sse')
    expect(buildClientConfig('claude', sse, { format: 'cli' }).content).toContain('--transport sse')
    // A client that documents streamable HTTP only keeps the standard endpoint and says so.
    const codex = buildClientConfig('codex', sse)
    expect(codex.sseSupported).toBe(false)
    expect(codex.content).toContain(ctx.servers[0].url)
    expect(codex.note).toMatch(/streamable HTTP only/)
    expect(formatsFor(findClient('codex'), 'sse').length).toBeGreaterThan(0)
  })

  it('the mcp-remote bridge sends the header through an environment variable, as its README shows', () => {
    const desktop = parse(buildClientConfig('desktop', ctx)).mcpServers['clio-default']
    expect(desktop.command).toBe('npx')
    expect(desktop.args).toEqual(['-y', 'mcp-remote', ctx.servers[0].url, '--header', 'Authorization:${AUTH_HEADER}'])
    expect(desktop.env.AUTH_HEADER).toBe('Bearer clio_abc123')
    expect(parse(buildClientConfig('desktop', envCtx)).mcpServers['clio-default'].env.AUTH_HEADER).toBe('Bearer ${CLIO_TOKEN}')
    expect(parse(buildClientConfig('desktop', noneCtx)).mcpServers['clio-default']).not.toHaveProperty('env')
    expect(parse(buildClientConfig('generic', ctx, { format: 'bridge' })).mcpServers['clio-default'].args).toContain('mcp-remote')
  })

  it('the API recipes carry both halves the vendors require', () => {
    const anthropic = buildClientConfig('anthropic-api', ctx).content
    expect(anthropic).toContain('mcp_servers=')
    expect(anthropic).toContain('"type": "mcp_toolset"')
    expect(anthropic).toContain('mcp-client-2025-11-20')
    const openai = buildClientConfig('openai-api', ctx).content
    expect(openai).toContain('"type": "mcp"')
    expect(openai).toContain('"server_url"')
    const agents = buildClientConfig('openai-agents', ctx).content
    expect(agents).toContain('from agents.mcp import MCPServerStreamableHttp')
    expect(agents).toContain('mcp_servers=[server]')
    const sdk = buildClientConfig('claude-agent-sdk', ctx, { format: 'ts' }).content
    expect(sdk).toContain('allowedTools: ["mcp__clio-default__*"]')
    expect(buildClientConfig('vercel-ai', ctx).content).toContain('import { createMCPClient } from "@ai-sdk/mcp"')
    expect(buildClientConfig('pydantic-ai', ctx).content).toContain('toolsets=[')
    expect(buildClientConfig('google-adk', ctx).content).toContain('StreamableHTTPConnectionParams(')
  })

  it('hosted connectors explain the OAuth requirement when sign-in is off', () => {
    const off = buildClientConfig('claude-ai', ctx)
    expect(off.hint).toMatch(/identity provider/i)
    const on = buildClientConfig('claude-ai', { ...ctx, oauthEnabled: true })
    expect(on.hint).not.toMatch(/no identity provider configured/i)
  })

  it('the generic entry mentions the legacy SSE endpoint and unknown keys fall back to the first client', () => {
    expect(buildClientConfig('generic', ctx).content).toContain(ctx.servers[0].sseUrl)
    expect(findClient('no-such-client').key).toBe(MCP_CLIENTS[0].key)
    expect(buildClientConfig('claude', ctx, { format: 'no-such-format' }).format).toBe('file')
  })
})
