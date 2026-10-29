import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia } from 'pinia'
import MCPTab from '../MCPTab.vue'
import http from '../../utils/http'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

const existingToken = { id: 'existing', name: 'Other device' }

beforeEach(() => {
  vi.resetAllMocks()
  http.get.mockImplementation(async (path) => ({ data: {
    '/api/collections': { collections: [{ id: 'research & notes', name: 'Research' }] },
    '/api/mcp/config': { enable_mcp: true },
    '/api/mcp/tokens': { tokens: [existingToken] },
  }[path] }))
})

async function openAnythingLLM() {
  const wrapper = mount(MCPTab, { global: { plugins: [createPinia()] } })
  await flushPromises()
  await wrapper.findAll('button').find(button => button.text() === 'AnythingLLM').trigger('click')
  return wrapper
}

function exportedServer(wrapper) {
  return Object.values(JSON.parse(wrapper.get('pre').text()).mcpServers)[0]
}

describe('MCP client setup', () => {
  it('exports AnythingLLM Streamable HTTP configuration with an encoded collection and no invented credential', async () => {
    const wrapper = await openAnythingLLM()
    const server = exportedServer(wrapper)
    expect(server.type).toBe('streamable')
    expect(new URL(server.url).searchParams.get('collection_id')).toBe('research & notes')
    expect(server.headers.Authorization).toContain('Bearer <PASTE_YOUR_TOKEN')
    expect(server.command).toBeUndefined()
    expect(wrapper.text()).toContain('anythingllm_mcp_servers.json')
    expect(wrapper.text()).toContain('storage/plugins')
    wrapper.unmount()
  })

  it('creates a read-only token and never substitutes its secret for another device token', async () => {
    const wrapper = await openAnythingLLM()
    http.post.mockResolvedValue({ data: { id: 'new', token: 'test-only-secret' } })
    http.get.mockResolvedValue({ data: { tokens: [existingToken, { id: 'new', name: 'AnythingLLM' }] } })
    await wrapper.get('#mcp-token-name').setValue('AnythingLLM')
    await wrapper.findAll('button').find(button => button.text() === 'Generate token').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/mcp/tokens', {
      name: 'AnythingLLM', collection_scope: [], can_write: false, expires_in_days: 90, allowlist: false,
    })
    expect(exportedServer(wrapper).headers.Authorization).toBe('Bearer test-only-secret')
    await wrapper.get('#mcp-export-token').setValue('existing')
    expect(wrapper.get('pre').text()).not.toContain('test-only-secret')
    expect(exportedServer(wrapper).headers.Authorization).toContain('Bearer <PASTE_YOUR_TOKEN')
    wrapper.unmount()
  })

  it('lists every connection fact on its own with a copy button', async () => {
    const wrapper = await openAnythingLLM()
    const details = wrapper.get('[data-testid="mcp-connection-details"]')
    const labels = details.findAll('dt').map((d) => d.text())
    expect(labels).toEqual(expect.arrayContaining(['Server URL', 'Legacy SSE URL', 'Authorization header', 'Collection id', 'Tool catalog']))
    expect(details.find('[aria-label="Copy Server URL"]').exists()).toBe(true)
    expect(details.text()).toContain('/mcp?collection_id=research%20%26%20notes')
    wrapper.unmount()
  })

  it('hands over just the server entry when asked, and whole files otherwise', async () => {
    const wrapper = await openAnythingLLM()
    expect(JSON.parse(wrapper.get('pre').text())).toHaveProperty('mcpServers')
    await wrapper.get('#mcp-export-entry-only').setValue(true)
    const entry = wrapper.get('pre').text()
    expect(entry.startsWith('"clio-research-notes": {')).toBe(true)
    expect(entry).not.toContain('mcpServers')
    wrapper.unmount()
  })

  it('writes the token as an environment reference, in the client\'s syntax, when asked', async () => {
    const wrapper = await openAnythingLLM()
    await wrapper.findAll('button').find(button => button.text() === 'Cursor').trigger('click')
    await wrapper.get('#mcp-export-credential').setValue('env')
    await wrapper.get('#mcp-export-envvar').setValue('MY_CLIO_TOKEN')
    expect(exportedServer(wrapper).headers.Authorization).toBe('Bearer ${env:MY_CLIO_TOKEN}')
    await wrapper.get('#mcp-export-credential').setValue('none')
    expect(exportedServer(wrapper).headers).toBeUndefined()
    wrapper.unmount()
  })

  it('offers each client\'s documented formats and switches between them', async () => {
    const wrapper = await openAnythingLLM()
    await wrapper.findAll('button').find(button => button.text() === 'Claude Code').trigger('click')
    const tabs = wrapper.get('[data-testid="mcp-export-formats"]').findAll('[role="tab"]')
    expect(tabs.map((t) => t.text())).toEqual(['.mcp.json', 'Command line'])
    await tabs[1].trigger('click')
    expect(wrapper.get('pre').text()).toContain('claude mcp add --transport http')
    expect(wrapper.text()).toContain('terminal')
    // Picking another client goes back to its first format.
    await wrapper.findAll('button').find(button => button.text() === 'Codex').trigger('click')
    expect(wrapper.get('pre').text()).toContain('[mcp_servers.clio-research-notes]')
    wrapper.unmount()
  })

  it('names the server after the typed name and makes one entry per ticked collection', async () => {
    http.get.mockImplementation(async (path) => ({ data: {
      '/api/collections': { collections: [{ id: 'research', name: 'Research' }, { id: 'runbooks', name: 'Runbooks' }] },
      '/api/mcp/config': { enable_mcp: true },
      '/api/mcp/tokens': { tokens: [existingToken] },
    }[path] }))
    const wrapper = mount(MCPTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    await wrapper.findAll('button').find(button => button.text() === 'Claude Code').trigger('click')
    await wrapper.get('#mcp-export-name').setValue('kb')
    expect(Object.keys(JSON.parse(wrapper.get('pre').text()).mcpServers)).toEqual(['kb'])
    await wrapper.get('[aria-label="Collection Runbooks"]').setValue(true)
    const servers = JSON.parse(wrapper.get('pre').text()).mcpServers
    expect(Object.keys(servers)).toEqual(['kb-research', 'kb-runbooks'])
    expect(servers['kb-runbooks'].url).toContain('collection_id=runbooks')
    // Nothing ticked: a single unscoped server the agent scopes itself.
    await wrapper.get('[aria-label="Collection Research"]').setValue(false)
    await wrapper.get('[aria-label="Collection Runbooks"]').setValue(false)
    expect(JSON.parse(wrapper.get('pre').text()).mcpServers.kb.url).toMatch(/\/mcp$/)
    wrapper.unmount()
  })
})

