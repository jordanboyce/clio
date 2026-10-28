import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

import http from '../../utils/http'
import { useUserStore } from '../../stores/userStore'
import { useUiStore } from '../../stores/uiStore'
import ExpertiseLibrary from '../ExpertiseLibrary.vue'

const pack = (over = {}) => ({
  id: 'p1', name: 'House style', description: 'How we write', body: 'Define acronyms.',
  created_at: '2026-01-01T00:00:00', updated_at: '2026-01-02T00:00:00', flag: null, ...over,
})

const mountLibrary = async ({ admin, packs = [pack()] }) => {
  setActivePinia(createPinia())
  useUserStore().adminConsole = admin
  http.get.mockResolvedValue({ data: packs })
  const wrapper = mount(ExpertiseLibrary)
  await flushPromises()
  return wrapper
}

beforeEach(() => vi.clearAllMocks())

describe('Saved instructions are admin-written', () => {
  it('hides every edit control from someone who is not an administrator', async () => {
    const wrapper = await mountLibrary({ admin: false })
    expect(wrapper.get('[data-testid="expertise-readonly"]').text()).toContain('Only an administrator')
    expect(wrapper.text()).not.toContain('New instructions')
    expect(wrapper.find('button[title="Edit"]').exists()).toBe(false)
    expect(wrapper.find('button[title="Delete"]').exists()).toBe(false)
    // The pack is still listed, and clicking it does not open an editor.
    expect(wrapper.text()).toContain('House style')
    await wrapper.get('.card.bg-base-200').trigger('click')
    expect(wrapper.text()).not.toContain('Edit instructions')
  })

  it('shows the controls to an administrator, and marks a flagged pack', async () => {
    const wrapper = await mountLibrary({
      admin: true, packs: [pack({ flag: { level: 'medium', reason: 'it tries to override earlier instructions' } })],
    })
    expect(wrapper.find('[data-testid="expertise-readonly"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('New instructions')
    expect(wrapper.find('button[title="Edit"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="expertise-flagged"]').text()).toBe('flagged')
  })

  it('says plainly when the server refuses a save with 403', async () => {
    const wrapper = await mountLibrary({ admin: true })
    http.put.mockRejectedValue({ message: 'Request failed with status code 403', response: { status: 403, data: { detail: 'Only an administrator can manage saved instructions.' } } })
    await wrapper.get('button[title="Edit"]').trigger('click')
    const save = wrapper.findAll('button').find(b => b.text().includes('Save Changes'))
    await save.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Only an administrator can manage saved instructions.')
    expect(wrapper.text()).not.toContain('status code 403')
  })

  it('shows the scan warning after a flagged pack is saved', async () => {
    const wrapper = await mountLibrary({ admin: true })
    http.put.mockResolvedValue({ data: pack({ flag: { level: 'high' }, warning: { message: 'It was saved, but agents are told it is flagged.' } }) })
    await wrapper.get('button[title="Edit"]').trigger('click')
    await wrapper.findAll('button').find(b => b.text().includes('Save Changes')).trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="expertise-flag-warning"]').text()).toContain('agents are told it is flagged')
  })

  it('reports a refused delete instead of failing silently', async () => {
    const wrapper = await mountLibrary({ admin: true })
    HTMLDialogElement.prototype.showModal = HTMLDialogElement.prototype.showModal || function () {}
    HTMLDialogElement.prototype.close = HTMLDialogElement.prototype.close || function () {}
    http.delete.mockRejectedValue({ response: { status: 403, data: { detail: 'Only an administrator can manage saved instructions.' } } })
    await wrapper.get('button[title="Delete"]').trigger('click')
    await wrapper.findAll('button').find(b => b.text() === 'Delete').trigger('click')
    await flushPromises()
    const toasts = useUiStore().toasts
    expect(toasts.at(-1).message).toContain('Only an administrator')
    expect(toasts.at(-1).type).toBe('error')
  })
})
