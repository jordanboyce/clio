import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))

import http from '../../utils/http'
import ApiAppsPanel from '../ApiAppsPanel.vue'

const app = (over = {}) => ({
  id: 'a1', name: 'invoice-portal', status: 'active', collections: ['c1'], can_write: false,
  key_prefix: 'clio_app_abc123', oidc_subject: null, last_used_at: null, expires_at: null,
  previous_key_expires_at: null, ...over,
})

function wire(apps = []) {
  http.get.mockImplementation((url) => {
    if (url === '/api/admin/apps') return Promise.resolve({ data: { apps } })
    if (url === '/api/collections') return Promise.resolve({ data: { collections: [{ id: 'c1', name: 'Finance' }, { id: 'c2', name: 'Legal' }] } })
    return Promise.resolve({ data: {} })
  })
}

async function mounted(apps) {
  wire(apps)
  const wrapper = mount(ApiAppsPanel)
  await flushPromises()
  return wrapper
}

const byId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`)

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('ApiAppsPanel', () => {
  it('lists an app with its collection names, not ids', async () => {
    const row = byId(await mounted([app()]), 'apps-row')
    expect(row.text()).toContain('invoice-portal')
    expect(row.text()).toContain('Finance')
    expect(row.text()).toContain('Read only')
    expect(row.text()).toContain('Never')
  })

  it('offers no actions on a revoked app', async () => {
    const wrapper = await mounted([app({ status: 'revoked' })])
    expect(byId(wrapper, 'apps-row').text()).toContain('revoked')
    expect(byId(wrapper, 'apps-revoke').exists()).toBe(false)
    expect(byId(wrapper, 'apps-rotate').exists()).toBe(false)
  })

  it('cannot register an app with no collection', async () => {
    const wrapper = await mounted([])
    await byId(wrapper, 'apps-new').trigger('click')
    await wrapper.find('input[placeholder="invoice-portal"]').setValue('portal')
    expect(byId(wrapper, 'apps-submit').element.disabled).toBe(true)
    await wrapper.find('input[type="checkbox"][value="c1"]').setValue(true)
    expect(byId(wrapper, 'apps-submit').element.disabled).toBe(false)
  })

  it('registers with only what was filled in, and shows the key until dismissed', async () => {
    const wrapper = await mounted([])
    http.post.mockResolvedValue({ data: { name: 'portal', key: 'clio_app_secret' } })
    await byId(wrapper, 'apps-new').trigger('click')
    await wrapper.find('input[placeholder="invoice-portal"]').setValue('portal')
    await wrapper.find('input[type="checkbox"][value="c1"]').setValue(true)
    await byId(wrapper, 'apps-form').trigger('submit')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/api/admin/apps', {
      name: 'portal', collections: ['c1'], can_write: false, issue_key: true,
    })
    expect(byId(wrapper, 'apps-key').text()).toBe('clio_app_secret')
    await byId(wrapper, 'apps-dismiss').trigger('click')
    expect(byId(wrapper, 'apps-issued').exists()).toBe(false)
  })

  it('sends a budget of 0, which means unlimited, not unset', async () => {
    const wrapper = await mounted([])
    http.post.mockResolvedValue({ data: { name: 'portal', key: 'k' } })
    await byId(wrapper, 'apps-new').trigger('click')
    await wrapper.find('input[placeholder="invoice-portal"]').setValue('portal')
    await wrapper.find('input[type="checkbox"][value="c1"]').setValue(true)
    await wrapper.findAll('input[placeholder="Deployment default"]')[0].setValue(0)
    await byId(wrapper, 'apps-form').trigger('submit')
    await flushPromises()
    expect(http.post.mock.calls[0][1].search_per_minute).toBe(0)
  })

  it('keeps the form open and says why when registration fails', async () => {
    const wrapper = await mounted([])
    http.post.mockRejectedValue({ response: { data: { detail: "An app named 'portal' already exists." } } })
    await byId(wrapper, 'apps-new').trigger('click')
    await wrapper.find('input[placeholder="invoice-portal"]').setValue('portal')
    await wrapper.find('input[type="checkbox"][value="c1"]').setValue(true)
    await byId(wrapper, 'apps-form').trigger('submit')
    await flushPromises()
    expect(byId(wrapper, 'apps-form').text()).toContain('already exists')
    expect(byId(wrapper, 'apps-issued').exists()).toBe(false)
  })

  it('asks before revoking, and does nothing until confirmed', async () => {
    const wrapper = await mounted([app()])
    http.delete.mockResolvedValue({ data: {} })
    await byId(wrapper, 'apps-revoke').trigger('click')
    expect(http.delete).not.toHaveBeenCalled()
    await byId(wrapper, 'apps-confirm').trigger('click')
    await flushPromises()
    expect(http.delete).toHaveBeenCalledWith('/api/admin/apps/a1')
  })

  it('rotating shows the new key', async () => {
    const wrapper = await mounted([app()])
    http.post.mockResolvedValue({ data: { name: 'invoice-portal', key: 'clio_app_new' } })
    await byId(wrapper, 'apps-rotate').trigger('click')
    await byId(wrapper, 'apps-confirm').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/admin/apps/a1/rotate-key', { grace_hours: 24 })
    expect(byId(wrapper, 'apps-key').text()).toBe('clio_app_new')
  })
})
