import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import FeatureSettings from '../FeatureSettings.vue'
import http from '../../utils/http'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('../../utils/aiProviders', async (original) => ({
  ...(await original()),
  fetchDeploymentDefault: vi.fn().mockResolvedValue({ configured: false }),
  fetchServerProviderIds: vi.fn().mockResolvedValue([]),
}))

const FEATURES = ['semantic', 'reranker', 'ocr', 'transcription', 'figures', 'ai', 'research', 'links', 'git', 'mcp', 'pii']
  .map((id) => ({ id, label: id, state: 'on', detail: `${id} works.`, fix: '' }))

const CONFIG = {
  enable_reranker: null, enable_ocr: false, enable_transcription: true, whisper_model: 'base',
  extract_figures: true, research_lookup_enabled: true, research_contact_email: '',
  link_indexing_enabled: true, git_allowed_hosts: '', enable_mcp: true, pii_scan_action: 'label',
  ai_provider: 'openai', ai_model: 'gpt-x', ai_base_url: '', ai_provider_label: '', ai_api_key_set: true,
}

async function mountSettings(props = {}) {
  http.get.mockImplementation((url) => Promise.resolve({
    data: url === '/api/features' ? { features: FEATURES } : { ...CONFIG },
  }))
  http.post.mockResolvedValue({ data: { success: true } })
  const wrapper = mount(FeatureSettings, { props })
  await flushPromises()
  return wrapper
}

describe('FeatureSettings', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    http.get.mockReset()
    http.post.mockReset()
  })

  it('has a control for every feature the status bar reports', async () => {
    const wrapper = await mountSettings()
    for (const { id } of FEATURES) {
      const row = wrapper.get(`[data-feature-row="${id}"]`)
      expect(row.find('input, select, button').exists(), id).toBe(true)
    }
  })

  it('saves a switch as soon as it is flipped', async () => {
    const wrapper = await mountSettings()
    await wrapper.get('[data-testid="feature-toggle-links"]').setValue(false)
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/config', { link_indexing_enabled: false })
    expect(wrapper.emitted('changed')[0]).toEqual(['links'])
  })

  it('sends automatic for the reranker as no value, not as text', async () => {
    const wrapper = await mountSettings()
    await wrapper.get('[data-testid="feature-reranker"]').setValue('false')
    await wrapper.get('[data-testid="feature-reranker"]').setValue('auto')
    await flushPromises()
    expect(http.post.mock.calls.map(([, body]) => body)).toEqual([{ enable_reranker: false }, { enable_reranker: null }])
  })

  it('shows what the server refused and why', async () => {
    const wrapper = await mountSettings()
    http.post.mockRejectedValue(new Error('Not a host name: https://x'))
    await wrapper.get('[data-testid="feature-git-hosts"]').setValue('https://x')
    await wrapper.get('[data-testid="feature-git-save"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-feature-row="git"]').text()).toContain('Not a host name')
  })

  it('keeps the saved key when the provider stays, and drops it when the provider changes', async () => {
    const wrapper = await mountSettings()
    await wrapper.get('[data-testid="feature-ai-save"]').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls[0][1].ai_api_key).toBe('********')

    await wrapper.get('[data-testid="feature-ai-provider"]').setValue('anthropic')
    await wrapper.get('[data-testid="feature-ai-save"]').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls[1][1]).toMatchObject({ ai_provider: 'anthropic', ai_api_key: '' })
  })

  it('does not offer hosted providers in offline mode', async () => {
    http.get.mockReset()
    const wrapper = await mountSettings({ offline: true })
    const offered = wrapper.get('[data-testid="feature-ai-provider"]').findAll('option').map((o) => o.element.value)
    expect(offered).toContain('ollama')
    expect(offered).not.toContain('anthropic')
  })

  it('lets someone who is not an admin read but not change', async () => {
    const wrapper = await mountSettings({ locked: true })
    expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('admin-only')
  })
})
