import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => {
  const http = { get: vi.fn(), post: vi.fn() }
  return { default: http, http }
})

import http from '../../utils/http'
import WelcomeOnboarding from '../WelcomeOnboarding.vue'

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
