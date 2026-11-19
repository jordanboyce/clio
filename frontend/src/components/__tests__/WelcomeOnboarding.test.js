import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => {
  const http = { get: vi.fn(), post: vi.fn() }
  return { default: http, http }
})

const axiosPost = vi.fn()
vi.mock('axios', () => ({ default: { post: axiosPost, get: vi.fn(() => Promise.resolve({ data: {} })) } }))

import http from '../../utils/http'
import WelcomeOnboarding from '../WelcomeOnboarding.vue'
import { invalidateModelCache } from '../../utils/aiProviders'

// F20: Ollama lists its embedding models too, often first. The answer
// step must offer only models that can answer, and start on the server's
// default rather than whatever came first.
describe('Onboarding with Ollama', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    invalidateModelCache()
    http.get.mockReset()
    http.get.mockResolvedValue({ data: {} })
    axiosPost.mockReset()
  })

  it('offers chat models only and starts on the server’s default', async () => {
    const rec = (id, kinds, extra = {}) => ({ id, label: id, kinds, recommended: false, roles: [], ...extra })
    axiosPost.mockResolvedValue({ data: {
      records: [
        rec('nomic-embed-text:latest', ['embedding']),
        rec('gemma3:12b', ['chat'], { vision: true }),
        rec('qwen3:8b', ['chat'], { tools: true }),
      ],
      defaults: { model: 'qwen3:8b', fast: 'qwen3:8b', vision: 'gemma3:12b' },
      listed: true,
      error: null,
    } })
    const wrapper = mount(WelcomeOnboarding, { props: { show: true }, global: { stubs: { EmbeddingSetup: true } } })
    await flushPromises()

    await wrapper.findAll('button').find((b) => b.text().includes('Ollama')).trigger('click')
    await wrapper.findAll('button').find((b) => b.text().trim() === 'Detect').trigger('click')
    await flushPromises()

    const select = wrapper.get('select[aria-label="Model"]')
    expect(select.findAll('option').map((o) => o.element.value)).toEqual(['gemma3:12b', 'qwen3:8b'])
    expect(select.element.value).toBe('qwen3:8b')
  })
})

// F15: the step indicator only lists steps this screen can reach. Adding
// sources happens in the Sources panel after it closes, so it is not a step.
describe('Onboarding steps', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    http.get.mockReset()
  })

  const steps = (wrapper) => wrapper.findAll('ol[aria-label="Setup steps"] > li').map((li) => ({
    label: li.text().replace(/^\d\s*/, ''),
    current: li.find('[aria-current="step"]').exists(),
  }))

  it('can make every step it lists the current one', async () => {
    http.get.mockImplementation(async (url) => (url === '/api/embedding/status'
      ? { data: { status: 'not_configured' } }
      : { data: {} }))
    const wrapper = mount(WelcomeOnboarding, { props: { show: true }, global: { stubs: { EmbeddingSetup: true } } })
    await flushPromises()

    expect(steps(wrapper)).toEqual([
      { label: 'Answers', current: true },
      { label: 'Search index', current: false },
    ])

    const skip = wrapper.findAll('button').find((b) => b.text().startsWith('Skip for now'))
    await skip.trigger('click')
    await flushPromises()
    expect(steps(wrapper).map((s) => s.current)).toEqual([false, true])
  })
})

describe('the onboarding behaves like the modal dialog it says it is (C2)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    http.get.mockReset()
    http.get.mockResolvedValue({ data: {} })
  })

  it('moves focus in, and Escape skips', async () => {
    const wrapper = mount(WelcomeOnboarding, { props: { show: true }, global: { stubs: { EmbeddingSetup: true } }, attachTo: document.body })
    await flushPromises()
    const dialog = wrapper.get('[role="dialog"]').element
    expect(dialog.contains(document.activeElement)).toBe(true)
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(wrapper.emitted('skip')).toBeTruthy()
    wrapper.unmount()
  })
})
