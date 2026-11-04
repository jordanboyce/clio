import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import SettingsTab from '../SettingsTab.vue'
import { useBackgroundJobsStore } from '../../stores/backgroundJobsStore'
import { useCollectionStore } from '../../stores/collectionStore'

// "Clear data" used to walk the collection one HTTP request per source from
// inside this component: thirty minutes behind a dialog that could not close,
// and a half-deleted collection if the tab went away.

const clearJob = (over = {}) => ({
  job_id: 9, collection_id: 'lab', status: 'running', total_files: 40, processed_files: 0, job_type: 'delete', ...over,
})

let wrapper
async function settingsOnDataSection() {
  wrapper = mount(SettingsTab, { attachTo: document.body })
  await flushPromises()
  const dialog = wrapper.get('dialog[aria-labelledby="clear-all-title"]').element
  dialog.showModal = vi.fn()
  dialog.close = vi.fn()
  return { w: wrapper, dialog }
}
const button = (w, text) => w.findAll('button').find(b => b.text().includes(text))

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
  vi.stubGlobal('matchMedia', () => ({ matches: false, addEventListener() {}, removeEventListener() {} }))
  const collections = useCollectionStore()
  collections.currentCollectionId = 'lab'
  collections.collections = [{ id: 'lab', name: 'Lab', permission: 'owner' }]
  http.get.mockResolvedValue({ data: {} })
  http.post.mockResolvedValue({ data: clearJob() })
})
afterEach(() => {
  useBackgroundJobsStore().cleanup()
  wrapper?.unmount()
  wrapper = null
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe('Delete generated reviews and reports', () => {
  const counts = (over = {}) => ({ reviews: 3, screenings: 2, reports: 4, reports_held: 0, ...over })

  async function dataSection(data = counts()) {
    http.get.mockImplementation(async (url) => (url.endsWith('/outputs') ? { data } : { data: {} }))
    const { w } = await settingsOnDataSection()
    const nav = w.findAll('button').find(b => b.text() === 'Data')
    await nav.trigger('click')
    await flushPromises()
    const dialog = w.get('dialog[aria-labelledby="delete-outputs-title"]').element
    dialog.showModal = vi.fn()
    dialog.close = vi.fn()
    return { w, dialog }
  }

  it('says how much there is, counting what no list shows', async () => {
    const { w } = await dataSection()

    expect(w.get('[data-testid="outputs-counts"]').text()).toBe('3 reviews · 2 screening verdicts · 4 saved reports')
    expect(w.get('[data-testid="delete-outputs"]').attributes('disabled')).toBeUndefined()
  })

  it('has nothing to offer when nothing was generated', async () => {
    const { w } = await dataSection(counts({ reviews: 0, screenings: 0, reports: 0 }))

    expect(w.get('[data-testid="outputs-counts"]').text()).toBe('Nothing generated yet.')
    expect(w.get('[data-testid="delete-outputs"]').attributes('disabled')).toBeDefined()
  })

  it('deletes what was ticked, and updates the counts', async () => {
    http.delete.mockResolvedValue({ data: { reviews: 3, screenings: 2, reports: 0, reports_held: 0 } })
    const { w, dialog } = await dataSection()

    await w.get('[data-testid="delete-outputs"]').trigger('click')
    expect(dialog.showModal).toHaveBeenCalled()
    await w.get('[data-testid="outputs-reports"]').setValue(false)

    http.get.mockImplementation(async (url) => (
      url.endsWith('/outputs') ? { data: counts({ reviews: 0, screenings: 0 }) } : { data: {} }
    ))
    await w.get('[data-testid="outputs-confirm"]').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith('/api/collections/lab/outputs', { params: { reviews: true } })
    expect(dialog.close).toHaveBeenCalled()
    expect(w.text()).toContain('Deleted 3 reviews, 2 screening verdicts')
    expect(w.get('[data-testid="outputs-counts"]').text()).toBe('0 reviews · 0 screening verdicts · 4 saved reports')
  })

  it('cannot be confirmed with nothing ticked', async () => {
    const { w } = await dataSection()

    await w.get('[data-testid="delete-outputs"]').trigger('click')
    await w.get('[data-testid="outputs-reviews"]').setValue(false)
    await w.get('[data-testid="outputs-reports"]').setValue(false)

    expect(w.get('[data-testid="outputs-confirm"]').attributes('disabled')).toBeDefined()
  })

  it('explains reports it cannot touch', async () => {
    const { w } = await dataSection(counts({ reports_held: 2 }))

    expect(w.get('[data-testid="outputs-row"]').text()).toContain('2 held for review stay')
  })

  it('shows a refusal rather than losing it', async () => {
    http.delete.mockRejectedValue({ message: 'A research job is running.' })
    const { w } = await dataSection()

    await w.get('[data-testid="delete-outputs"]').trigger('click')
    await w.get('[data-testid="outputs-confirm"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('A research job is running.')
  })

  it('is closed to read-only people', async () => {
    useCollectionStore().collections = [{ id: 'lab', name: 'Lab', permission: 'read' }]
    const { w } = await dataSection()

    expect(w.get('[data-testid="delete-outputs"]').attributes('disabled')).toBeDefined()
  })
})

describe('Clear collection data', () => {
  it('starts one job and closes the dialog, without deleting source by source', async () => {
    const { w, dialog } = await settingsOnDataSection()

    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'lab', everything: true })
    expect(http.delete).not.toHaveBeenCalled()
    expect(http.get).not.toHaveBeenCalledWith(expect.stringContaining('/documents?collection_id'))
    expect(dialog.close).toHaveBeenCalled()
  })

  it('shows real progress while it runs and keeps the button from starting another', async () => {
    const { w } = await settingsOnDataSection()
    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    const jobs = useBackgroundJobsStore()
    jobs.updateUploadJob(clearJob({
      processed_files: 10, progress_percent: 25, phase: 'deleting', phase_detail: 'Removed 10 of 40 stored files',
    }))
    await flushPromises()

    const progress = w.get('[data-testid="clear-progress"]')
    expect(progress.text()).toContain('Removed 10 of 40 stored files')
    expect(progress.get('progress').attributes('value')).toBe('25')
    expect(button(w, 'Clearing').attributes('disabled')).toBeDefined()
  })

  it('picks a job already running back up when the tab is opened again', async () => {
    useBackgroundJobsStore().addUploadJob({ ...clearJob(), deleting_everything: true })

    const { w } = await settingsOnDataSection()

    expect(w.find('[data-testid="clear-progress"]').exists()).toBe(true)
    expect(button(w, 'Clearing').attributes('disabled')).toBeDefined()
  })

  it('says it worked once the job has finished, and tells the app to refresh', async () => {
    const { w } = await settingsOnDataSection()
    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    useBackgroundJobsStore().updateUploadJob(clearJob({ status: 'completed', processed_files: 40 }))
    await flushPromises()

    expect(w.find('[data-testid="clear-progress"]').exists()).toBe(false)
    expect(w.text()).toContain('All data cleared')
    expect(w.emitted('data-cleared')).toHaveLength(1)
  })

  it('shows what went wrong when the job fails', async () => {
    const { w } = await settingsOnDataSection()
    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    useBackgroundJobsStore().updateUploadJob(clearJob({ status: 'failed', error: 'disk full' }))
    await flushPromises()

    expect(w.text()).toContain('disk full')
    expect(w.text()).not.toContain('All data cleared')
  })

  it('reports a refusal instead of leaving the dialog stuck', async () => {
    http.post.mockRejectedValue({ message: 'This collection is being re-indexed.' })
    const { w, dialog } = await settingsOnDataSection()

    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('This collection is being re-indexed.')
    expect(dialog.close).toHaveBeenCalled()
    expect(button(w, 'Clear data').attributes('disabled')).toBeUndefined()
  })

  it('treats an already empty collection as done', async () => {
    http.post.mockResolvedValue({ data: { job_id: null, status: 'completed' } })
    const { w } = await settingsOnDataSection()

    await button(w, 'Delete Everything').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('All data cleared')
    expect(useBackgroundJobsStore().uploadJobs).toHaveLength(0)
  })
})
