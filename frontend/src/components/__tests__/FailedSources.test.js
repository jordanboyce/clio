import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(() => Promise.resolve({ data: { jobs: [] } })), post: vi.fn() } }))
import http from '../../utils/http'
import FailedSources from '../FailedSources.vue'
import { useBackgroundJobsStore } from '../../stores/backgroundJobsStore'

const scan = { filename: 'scan.pdf', error: 'image-only', reason: 'It is a scan with no text to read.', fix: 'Turn on OCR.', retry: 'tmpa/scan.pdf' }

describe('files that could not be added stay listed (U2, D1)', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    http.post.mockReset()
  })

  it('lists the reason and the fix, survives a reload, and retries from the kept copy', async () => {
    const store = useBackgroundJobsStore()
    store.failedSources.push({ key: '4:scan.pdf', collectionId: 'default', ...scan })
    const w = mount(FailedSources, { props: { collectionId: 'default', canRetry: true } })
    expect(w.text()).toContain('1 could not be added')
    expect(w.text()).toContain('It is a scan with no text to read.')
    expect(w.text()).toContain('Turn on OCR.')

    http.post.mockResolvedValueOnce({ data: { job_id: 9, collection_id: 'default', status: 'pending', total_files: 1 } })
    await w.findAll('button').find(b => b.text() === 'Retry').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/documents/retry-failed', { collection_id: 'default', items: ['tmpa/scan.pdf'] })
    expect(w.find('[data-testid="failed-sources"]').exists()).toBe(false)
  })

  it('dismisses one file and keeps the list across a reload', async () => {
    const store = useBackgroundJobsStore()
    store.failedSources.push({ key: '4:scan.pdf', collectionId: 'default', ...scan }, { key: '4:b.docx', collectionId: 'default', filename: 'b.docx', reason: 'Damaged.', fix: '', retry: null })
    store.dismissFailed('4:scan.pdf')
    setActivePinia(createPinia())
    const reloaded = useBackgroundJobsStore()
    expect(reloaded.failedFor('default').map(f => f.filename)).toEqual(['b.docx'])
  })
})
