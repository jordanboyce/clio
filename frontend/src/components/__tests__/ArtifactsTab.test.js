import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }))
vi.mock('../../utils/http', () => ({ default: http }))

const providers = vi.hoisted(() => ({ active: 'anthropic' }))
vi.mock('../../stores/providerStore', () => ({
  useProviderStore: () => ({
    get activeProviderId() { return providers.active },
    loadServerProviders: () => Promise.resolve(),
  }),
}))

import ArtifactsTab from '../ArtifactsTab.vue'
import { useCollectionStore } from '../../stores/collectionStore'
import { useUserStore } from '../../stores/userStore'

const summary = (over = {}) => ({
  id: 'a1', artifact_type: 'faq', title: 'FAQ', status: 'done', error: null,
  created_at: '2026-11-03T10:00:00+00:00', created_by: 'default',
  has_file: false, held: false, source_changes: [], ...over,
})
const detail = (over = {}) => ({ ...summary(over), content: '# Saved report', sources: [], ...over })

let saved
let details

function serve() {
  http.get.mockImplementation((url) => {
    if (url === '/api/artifacts/types') {
      return Promise.resolve({ data: { types: [{ type: 'faq', label: 'FAQ', description: 'Questions' }] } })
    }
    if (url === '/api/artifacts') return Promise.resolve({ data: { artifacts: saved, truncated: false } })
    const id = url.split('/').pop()
    return details[id] ? Promise.resolve({ data: details[id] }) : Promise.reject({ status: 404, message: 'Artifact not found' })
  })
}

async function mountTab(setup) {
  const collections = useCollectionStore()
  collections.collections = [{ id: 'c1', name: 'One', permission: 'owner' }, { id: 'c2', name: 'Two', permission: 'owner' }]
  collections.currentCollectionId = 'c1'
  setup?.(collections)
  const wrapper = mount(ArtifactsTab)
  await flushPromises()
  return wrapper
}

