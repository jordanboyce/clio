import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

import http from '../../utils/http'
import AdminTab from '../AdminTab.vue'

const item = (id, over = {}) => ({
  collection_id: 'default', document_id: id, filename: `${id}.pdf`, collection_name: 'Default',
  upload_timestamp: '2026-10-01T00:00:00', kinds: ['injection'], priority: 'high', policy_status: 'clear',
  reasons: [{ kind: 'injection', text: 'Text on 1 page tries to override the assistant.' }],
  injection: {
    level: 'high', flagged_pages: 1, max_score: 0.9,
    top: [{ page: '1', category: 'instruction_override', severity: 'high', pattern_name: 'ignore_instructions', matched_text: 'ignore all previous instructions' }],
  },
  reports: null, ...over,
})

const review = (items) => ({
  content_policy_action: 'flag', injection_scan_action: 'review', pii_scan_action: 'label',
  pii_engine: { engine: 'regex' }, injection_tuning: { muted: [], allow_phrases: [], noisy: [] },
  items, summary: { pending: items.length, injection: items.length }, quarantined: [], flagged: [], reports: [], blocked_hashes: 0,
})

const tuning = { muted: [], allow_phrases: [], noisy: [], noisy_rule: { min_dismissals: 5, dismiss_ratio: 0.8 }, patterns: [
  { name: 'ignore_instructions', category: 'instruction_override', severity: 'high', dismissed: 2, confirmed: 0, muted: false, noisy: false },
] }

function wireHttp(items) {
  http.get.mockImplementation((url) => {
    if (url === '/api/admin/review') return Promise.resolve({ data: review(items) })
    if (url === '/api/admin/injection-tuning') return Promise.resolve({ data: tuning })
    if (url === '/api/admin/audit') return Promise.resolve({ data: { events: [], actions: [] } })
    if (url === '/api/admin/blocked-hashes') return Promise.resolve({ data: { hashes: [] } })
    if (url === '/api/admin/aup-acknowledgements') return Promise.resolve({ data: { acknowledgements: [] } })
    if (url === '/api/config') return Promise.resolve({ data: { pii_scan_action: 'flag', pii_flag_threshold: 25 } })
    if (url.startsWith('/api/admin/usage')) return Promise.resolve({ data: { by_user: [], by_day: [] } })
    return Promise.resolve({ data: {} })
  })
  http.post.mockImplementation((url) => {
    if (url === '/api/admin/review/resolve') return Promise.resolve({ data: { resolved: 2, skipped: [] } })
    if (url === '/api/admin/review/remove') return Promise.resolve({ data: { removed: 2, blocked: 2, skipped: [] } })
    if (url === '/api/admin/injection-tuning') return Promise.resolve({ data: { ...tuning, muted: ['ignore_instructions'] } })
    return Promise.resolve({ data: {} })
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  window.confirm = vi.fn(() => true)
})

const mountAdmin = async (items) => {
  wireHttp(items)
  const w = mount(AdminTab, { global: { stubs: { teleport: true } } })
  await flushPromises()
  return w
}

const postsTo = (url) => http.post.mock.calls.filter((c) => c[0] === url).map((c) => c[1])

describe('review queue bulk decisions', () => {
  it('selecting rows shows a bulk bar and approves them in one call', async () => {
    const w = await mountAdmin([item('a'), item('b'), item('c')])
    expect(w.find('[data-testid="bulk-bar"]').exists()).toBe(false)

    const boxes = w.findAll('[data-testid="row-select"]')
    expect(boxes).toHaveLength(3)
    await boxes[0].setValue(true)
    await boxes[2].setValue(true)
    expect(w.get('[data-testid="bulk-bar"]').text()).toContain('2 selected')

    await w.get('[data-testid="bulk-approve"]').trigger('click')
    await flushPromises()
    const [body] = postsTo('/api/admin/review/resolve')
    expect(body.items.map((i) => i.document_id)).toEqual(['a', 'c'])
    expect(window.confirm).toHaveBeenCalled()
  })

  it('"select all shown" follows the filter and removes with the block flag', async () => {
    const w = await mountAdmin([item('a'), item('b')])
    await w.get('[data-testid="select-all-shown"]').setValue(true)
    expect(w.get('[data-testid="bulk-bar"]').text()).toContain('2 selected')

    await w.get('[data-testid="bulk-remove-block"]').trigger('click')
    await flushPromises()
    const [body] = postsTo('/api/admin/review/remove')
    expect(body.block).toBe(true)
    expect(body.items.map((i) => i.document_id)).toEqual(['a', 'b'])
    expect(window.confirm.mock.calls[0][0]).toContain('block')
  })

  it('"remove selected" without blocking passes block=false and respects a cancelled confirm', async () => {
    const w = await mountAdmin([item('a'), item('b')])
    await w.get('[data-testid="select-all-shown"]').setValue(true)
    window.confirm = vi.fn(() => false)
    await w.get('[data-testid="bulk-remove"]').trigger('click')
    await flushPromises()
    expect(postsTo('/api/admin/review/remove')).toHaveLength(0)

    window.confirm = vi.fn(() => true)
    await w.get('[data-testid="bulk-remove"]').trigger('click')
    await flushPromises()
    expect(postsTo('/api/admin/review/remove')[0].block).toBe(false)
  })
})

describe('scanner tuning from the evidence', () => {
  it('mutes the rule that flagged a document and allows its wording', async () => {
    const w = await mountAdmin([item('a')])
    await w.find('button[aria-expanded]').trigger('click')
    await w.get('[data-testid="mute-pattern"]').trigger('click')
    await flushPromises()
    expect(postsTo('/api/admin/injection-tuning')[0]).toEqual({ action: 'mute', pattern: 'ignore_instructions' })

    // the evidence stays open across the reload, so the wording button is still there
    await w.get('[data-testid="allow-wording"]').trigger('click')
    await flushPromises()
    expect(postsTo('/api/admin/injection-tuning')[1]).toEqual({ action: 'allow', phrase: 'ignore all previous instructions' })
    // the queue is reloaded after every tuning change
    expect(http.get.mock.calls.filter((c) => c[0] === '/api/admin/review').length).toBeGreaterThan(2)
  })

  it('loads the policy form with the personal-data settings', async () => {
    const w = await mountAdmin([])
    expect(w.get('[data-testid="pii-action"]').element.value).toBe('flag')
    expect(w.text()).toContain('personal data: label')
    expect(w.text()).toContain('Built-in patterns')
  })
})
