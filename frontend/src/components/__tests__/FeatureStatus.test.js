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

  it('shows every feature with its state', async () => {
    const wrapper = await mountStatus()
    const state = (id) => wrapper.find(`[data-feature="${id}"]`).attributes('data-state')
    expect(state('semantic')).toBe('limited')
    expect(state('ocr')).toBe('off')
    expect(state('transcription')).toBe('unavailable')
    expect(state('mcp')).toBe('on')
    expect(wrapper.find('[data-feature="transcription"]').text()).toBe('Speech')
    wrapper.unmount()
  })

  it('says what each state is for a screen reader, not only by colour', async () => {
    const wrapper = await mountStatus()
    const label = wrapper.get('[data-testid="feature-strip"]').attributes('aria-label')
    expect(label).toContain('1 of 5 fully working')
    expect(label).toContain('Transcription: Unavailable')
    expect(label).toContain('OCR: Off')
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
    const wrapper = await mountStatus()
    expect(wrapper.find('[data-feature="ai"]').attributes('data-state')).toBe('off')
    wrapper.unmount()

    const store = useProviderStore()
    localStorage.setItem('ai_providers_config', JSON.stringify([{ id: 'ollama', available: true }]))
    localStorage.setItem('ai_settings', JSON.stringify({ provider: 'ollama' }))
    store.touch()
    expect(store.activeProviderId).toBe('ollama')
    const connected = await mountStatus()
    expect(connected.find('[data-feature="ai"]').attributes('data-state')).toBe('on')
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
