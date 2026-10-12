import { beforeEach, describe, expect, it, vi } from 'vitest'

// axios is imported dynamically inside the module under test, so the mock has
// to be in place before those imports resolve.
const get = vi.fn()
vi.mock('axios', () => ({ default: { get } }))

import {
  buildProviderHeaders,
  fetchDeploymentDefault,
  fetchServerProviderIds,
  getConfiguredProviderIds,
  getProviderConfig,
  getProviderDisplayName,
  isDeploymentProvider,
  resolveProvider,
  retireSurfaceOverrides,
  setActiveProviderLS,
  setProviderModel,
  upsertProviderConfig,
} from '../aiProviders'

const DEPLOYMENT = {
  configured: true,
  provider: 'openai_compatible',
  label: 'Acme Internal LLM',
  model: 'llama-3.3-70b',
  base_url: 'http://vllm.internal:8000/v1',
  key_configured: true,
}

/** Serve /api/ai/deployment and /api/agent/config from one fake. */
function server({ deployment = { configured: false }, teamKeys = {} } = {}) {
  get.mockImplementation((url) => {
    if (url === '/api/ai/deployment') return Promise.resolve({ data: deployment })
    if (url === '/api/agent/config') return Promise.resolve({ data: { providers: teamKeys } })
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
}

describe('deployment-configured provider', () => {
  beforeEach(async () => {
    localStorage.clear()
    get.mockReset()
    // Reset module-level caches to "nothing configured on the server".
    server()
    await fetchDeploymentDefault()
    await fetchServerProviderIds()
  })

  it('is not offered when the server has none', () => {
    expect(getConfiguredProviderIds()).toEqual([])
    expect(resolveProvider()).toBe('')
    expect(isDeploymentProvider('openai_compatible')).toBe(false)
  })

  it('makes a browser that configured nothing resolve to the server’s model', async () => {
    server({ deployment: DEPLOYMENT })
    await fetchDeploymentDefault()

    expect(getConfiguredProviderIds()).toEqual(['openai_compatible'])
    expect(resolveProvider()).toBe('openai_compatible')
    expect(resolveProvider()).toBe('openai_compatible')
    expect(getProviderDisplayName('openai_compatible')).toBe('Acme Internal LLM')
  })

  it('sends no credentials for it — the server holds the endpoint and key', async () => {
    server({ deployment: DEPLOYMENT })
    await fetchDeploymentDefault()

    expect(buildProviderHeaders('openai_compatible')).toEqual({})
    // …but a model override still travels, for an endpoint serving several.
    expect(buildProviderHeaders('openai_compatible', 'mixtral')).toEqual({ 'X-AI-Model': 'mixtral' })
  })

  it('yields to a provider this browser explicitly chose', async () => {
    server({ deployment: DEPLOYMENT })
    await fetchDeploymentDefault()
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    setActiveProviderLS('anthropic')

    expect(resolveProvider()).toBe('anthropic')
    expect(buildProviderHeaders('anthropic')).toEqual({ 'X-AI-Key': 'sk-ant-test' })
  })

  it('is listed once when it is also a team-key provider', async () => {
    server({
      deployment: { ...DEPLOYMENT, provider: 'anthropic', label: 'Anthropic' },
      teamKeys: { anthropic: { configured: true } },
    })
    await fetchDeploymentDefault()
    await fetchServerProviderIds()

    expect(getConfiguredProviderIds()).toEqual(['anthropic'])
  })

  it('survives a server that cannot answer', async () => {
    get.mockImplementation(() => Promise.reject(new Error('offline')))
    await fetchDeploymentDefault()

    expect(getConfiguredProviderIds()).toEqual([])
  })
})

describe('one provider choice for every surface', () => {
  beforeEach(async () => {
    localStorage.clear()
    get.mockReset()
    server()
    await fetchDeploymentDefault()
    await fetchServerProviderIds()
  })

  it('stores the model on the provider and sends it in the headers', () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    setProviderModel('anthropic', 'claude-x')
    expect(getProviderConfig('anthropic').model).toBe('claude-x')
    expect(buildProviderHeaders('anthropic')['X-AI-Model']).toBe('claude-x')
    setProviderModel('anthropic', '')
    expect(getProviderConfig('anthropic').model).toBe('')
  })

  it('retires per-surface overrides, promoting the chat one to the global default', () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    upsertProviderConfig('openai', { apiKey: 'sk-test' })
    setActiveProviderLS('anthropic')
    localStorage.setItem('clio_provider_override_chat', 'openai')
    localStorage.setItem('clio_provider_override_search', 'anthropic')
    localStorage.setItem('clio_chat_model_overrides', '{"openai":"gpt-x"}')
    localStorage.setItem('clio_search_model_overrides', '{}')
    localStorage.setItem('clio_selected_providers', '["openai","anthropic"]')

    retireSurfaceOverrides()

    expect(resolveProvider()).toBe('openai')
    for (const key of [
      'clio_provider_override_chat', 'clio_provider_override_search',
      'clio_chat_model_overrides', 'clio_search_model_overrides', 'clio_selected_providers',
    ]) expect(localStorage.getItem(key)).toBeNull()
  })

  it('ignores a chat override that is no longer configured', () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    setActiveProviderLS('anthropic')
    localStorage.setItem('clio_provider_override_chat', 'grok')
    retireSurfaceOverrides()
    expect(resolveProvider()).toBe('anthropic')
  })
})
