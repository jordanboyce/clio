import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

import http from '../../utils/http'
import AdminTab from '../AdminTab.vue'

const review = {
  content_policy_action: 'flag', injection_scan_action: 'review', pii_scan_action: 'label',
  pii_engine: { engine: 'regex' }, injection_tuning: { muted: [], allow_phrases: [], noisy: [] },
  items: [], summary: { pending: 0 }, quarantined: [], flagged: [], reports: [], blocked_hashes: 0,
}

let suspended

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
  suspended = [{ identity: 'gone@example.com', suspended_by: 'admin@example.com', suspended_at: '2026-11-18T10:00:00', reason: 'left' }]
  http.get.mockImplementation((url) => {
    if (url === '/api/admin/review') return Promise.resolve({ data: review })
    if (url === '/api/admin/audit') return Promise.resolve({ data: { events: [], actions: [] } })
    if (url === '/api/admin/blocked-hashes') return Promise.resolve({ data: { hashes: [] } })
    if (url === '/api/admin/aup-acknowledgements') return Promise.resolve({ data: { acknowledgements: [] } })
    if (url === '/api/admin/users/suspended') return Promise.resolve({ data: { suspended } })
    if (url.startsWith('/api/admin/usage')) return Promise.resolve({ data: { by_user: [], by_day: [] } })
    return Promise.resolve({ data: {} })
  })
  http.post.mockImplementation((url) => {
    if (url.endsWith('/reinstate')) {
      suspended = []
      return Promise.resolve({ data: { reinstated: true } })
    }
    return Promise.resolve({ data: { tokens_revoked: 0 } })
  })
})

describe('suspended identities', () => {
  it('lists who is suspended and reinstates one', async () => {
    const w = mount(AdminTab, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const list = w.get('[aria-label="Suspended identities"]')
    expect(list.text()).toContain('gone@example.com')

    await list.get('button').trigger('click')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/api/admin/users/gone%40example.com/reinstate')
    expect(w.find('[aria-label="Suspended identities"]').exists()).toBe(false)
  })
})
