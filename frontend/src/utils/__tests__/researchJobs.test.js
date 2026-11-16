import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { JOB_STATE_LABELS, createJobPoller, finishNotice, isActive } from '../researchJobs.js'

const WORDS = { noun: 'Screening', past: 'screened', failed: 'could not be screened' }
const job = (over = {}) => ({ job_id: 'j1', status: 'done', total: 10, done: 10, failed: 0, errors: [], finished: true, ...over })

describe('finishNotice', () => {
  it('says nothing for a clean finish', () => {
    expect(finishNotice([job()], WORDS)).toBeNull()
  })

  it('reports a job with errors, with the count', () => {
    const n = finishNotice([job({ status: 'done_with_errors', done: 1, failed: 199, total: 200 })], WORDS)
    expect(n.type).toBe('warning')
    expect(n.message).toContain('finished with errors')
    expect(n.message).toContain('1 of 200 sources screened; 199 could not be screened')
  })

  it('reports a budget stop even when no paper failed', () => {
    const n = finishNotice([job({ status: 'failed', done: 3, failed: 0, current: 'Daily token budget reached' })], WORDS)
    expect(n.message).toBe('Screening stopped: Daily token budget reached. Screening: 3 of 10 sources screened; 7 not done.')
    expect(n.type).toBe('warning')
  })

  it('reports a cancel', () => {
    const n = finishNotice([job({ status: 'cancelled', done: 4 })], WORDS)
    expect(n.message).toContain('was cancelled')
    expect(n.message).toContain('4 of 10 sources screened; 6 not done')
  })

  it('reports a job where nothing worked as an error with the first reason', () => {
    const n = finishNotice([job({ status: 'failed', done: 0, failed: 10, errors: [{ error: 'model unreachable\nFor more information check: https://example.org' }] })], WORDS)
    expect(n.type).toBe('error')
    expect(n.message).toContain('First error: model unreachable')
    expect(n.message).not.toContain('example.org')
  })

  it('adds up the parts of a split job', () => {
    const n = finishNotice([job({ total: 200, done: 200 }), job({ job_id: 'j2', status: 'done_with_errors', total: 50, done: 48, failed: 2 })],
      { noun: 'Analysis', past: 'analyzed', failed: 'could not be read' })
    expect(n.message).toContain('248 of 250 sources analyzed; 2 could not be read')
  })

  it('says when the job is gone from the list', () => {
    expect(finishNotice([], WORDS, { missing: 1 }).message).toContain('no longer listed')
  })
})

describe('isActive', () => {
  it('trusts the server\'s finished flag, so a state added later never reads as running', () => {
    expect(isActive({ status: 'some_new_state', finished: true })).toBe(false)
    expect(isActive({ status: 'running', finished: false })).toBe(true)
    expect(isActive({ status: 'queued' })).toBe(true)
    expect(isActive({ status: 'done_with_errors' })).toBe(false)
    expect(isActive(undefined)).toBe(false)
  })

  it('has a label for every state the server reports', () => {
    for (const state of ['queued', 'running', 'done', 'done_with_errors', 'failed', 'cancelled']) {
      expect(JOB_STATE_LABELS[state]).toBeTruthy()
    }
  })
})

describe('createJobPoller', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })

  it('keeps two watches apart: starting the second never orphans the first', async () => {
    const server = { a: 'running', s: 'running' }
    const fetchJobs = vi.fn(async () => Object.entries(server).map(([id, status]) => job({ job_id: id, status, finished: !['queued', 'running'].includes(status) })))
    const poller = createJobPoller(fetchJobs, { interval: 100 })
    const finished = []
    poller.watch(['a'], { onFinished: (jobs) => finished.push(jobs[0].job_id) })
    poller.watch(['s'], { onFinished: (jobs) => finished.push(jobs[0].job_id) })
    expect(poller.active).toBe(2)

    server.a = 'done'
    await vi.advanceTimersByTimeAsync(150)
    expect(finished).toEqual(['a'])
    server.s = 'done_with_errors'
    await vi.advanceTimersByTimeAsync(150)
    expect(finished).toEqual(['a', 's'])
    expect(poller.active).toBe(0)
  })

  it('waits for every part of a split job, and counts parts that vanished', async () => {
    let jobs = [job({ job_id: 'p1' }), job({ job_id: 'p2', status: 'running', finished: false })]
    const poller = createJobPoller(async () => jobs, { interval: 100 })
    const onFinished = vi.fn()
    poller.watch(['p1', 'p2', 'p3'], { onFinished })
    await vi.advanceTimersByTimeAsync(150)
    expect(onFinished).not.toHaveBeenCalled()
    jobs = [job({ job_id: 'p1' }), job({ job_id: 'p2' })]
    await vi.advanceTimersByTimeAsync(150)
    expect(onFinished).toHaveBeenCalledWith([expect.objectContaining({ job_id: 'p1' }), expect.objectContaining({ job_id: 'p2' })], { missing: 1 })
  })

  it('keeps polling through a failed request, and stopAll ends every watch', async () => {
    const fetchJobs = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue([job({ status: 'running', finished: false })])
    const poller = createJobPoller(fetchJobs, { interval: 100 })
    poller.watch(['j1'], {})
    await vi.advanceTimersByTimeAsync(250)
    expect(fetchJobs).toHaveBeenCalledTimes(2)
    poller.stopAll()
    await vi.advanceTimersByTimeAsync(500)
    expect(fetchJobs).toHaveBeenCalledTimes(2)
  })
})
