import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import http from '../../utils/http'
import { useBackgroundJobsStore } from '../backgroundJobsStore'
import { useUiStore } from '../uiStore'

// A stand-in for the browser's EventSource that records how many are open,
// which is the number that decides whether the rest of the app can load.
class FakeEventSource {
  static open = []

  constructor(url) {
    this.url = url
    this.listeners = {}
    FakeEventSource.open.push(this)
  }

  addEventListener(name, handler) { this.listeners[name] = handler }

  emit(name, data) { this.listeners[name]?.({ data: JSON.stringify({ event_type: name, ...data }) }) }

  close() { FakeEventSource.open = FakeEventSource.open.filter(s => s !== this) }
}

const job = (id, over = {}) => ({
  job_id: id, collection_id: 'c1', status: 'running', total_files: 10, processed_files: 0,
  job_type: 'index', ...over,
})

let jobs
beforeEach(() => {
  setActivePinia(createPinia())
  FakeEventSource.open = []
  vi.stubGlobal('EventSource', FakeEventSource)
  http.get.mockReset()
  http.post.mockReset()
  jobs = useBackgroundJobsStore()
})
afterEach(() => {
  jobs.cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('following jobs without using up the browser', () => {
  it('opens at most two streams, however many jobs are running', () => {
    for (let id = 1; id <= 8; id += 1) jobs.addUploadJob(job(id))

    expect(FakeEventSource.open).toHaveLength(2)
    expect(jobs.activeJobCount).toBe(8)
  })

  it('polls the jobs that did not get a stream', async () => {
    http.get.mockImplementation(async (url) => ({ data: job(Number(url.split('/')[3]), { processed_files: 4 }) }))
    for (let id = 1; id <= 3; id += 1) jobs.addUploadJob(job(id))

    await jobs.pollAllJobs()

    expect(http.get.mock.calls.map(c => c[0])).toEqual(['/documents/upload/3/status'])
    expect(jobs.uploadJobs.find(j => j.jobId === 3).processedFiles).toBe(4)
  })

  it('still checks on a streamed job now and then, in case a proxy swallows its events', async () => {
    http.get.mockResolvedValue({ data: job(1, { status: 'completed', processed_files: 10 }) })
    jobs.addUploadJob(job(1))

    for (let tick = 0; tick < 5; tick += 1) await jobs.pollAllJobs()

    expect(http.get).toHaveBeenCalledWith('/documents/upload/1/status')
    expect(jobs.uploadJobs[0].status).toBe('completed')
  })

  it('does not start a new round of polling on top of one still waiting', async () => {
    let answer
    http.get.mockImplementation(() => new Promise((resolve) => { answer = resolve }))
    jobs.addUploadJob(job(1))
    jobs.addUploadJob(job(2))
    jobs.addUploadJob(job(3))

    const first = jobs.pollAllJobs()
    await jobs.pollAllJobs()
    expect(http.get).toHaveBeenCalledTimes(1)

    answer({ data: job(3) })
    await first
  })

  it('signals the source list at most every couple of seconds, however fast the job', () => {
    vi.useFakeTimers()
    jobs.addUploadJob(job(1, { job_type: 'delete' }))
    const before = jobs.dataRefreshTick
    const stream = FakeEventSource.open[0]

    // Three hundred sources a tick, four ticks a second.
    for (let i = 1; i <= 8; i += 1) {
      vi.advanceTimersByTime(250)
      stream.emit('file_complete', { file_index: i * 300, total_files: 2400 })
    }

    expect(jobs.dataRefreshTick - before).toBe(0)
    vi.advanceTimersByTime(250)
    stream.emit('file_complete', { file_index: 2700, total_files: 3000 })
    expect(jobs.dataRefreshTick - before).toBe(1)
  })
})

describe('deleting sources', () => {
  it('asks for named sources and follows the job', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete', status: 'pending', total_files: 2 }) })

    const record = await jobs.deleteSources({ collectionId: 'c1', documentIds: ['a', 'b'] })

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'c1', document_ids: ['a', 'b'] })
    expect(record.jobId).toBe(7)
    expect(FakeEventSource.open).toHaveLength(1)
    const pending = jobs.pendingDeletion('c1')
    expect(pending.everything).toBe(false)
    expect([...pending.ids]).toEqual(['a', 'b'])
  })

  it('asks for everything when given no ids', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete' }) })

    await jobs.deleteSources({ collectionId: 'c1' })

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'c1', everything: true })
    expect(jobs.pendingDeletion('c1').everything).toBe(true)
  })

  it('returns nothing when the server had nothing to delete', async () => {
    http.post.mockResolvedValue({ data: { job_id: null, status: 'completed' } })

    expect(await jobs.deleteSources({ collectionId: 'c1' })).toBeNull()
    expect(jobs.uploadJobs).toHaveLength(0)
  })

  it('stops marking sources once the job has ended', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete' }) })
    http.get.mockResolvedValue({ data: job(7, { job_type: 'delete', status: 'cancelled', error: 'Stopped' }) })
    await jobs.deleteSources({ collectionId: 'c1', documentIds: ['a'] })

    FakeEventSource.open[0].emit('job_cancelled', { error: 'Stopped' })
    await vi.waitFor(() => expect(jobs.pendingDeletion('c1').ids.size).toBe(0))
  })

  it('keeps another collection out of it', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete' }) })

    await jobs.deleteSources({ collectionId: 'c1', documentIds: ['a'] })

    expect(jobs.pendingDeletion('c2').ids.size).toBe(0)
  })

  it('reports a small delete that was over before the response', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete', status: 'completed', total_files: 1 }) })
    http.get.mockResolvedValue({
      data: job(7, { job_type: 'delete', status: 'completed', result_summary: { documents_processed: 1, filename: 'plan.pdf' } }),
    })

    await jobs.deleteSources({ collectionId: 'c1', documentIds: ['a'] })

    await vi.waitFor(() => expect(useUiStore().toasts.map(t => t.message)).toEqual(['Deleted plan.pdf']))
  })

  it.each([
    ['completed', { documents_processed: 2000, failed_files: [] }, null, 'Deleted 2,000 sources', 'success'],
    ['completed', { documents_processed: 3, failed_files: [{ filename: 'a', error: 'locked' }] }, null,
      'Deleted 3 sources, but 1 stored file could not be removed — open Background jobs for the list.', 'warning'],
    ['cancelled', {}, 'Stopped after deleting 2 of 5 sources. The rest were kept.',
      'Stopped after deleting 2 of 5 sources. The rest were kept.', 'warning'],
    ['failed', {}, 'disk full', 'Deleting sources failed: disk full', 'error'],
  ])('says how a %s job ended', async (status, summary, error, message, type) => {
    jobs.addUploadJob(job(7, { job_type: 'delete' }))
    http.get.mockResolvedValue({ data: job(7, { job_type: 'delete', status, error, result_summary: summary }) })

    FakeEventSource.open[0].emit(status === 'completed' ? 'job_complete' : status === 'failed' ? 'job_error' : 'job_cancelled', {})

    await vi.waitFor(() => expect(useUiStore().toasts).toHaveLength(1))
    expect(useUiStore().toasts[0]).toMatchObject({ message, type })
  })
})

