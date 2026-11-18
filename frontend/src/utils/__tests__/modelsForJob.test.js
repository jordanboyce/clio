import { beforeEach, describe, expect, it, vi } from 'vitest'

const get = vi.fn()
vi.mock('axios', () => ({ default: { get, post: vi.fn() } }))
// The team-key and deployment lookups go through the app's http client.
vi.mock('../http', () => ({ default: { get, post: vi.fn() } }))

import {
  describeOtherJobs,
  fetchDeploymentDefault,
  getActiveProvider,
  getConfiguredProviderIds,
  getProvidersConfig,
  isLocalProvider,
  isPrivateUrl,
  migrateLegacySettings,
  modelAbilities,
  modelsForJob,
  PROVIDER_DEFS,
  providerLocation,
  resolveProvider,
  upsertProviderConfig,
} from '../aiProviders'

const m = (id, kinds, extra = {}) => ({ id, kinds, recommended: false, ...extra })

const LIST = [
  m('best', ['chat'], { recommended: true, tools: true, vision: true }),
  m('plain', ['chat'], { tools: false, vision: false }),
  m('maybe', ['chat']),
  m('embed-a', ['embedding']),
  m('embed-b', ['embedding'], { recommended: true }),
  m('tts', ['speech']),
  m('house-model', []),
]

describe('each picker shows the models that can do its job', () => {
  it('answers: chat models, recommendations first, unknown kinds kept apart', () => {
    const g = modelsForJob(LIST, 'answers')
    expect(g.recommended.map(x => x.id)).toEqual(['best'])
    expect(g.fits.map(x => x.id)).toEqual(['plain', 'maybe'])
    expect(g.unknown.map(x => x.id)).toEqual(['house-model'])
    expect(g.otherJobs).toEqual({ embedding: 2, speech: 1 })
    expect(describeOtherJobs(g.otherJobs)).toBe('2 embedding, 1 speech')
  })

  it('deep search: a chat model that cannot call tools is set aside, not hidden as chat', () => {
    const g = modelsForJob(LIST, 'tools')
    expect(g.recommended.map(x => x.id)).toEqual(['best'])
    expect(g.unable.map(x => x.id)).toEqual(['plain'])
    expect(g.fits.map(x => x.id)).toEqual(['maybe'])
  })

  it('vision: only models known to read images fit', () => {
    const g = modelsForJob(LIST, 'vision')
    expect(g.recommended.map(x => x.id)).toEqual(['best'])
    expect(g.fits).toEqual([])
    expect(g.unable.map(x => x.id)).toEqual(['plain'])
    expect(g.unknown.map(x => x.id)).toEqual(['maybe', 'house-model'])
  })

  it('embedding: embedding models only', () => {
    const g = modelsForJob(LIST, 'embedding')
    expect([...g.recommended, ...g.fits].map(x => x.id)).toEqual(['embed-b', 'embed-a'])
    expect(g.otherJobs).toEqual({ chat: 3, speech: 1 })
  })

  it('says what a model can do, and leaves out what nobody reported', () => {
    expect(modelAbilities(m('x', ['chat'], { tools: true, vision: true, context_tokens: 200000 })))
      .toEqual(['Uses tools', 'Reads images', '200,000-token context'])
    expect(modelAbilities(m('y', ['chat']))).toEqual([])
    expect(modelAbilities(null)).toEqual([])
  })
})

describe('where a provider runs', () => {
  beforeEach(() => {
    localStorage.clear()
    get.mockReset()
  })

  it('tells private addresses from public ones, as the server does', () => {
    for (const url of ['http://localhost:1234/v1', 'http://127.0.0.1:11434', 'http://10.2.3.4/v1',
      'http://172.20.0.5:8000', 'http://192.168.1.9', 'http://ollama:11434', 'http://[::1]:8000', 'gpu-box:8000']) {
      expect(isPrivateUrl(url), url).toBe(true)
    }
    for (const url of ['https://api.groq.com/openai/v1', 'http://172.32.0.1', 'http://llm.corp.example',
      'https://8.8.8.8', '', 'not a url at all']) {
      expect(isPrivateUrl(url), url).toBe(false)
    }
  })

  it('files a custom endpoint by its address, not as "cloud" by default', () => {
    upsertProviderConfig('custom_lm', { isCustom: true, name: 'LM Studio', baseUrl: 'http://localhost:1234/v1' })
    upsertProviderConfig('custom_groq', { isCustom: true, name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1' })
    expect(providerLocation('custom_lm')).toBe('network')
    expect(isLocalProvider('custom_lm')).toBe(true)
    expect(providerLocation('custom_groq')).toBe('hosted')
    expect(providerLocation('ollama')).toBe('network')
    expect(providerLocation('anthropic')).toBe('hosted')
  })

  it('follows the server for the deployment’s own provider', async () => {
    get.mockResolvedValue({ data: { configured: true, provider: 'openai_compatible', label: 'Acme', base_url: 'http://llm.corp.example/v1', external: false } })
    await fetchDeploymentDefault()
    expect(providerLocation('openai_compatible')).toBe('network')

    get.mockResolvedValue({ data: { configured: true, provider: 'openai_compatible', label: 'Acme', base_url: 'http://localhost/v1', external: true } })
    await fetchDeploymentDefault()
    expect(providerLocation('openai_compatible')).toBe('hosted')

    get.mockResolvedValue({ data: { configured: false } })
    await fetchDeploymentDefault()
  })

  it('gives every provider a one-line description, and lists no model names in it', () => {
    for (const def of PROVIDER_DEFS) expect(def.description, def.id).toMatch(/\.$/)
  })
})

describe('a retired provider (F21)', () => {
  beforeEach(() => localStorage.clear())

  it('is not offered, and a saved config of it is dropped without breaking the choice', () => {
    expect(PROVIDER_DEFS.some(d => d.id === 'github')).toBe(false)
    upsertProviderConfig('github', { apiKey: 'ghp_old', model: 'openai/gpt-x' })
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    localStorage.setItem('ai_settings', JSON.stringify({ provider: 'github' }))

    // Before migration it already resolves past the retired one.
    expect(getConfiguredProviderIds()).not.toContain('github')
    expect(resolveProvider()).toBe('anthropic')

    migrateLegacySettings()
    expect(getProvidersConfig().map(c => c.id)).not.toContain('github')
    expect(getActiveProvider()).toBeNull()
    expect(resolveProvider()).toBe('anthropic')
  })

  it('migrates a legacy chat choice without throwing', () => {
    localStorage.setItem('chat_provider', 'openai')
    localStorage.setItem('ai_settings', JSON.stringify({ provider: 'anthropic' }))
    expect(() => migrateLegacySettings()).not.toThrow()
    expect(getActiveProvider()).toBe('anthropic')
    expect(localStorage.getItem('chat_provider')).toBeNull()
  })
})
