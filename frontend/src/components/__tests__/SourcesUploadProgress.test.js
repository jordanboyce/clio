import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import SourcesSidebar from '../SourcesSidebar.vue'
import { useCollectionStore } from '../../stores/collectionStore'

// C6: dropped files are added at once, the transfer shows bytes sent, and
// Cancel stops it with nothing added.
const tenFiles = () => Array.from({ length: 10 }, (_, i) => new File(['x'.repeat(1000)], `note${i}.txt`))

let wrapper
beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
  const collections = useCollectionStore()
  collections.currentCollectionId = 'lab'
  collections.collections = [{ id: 'lab', name: 'Lab', permission: 'owner' }]
  http.get.mockImplementation(async (url) => {
    if (url === '/documents') return { data: { documents: [], total_documents: 0, kind_counts: {} } }
    if (url === '/documents/sync-folders') return { data: { folders: [] } }
    if (url === '/api/expertise/packs') return { data: [] }
    if (url.endsWith('/expertise')) return { data: { packs: [] } }
    return { data: {} }
  })
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.clearAllMocks() })

async function mountPanel() {
  wrapper = mount(SourcesSidebar, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function drop(target, files) {
  const dataTransfer = { files, types: ['Files'], dropEffect: '' }
  return target.trigger('drop', { dataTransfer })
}

describe('adding files by dropping them', () => {
  it('shows bytes sent while the transfer runs, then hands the files to indexing', async () => {
    let progress
    let finish
    http.post.mockImplementation((url, body, config) => {
      if (url === '/documents/upload-staged') {
        progress = config.onUploadProgress
        return new Promise((resolve) => { finish = () => resolve({ data: { staged: [{ path: '/staged/a' }], failed: [] } }) })
      }
      if (url === '/documents/index-local-async') return Promise.resolve({ data: { job_id: 7, total_files: 10 } })
      return Promise.resolve({ data: {} })
    })
    const w = await mountPanel()

    await drop(w.find('div'), tenFiles())
    await flushPromises()
    const bar = w.get('[data-testid="upload-progress"]')
    expect(bar.attributes('max')).toBe('10000')
    progress({ loaded: 4000 })
    await flushPromises()
    expect(bar.attributes('value')).toBe('4000')
    expect(w.text()).toContain('Uploading 10 files')

    finish()
    await flushPromises()
    expect(http.post.mock.calls.map(c => c[0])).toContain('/documents/index-local-async')
  })

  it('Cancel stops the transfer and adds nothing', async () => {
    let signal
    http.post.mockImplementation((url, body, config) => {
      if (url === '/documents/upload-staged') {
        signal = config.signal
        return new Promise((resolve, reject) => {
          signal.addEventListener('abort', () => reject(Object.assign(new Error('canceled'), { code: 'ERR_CANCELED' })))
        })
      }
      return Promise.resolve({ data: {} })
    })
    const w = await mountPanel()
    await drop(w.find('div'), tenFiles())
    await flushPromises()

    await w.get('[data-testid="upload-cancel"]').trigger('click')
    await flushPromises()

    expect(signal.aborted).toBe(true)
    expect(http.post.mock.calls.map(c => c[0])).not.toContain('/documents/index-local-async')
    expect(w.text()).toContain('Upload cancelled')
  })

  it('takes files dropped on Ask', async () => {
    http.post.mockImplementation(() => new Promise(() => {}))
    await mountPanel()
    const detail = { files: tenFiles(), handled: false }
    window.dispatchEvent(new CustomEvent('clio:files-dropped', { detail }))
    await flushPromises()
    expect(detail.handled).toBe(true)
    expect(http.post.mock.calls[0][0]).toBe('/documents/upload-staged')
  })
})