describe('what the drawer shows', () => {
  const inDrawer = (id) => jobs.allJobs.find(j => j.id === id)

  it('offers no Cancel once a collection is being emptied, since it cannot stop half way', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete', queue_position: 1, status: 'pending' }) })
    await jobs.deleteSources({ collectionId: 'c1' })
    expect(inDrawer(7).cancellable).toBe(true) // still queued: nothing has happened yet

    FakeEventSource.open[0].emit('file_complete', { file_index: 0, total_files: 5 })
    expect(inDrawer(7).cancellable).toBe(false)
  })

  it('keeps Cancel for a delete of named sources, which stops at the next batch', async () => {
    http.post.mockResolvedValue({ data: job(7, { job_type: 'delete' }) })
    await jobs.deleteSources({ collectionId: 'c1', documentIds: ['a'] })

    expect(inDrawer(7).cancellable).toBe(true)
  })

  it('estimates the time left from the rate since this tab first saw progress', () => {
    vi.useFakeTimers()
    jobs.addUploadJob(job(1))
    const stream = FakeEventSource.open[0]

    stream.emit('file_complete', { file_index: 1, overall_percent: 10 })
    expect(inDrawer(1).remainingSeconds).toBeNull() // one reading is not a rate

    vi.advanceTimersByTime(10000)
    stream.emit('file_complete', { file_index: 2, overall_percent: 20 })
    expect(inDrawer(1).remainingSeconds).toBe(80)
  })

  it('gives no estimate before there is enough to go on', () => {
    vi.useFakeTimers()
    jobs.addUploadJob(job(1))
    const stream = FakeEventSource.open[0]

    stream.emit('file_complete', { file_index: 1, overall_percent: 10 })
    vi.advanceTimersByTime(1000)
    stream.emit('file_complete', { file_index: 2, overall_percent: 50 })

    expect(inDrawer(1).remainingSeconds).toBeNull()
  })

  it('counts a re-index by how far into the current file it is', () => {
    jobs.setReindexJob({
      id: 3, status: 'running', collection_id: 'c1', total_documents: 2, processed_documents: 1,
      current_file: 'big.pdf', phase: 'embedding', phase_progress: 50, phase_detail: 'Embedded 50/100 chunks',
      chunks_processed: 50, chunks_total: 100,
    })

    expect(inDrawer(3)).toMatchObject({
      type: 'reindex', progress: 75, phase: 'embedding', phaseDetail: 'Embedded 50/100 chunks',
      chunksProcessed: 50, chunksTotal: 100,
    })
  })

  it('says how a re-index ended, once', async () => {
    jobs.setReindexJob({ id: 3, status: 'running', total_documents: 2, processed_documents: 1 })
    http.get.mockResolvedValue({
      data: { id: 3, status: 'failed', total_documents: 2, processed_documents: 1, error: '1 of 2 file(s) could not be re-indexed' },
    })

    await jobs.pollAllJobs()
    await jobs.pollAllJobs()

    expect(useUiStore().toasts.map(t => t.message)).toEqual(['Re-index failed: 1 of 2 file(s) could not be re-indexed'])
  })
})
