import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn() } }))

import http from '../../utils/http'
import AboutDialog from '../AboutDialog.vue'

const report = (over = {}) => ({
  ready: true,
  profile: 'secure',
  offline_mode: true,
  counts: { pass: 10, warn: 1, fail: 0 },
  checks: [
    { id: 'offline', title: 'Offline mode', status: 'pass', detail: 'ok', fix: '' },
    { id: 'reranker', title: 'Result reranker', status: 'warn', detail: 'Not cached.', fix: 'Include it in the bundle.' },
  ],
  egress: [
    { kind: 'Embeddings', destination: 'in-process', state: 'none', note: '' },
    { kind: 'Web links', destination: 'disabled', state: 'blocked', note: '' },
  ],
  ...over,
})

beforeEach(() => {
  vi.clearAllMocks()
  http.get.mockImplementation((url) => {
    if (url === '/api/about') return Promise.resolve({ data: { version: '1.0', embedding_model: 'm', mcp_enabled: true, offline_mode: true, profile: 'secure' } })
    return Promise.resolve({ data: report() })
  })
})

const mountAbout = () => mount(AboutDialog, { props: { open: false } })

describe('About dialog deployment check', () => {
  it('loads nothing until asked, then lists only what needs attention', async () => {
    const wrapper = mountAbout()
    expect(http.get).not.toHaveBeenCalledWith('/api/system/readiness')

    await wrapper.get('[data-testid="readiness"] button').trigger('click')
    await flushPromises()

    const text = wrapper.get('[data-testid="readiness"]').text()
    expect(text).toContain('Ready')
    expect(text).toContain('10 passed, 1 to look at')
    expect(text).toContain('Result reranker.')
    expect(text).toContain('Include it in the bundle.')
    expect(text).not.toContain('Offline mode.') // passing checks stay out of the way
  })

  it('shows where data can go', async () => {
    const wrapper = mountAbout()
    await wrapper.get('[data-testid="readiness"] button').trigger('click')
    await flushPromises()
    const egress = wrapper.get('details').text()
    expect(egress).toContain('Embeddings')
    expect(egress).toContain('Web links')
  })

  it('says not ready when a check failed', async () => {
    http.get.mockImplementation(() => Promise.resolve({ data: report({ ready: false, counts: { pass: 3, warn: 0, fail: 1 },
      checks: [{ id: 'auth', title: 'Access control', status: 'fail', detail: 'Open to the network.', fix: 'Set AUTH_PASSWORD.' }] }) }))
    const wrapper = mountAbout()
    await wrapper.get('[data-testid="readiness"] button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="readiness"]').text()).toContain('Not ready')
    expect(wrapper.get('[data-testid="readiness"]').text()).toContain('Set AUTH_PASSWORD.')
  })

  it('disappears for people who are not allowed to see it', async () => {
    http.get.mockImplementation(() => Promise.reject({ status: 403, message: 'Forbidden' }))
    const wrapper = mountAbout()
    await wrapper.get('[data-testid="readiness"] button').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="readiness"]').exists()).toBe(false)
  })
})
