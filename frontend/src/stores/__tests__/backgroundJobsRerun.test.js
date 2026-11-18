import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import http from '../../utils/http'
import { useBackgroundJobsStore } from '../backgroundJobsStore'

class FakeEventSource {
  constructor() { this.listeners = {} }
  addEventListener(name, handler) { this.listeners[name] = handler }
  close() {}
}

let jobs
beforeEach(() => {
  setActivePinia(createPinia())
  vi.stubGlobal('EventSource', FakeEventSource)
  http.get.mockReset()
  http.post.mockReset()
  http.get.mockResolvedValue({ data: {} })
  jobs = useBackgroundJobsStore()
})
afterEach(() => {
  jobs.cleanup()
  vi.unstubAllGlobals()
})

describe('Run again', () => {
  it('offers an interrupted job again and tracks the new one', async () => {
    jobs.addUploadJob({
      job_id: 7, collection_id: 'c1', status: 'failed', total_files: 3, processed_files: 1,
      job_type: 'index', can_rerun: true, error: 'Interrupted - the server restarted while this job was running.',
    })
    const shown = () => jobs.allJobs.find(j => j.id === 7)
    expect(shown().canRerun).toBe(true)

    http.post.mockResolvedValue({ data: { job_id: 8, rerun_of: 7, status: 'pending' } })
    await jobs.rerunUploadJob(7)

    expect(http.post).toHaveBeenCalledWith('/documents/upload/7/rerun')
    expect(shown().canRerun).toBe(false)
    const fresh = jobs.allJobs.find(j => j.id === 8)
    expect(fresh.collectionId).toBe('c1')
    expect(fresh.status).toBe('pending')
  })

  it('never offers a job the server did not say can run again', () => {
    jobs.addUploadJob({ job_id: 9, collection_id: 'c1', status: 'failed', total_files: 1, processed_files: 0 })
    expect(jobs.allJobs.find(j => j.id === 9).canRerun).toBe(false)
  })
})
