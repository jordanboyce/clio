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

const event = (id) => ({
  id, timestamp: '2026-11-18T10:00:00', actor: 'a@example.com', action: 'config.update',
  auth_method: 'password', source_ip: '10.0.0.7', request_id: `r${id}`,
})

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  http.get.mockImplementation((url, opts) => {
    if (url === '/api/admin/audit') {
      const before = opts?.params?.before
      if (!before) return Promise.resolve({ data: { events: [event(4), event(3)], actions: [], next_before: 3 } })
      return Promise.resolve({ data: { events: [event(2), event(1)], actions: [], next_before: null } })
    }
    if (url === '/api/admin/review') return Promise.resolve({ data: review })
    if (url === '/api/admin/blocked-hashes') return Promise.resolve({ data: { hashes: [] } })
    if (url === '/api/admin/aup-acknowledgements') return Promise.resolve({ data: { acknowledgements: [] } })
    if (url === '/api/admin/users/suspended') return Promise.resolve({ data: { suspended: [] } })
    if (url.startsWith('/api/admin/usage')) return Promise.resolve({ data: { by_user: [], by_day: [] } })
    return Promise.resolve({ data: {} })
  })
})

describe('audit trail paging', () => {
  it('shows older events from the cursor until there are none', async () => {
    const w = mount(AdminTab, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const rows = () => w.findAll('section[aria-labelledby="admin-audit"] tbody tr')
    expect(rows()).toHaveLength(2)
    expect(rows()[0].find('td:nth-child(2)').attributes('title')).toContain('via password · from 10.0.0.7')

    await w.get('[data-testid="audit-older"]').trigger('click')
    await flushPromises()

    expect(http.get).toHaveBeenCalledWith('/api/admin/audit', { params: { limit: 200, before: 3 } })
    expect(rows()).toHaveLength(4)
    expect(w.find('[data-testid="audit-older"]').exists()).toBe(false)
  })
})
