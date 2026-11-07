import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn(() => Promise.resolve({ data: {} })) },
}))
import SearchTab from '../SearchTab.vue'
import { useStatsStore } from '../../stores/statsStore'

// Find's opening screen: example searches only where there is something to
// find. An empty collection gets the one step that changes that instead.
async function mountWith(chunks) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useStatsStore().chunks = chunks
  const w = mount(SearchTab, {
    global: { plugins: [pinia], stubs: { SearchSettings: true, SlashCommandPicker: true } },
  })
  await flushPromises()
  return w
}

describe('SearchTab empty state', () => {
  beforeEach(() => { localStorage.clear() })

  it('offers Add sources, not example searches, in an empty collection', async () => {
    const w = await mountWith(0)
    const add = w.get('[data-testid="find-add-sources"]')
    expect(w.text()).not.toContain('What are the key requirements?')
    await add.trigger('click')
    expect(w.emitted('add-sources')).toHaveLength(1)
  })

  it('offers example searches once there is something to search', async () => {
    const w = await mountWith(12)
    expect(w.find('[data-testid="find-add-sources"]').exists()).toBe(false)
    expect(w.text()).toContain('What are the key requirements?')
  })
})