describe('OAuth sign-in from the client', () => {
  it('shows the one URL to paste when the deployment names an authorization server', async () => {
    http.get.mockImplementation(async (path) => ({ data: {
      '/api/collections': { collections: [{ id: 'default', name: 'Default' }] },
      '/api/mcp/config': { enable_mcp: true },
      '/api/mcp/tokens': { tokens: [] },
      '/api/mcp/oauth': {
        enabled: true, resource: 'https://clio.agency.test/mcp',
        authorization_server: 'https://sso.agency.test/realms/main', scope: 'clio:mcp',
      },
    }[path] }))
    const wrapper = mount(MCPTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    const panel = wrapper.get('[data-testid="mcp-oauth"]')
    expect(panel.get('#mcp-oauth-url').text()).toBe('https://clio.agency.test/mcp')
    expect(panel.text()).toContain('sso.agency.test')
    expect(panel.text()).toContain('clio:mcp')
    expect(panel.text()).not.toContain('PASTE_YOUR_TOKEN')
    expect(wrapper.text()).toContain('Claude Desktop, claude.ai, ChatGPT')
    wrapper.unmount()
  })

  it('stays silent when OAuth is not configured, so token setup is the only story', async () => {
    const wrapper = mount(MCPTab, { global: { plugins: [createPinia()] } })
    await flushPromises()
    expect(wrapper.find('[data-testid="mcp-oauth"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Sign in from your client')
    wrapper.unmount()
  })
})