const listCalls = () => http.get.mock.calls.filter(([url]) => url === '/api/artifacts')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  vi.useFakeTimers({ shouldAdvanceTime: false })
  providers.active = 'anthropic'
  saved = []
  details = {}
  serve()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('Saved reports', () => {
  it('shows what was generated on an earlier visit, and opens it from the server', async () => {
    saved = [summary()]
    details = { a1: detail() }
    const wrapper = await mountTab()

    expect(wrapper.findAll('[data-testid="artifact-row"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="artifact-viewer"]').exists()).toBe(false)

    await wrapper.find('[data-testid="artifact-row"] button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="artifact-viewer"]').text()).toContain('Saved report')
    expect(http.get).toHaveBeenCalledWith('/api/artifacts/a1', { params: { collection_id: 'c1' } })
  })

  it('says so when nothing has been generated', async () => {
    const wrapper = await mountTab()
    expect(wrapper.get('[data-testid="artifacts-empty"]').text()).toContain('even after you leave this page')
  })

  it('generates in the background, keeps polling, and opens the result', async () => {
    const wrapper = await mountTab()
    const queued = summary({ id: 'n1', status: 'queued' })
    http.post.mockResolvedValue({ data: queued })

    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()
    const [, body, config] = http.post.mock.calls[0]
    expect(body.artifact_type).toBe('faq')
    expect(body).not.toHaveProperty('scope')
    expect(config.params).toEqual({ collection_id: 'c1' })
    expect(wrapper.get('[data-testid="artifact-row"]').text()).toContain('Waiting')

    saved = [summary({ id: 'n1', status: 'running' })]
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.get('[data-testid="artifact-row"]').text()).toContain('Generating')
    expect(wrapper.find('[data-testid="artifact-viewer"]').exists()).toBe(false)

    saved = [summary({ id: 'n1' })]
    details = { n1: detail({ id: 'n1', content: '# Fresh report' }) }
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(wrapper.get('[data-testid="artifact-viewer"]').text()).toContain('Fresh report')

    const after = listCalls().length
    await vi.advanceTimersByTimeAsync(10000)
    expect(listCalls()).toHaveLength(after)
  })

  it('does not take over the page if something else was opened meanwhile', async () => {
    saved = [summary()]
    details = { a1: detail(), n1: detail({ id: 'n1', content: '# Fresh report' }) }
    const wrapper = await mountTab()
    http.post.mockResolvedValue({ data: summary({ id: 'n1', status: 'running' }) })
    await wrapper.get('button.btn-primary').trigger('click')
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="artifact-row"]')
    await rows[1].find('button').trigger('click')
    await flushPromises()

    saved = [summary({ id: 'n1' }), summary()]
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(wrapper.get('[data-testid="artifact-viewer"]').text()).toContain('Saved report')
  })

  it('shows why a report failed, and it cannot be opened', async () => {
    saved = [summary({ status: 'failed', error: 'The provider refused the key' })]
    const wrapper = await mountTab()
    const row = wrapper.get('[data-testid="artifact-row"]')
    expect(row.text()).toContain('The provider refused the key')
    expect(row.text()).toContain('Failed')
    expect(row.find('button').attributes('disabled')).toBeDefined()
  })

  it('flags reports whose sources have since changed', async () => {
    const changes = [{ document_id: 'd1', filename: 'plan.pdf', change: 'changed' }]
    saved = [summary({ source_changes: changes })]
    details = { a1: detail({ source_changes: changes }) }
    const wrapper = await mountTab()
    expect(wrapper.get('[data-testid="artifact-row"]').text()).toContain('Sources changed')

    await wrapper.find('[data-testid="artifact-row"] button').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="artifact-changes"]').text()).toContain('plan.pdf (changed)')
  })

  it('asks before deleting, then removes the report', async () => {
    saved = [summary()]
    http.delete.mockResolvedValue({ data: { deleted: true } })
    const wrapper = await mountTab()

    await wrapper.get('button[aria-label="Delete FAQ"]').trigger('click')
    expect(http.delete).not.toHaveBeenCalled()

    const confirm = wrapper.findAll('button').find(b => b.text() === 'Delete permanently')
    await confirm.trigger('click')
    await flushPromises()
    expect(http.delete).toHaveBeenCalledWith('/api/artifacts/a1', { params: { collection_id: 'c1' } })
    expect(wrapper.findAll('[data-testid="artifact-row"]')).toHaveLength(0)
  })

  it('deletes every saved report at once, asking first', async () => {
    saved = [summary(), summary({ id: 'a2', title: 'Timeline' })]
    serve()
    http.delete.mockImplementation(async () => {
      saved = []
      return { data: { reviews: 0, screenings: 0, reports: 2, reports_held: 0 } }
    })
    const wrapper = await mountTab()

    await wrapper.get('[data-testid="delete-all-reports"]').trigger('click')
    expect(http.delete).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="delete-all-reports-confirm"]').text()).toContain('Delete all 2 permanently')

    await wrapper.get('[data-testid="delete-all-reports-confirm"]').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith('/api/collections/c1/outputs', { params: { reports: true } })
    expect(wrapper.findAll('[data-testid="artifact-row"]')).toHaveLength(0)
  })

  it('still lists a report the server kept back from a delete-all', async () => {
    saved = [summary(), summary({ id: 'a2', title: 'Held one' })]
    serve()
    http.delete.mockImplementation(async () => {
      saved = [summary({ id: 'a2', title: 'Held one' })]
      return { data: { reviews: 0, screenings: 0, reports: 1, reports_held: 1 } }
    })
    const wrapper = await mountTab()

    await wrapper.get('[data-testid="delete-all-reports"]').trigger('click')
    await wrapper.get('[data-testid="delete-all-reports-confirm"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-testid="artifact-row"]').map(r => r.text())).toEqual([expect.stringContaining('Held one')])
  })

  it('offers no delete-all to someone who cannot edit', async () => {
    saved = [summary(), summary({ id: 'a2' })]
    serve()
    const wrapper = await mountTab((collections) => {
      collections.collections = [{ id: 'c1', name: 'One', permission: 'read' }]
    })

    expect(wrapper.find('[data-testid="delete-all-reports"]').exists()).toBe(false)
  })

  it('can back out of deleting', async () => {
    saved = [summary()]
    const wrapper = await mountTab()
    await wrapper.get('button[aria-label="Delete FAQ"]').trigger('click')
    await wrapper.findAll('button').find(b => b.text() === 'Keep').trigger('click')
    expect(http.delete).not.toHaveBeenCalled()
    expect(wrapper.find('button[aria-label="Delete FAQ"]').exists()).toBe(true)
  })

  it('gives a reader the library and nothing else', async () => {
    saved = [summary()]
    const wrapper = await mountTab((collections) => {
      collections.collections = [{ id: 'c1', name: 'One', permission: 'read', shared: true }]
    })
    expect(wrapper.findAll('[data-testid="artifact-row"]')).toHaveLength(1)
    expect(wrapper.find('button.btn-primary').exists()).toBe(false)
    expect(wrapper.find('button[aria-label="Delete FAQ"]').exists()).toBe(false)
  })

  it('names who made a report only where people have identities', async () => {
    saved = [summary({ created_by: 'ana@example.test' })]
    let wrapper = await mountTab()
    expect(wrapper.get('[data-testid="artifact-row"]').text()).not.toContain('ana@example.test')
    wrapper.unmount()

    wrapper = await mountTab(() => { useUserStore().privateCollections = true })
    expect(wrapper.get('[data-testid="artifact-row"]').text()).toContain('ana@example.test')
  })

  it('switching collection shows that collection and drops the late reply', async () => {
    saved = [summary()]
    details = { a1: detail() }
    const wrapper = await mountTab()
    await wrapper.find('[data-testid="artifact-row"] button').trigger('click')
    await flushPromises()

    let answerSlowly
    http.get.mockImplementationOnce(() => new Promise((resolve) => { answerSlowly = resolve }))
    useCollectionStore().currentCollectionId = 'c2'
    await flushPromises()
    expect(wrapper.find('[data-testid="artifact-viewer"]').exists()).toBe(false)

    saved = []
    useCollectionStore().currentCollectionId = 'c1'
    await flushPromises()
    answerSlowly({ data: { artifacts: [summary({ id: 'late', title: 'Too late' })], truncated: false } })
    await flushPromises()
    expect(wrapper.text()).not.toContain('Too late')
  })

  it('stops polling when it leaves the page', async () => {
    saved = [summary({ status: 'running' })]
    const wrapper = await mountTab()
    wrapper.unmount()
    const before = listCalls().length
    await vi.advanceTimersByTimeAsync(10000)
    expect(listCalls()).toHaveLength(before)
  })

  it('keeps polling through a failed listing and clears the error after', async () => {
    saved = [summary({ status: 'running' })]
    const wrapper = await mountTab()
    http.get.mockImplementationOnce(() => Promise.reject({ status: 500, message: 'Listing failed' }))
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.text()).toContain('Listing failed')

    saved = [summary()]
    await vi.advanceTimersByTimeAsync(2500)
    expect(wrapper.text()).not.toContain('Listing failed')
  })
})
