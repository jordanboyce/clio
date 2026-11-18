import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import FeatureStatus from '../FeatureStatus.vue'
import http from '../../utils/http'
import { useProviderStore } from '../../stores/providerStore'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn() } }))

const FEATURES = [
  { id: 'semantic', label: 'Semantic search', state: 'limited', detail: 'Keywords only.', fix: 'Pre-seed a model.' },
  { id: 'ocr', label: 'OCR', state: 'off', detail: 'Turned off.', fix: 'Turn it on.' },
  { id: 'transcription', label: 'Transcription', state: 'unavailable', detail: 'Model missing.', fix: '' },
  { id: 'ai', label: 'AI answers', state: 'off', detail: 'No model.', fix: 'Connect one.' },
  { id: 'mcp', label: 'MCP', state: 'on', detail: 'Agents connect.', fix: '' },
]

async function mountStatus(features = FEATURES) {
  http.get.mockResolvedValue({ data: { features } })
  const wrapper = mount(FeatureStatus, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

describe('FeatureStatus', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    http.get.mockReset()
  })

  it('names only what needs attention; what is switched off is not a fault (B6)', async () => {
    const wrapper = await mountStatus()
    const state = (id) => wrapper.find(`[data-feature="${id}"]`).attributes('data-state')
    expect(state('semantic')).toBe('limited')
    expect(state('transcription')).toBe('unavailable')
    expect(wrapper.find('[data-feature="transcription"]').text()).toBe('Speech')
    expect(wrapper.find('[data-feature="ocr"]').exists()).toBe(false)
    expect(wrapper.find('[data-feature="mcp"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="feature-summary"]').text()).toBe('2 need attention')
    wrapper.unmount()
  })

  it('reads All working with no warning colour when nothing is wrong, however much is off', async () => {
    const wrapper = await mountStatus([
      { id: 'semantic', label: 'Semantic search', state: 'on', detail: '', fix: '' },
      { id: 'ocr', label: 'OCR', state: 'off', detail: 'Turned off.', fix: 'Turn it on.' },
      { id: 'reranker', label: 'Reranker', state: 'off', detail: 'Turned off.', fix: '' },
      { id: 'pra', label: 'PRA models', state: 'off', detail: 'Off.', fix: '' },
    ])
    const summary = wrapper.get('[data-testid="feature-summary"]')
    expect(summary.text()).toBe('All working')
    expect(wrapper.get('[data-testid="feature-strip"]').html()).not.toMatch(/text-(warning|error)/)
    wrapper.unmount()
  })

  it('groups the panel as needs attention, on and switched off', async () => {
    const wrapper = await mountStatus()
    await wrapper.get('[data-testid="feature-strip"]').trigger('click')
    expect(wrapper.get('[data-testid="feature-group-attention"]').text()).toContain('Transcription')
    expect(wrapper.get('[data-testid="feature-group-on"]').text()).toContain('MCP')
    expect(wrapper.get('[data-testid="feature-group-off"]').text()).toContain('OCR')
    wrapper.unmount()
  })

  it('says what each state is for a screen reader, not only by colour', async () => {
    const wrapper = await mountStatus()
    const label = wrapper.get('[data-testid="feature-strip"]').attributes('aria-label')
    expect(label).toContain('2 need attention')
    expect(label).toContain('Transcription: Unavailable')
    expect(label).toContain('Semantic search: Limited')
    wrapper.unmount()
  })

  it('opens the reason and the fix on click, and closes on Escape', async () => {
    const wrapper = await mountStatus()
    expect(wrapper.find('[data-testid="feature-panel"]').exists()).toBe(false)

    await wrapper.get('[data-testid="feature-strip"]').trigger('click')
    const panel = wrapper.get('[data-testid="feature-panel"]')
    expect(panel.text()).toContain('Keywords only.')
    expect(panel.text()).toContain('Pre-seed a model.')

    // Sent to the document: focus is on the strip's button, not in the panel.
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('[data-testid="feature-panel"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('counts a model connected in this browser as AI answers being on', async () => {
    const aiOnly = [{ id: 'ai', label: 'AI answers', state: 'off', detail: 'No model.', fix: 'Connect one.' }]
    const wrapper = await mountStatus(aiOnly)
    await wrapper.get('[data-testid="feature-strip"]').trigger('click')
    expect(wrapper.get('[data-testid="feature-group-off"]').text()).toContain('AI answers')
    wrapper.unmount()

    const store = useProviderStore()
    localStorage.setItem('ai_providers_config', JSON.stringify([{ id: 'ollama', available: true }]))
    localStorage.setItem('ai_settings', JSON.stringify({ provider: 'ollama' }))
    store.touch()
    expect(store.activeProviderId).toBe('ollama')
    const connected = await mountStatus(aiOnly)
    await connected.get('[data-testid="feature-strip"]').trigger('click')
    expect(connected.get('[data-testid="feature-group-on"]').text()).toContain('AI answers')
    connected.unmount()
    localStorage.clear()
  })

  it('renders nothing rather than failing when the status cannot be read', async () => {
    http.get.mockRejectedValue(new Error('down'))
    const wrapper = mount(FeatureStatus)
    await flushPromises()
    expect(wrapper.find('[data-testid="feature-strip"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('treats a state it does not know as unavailable instead of crashing', async () => {
    const wrapper = await mountStatus([{ id: 'new', label: 'New', state: 'mystery', detail: '', fix: '' }])
    expect(wrapper.find('[data-feature="new"]').attributes('data-state')).toBe('unavailable')
    wrapper.unmount()
  })
})
