import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import ResearchReviews from '../ResearchReviews.vue'
import { useUiStore } from '../../stores/uiStore'

const review = (id, over = {}) => ({
  id, document_id: `d${id}`, filename: `paper${id}.pdf`, title: `Paper ${id}`, authors: [], depth: 'brief',
  created_at: '2026-11-03T10:00:00+00:00', read_at: null, sections: [], ...over,
})

let wrapper
let serverJobs = []
async function mountReviews(briefs, props = {}) {
  http.get.mockImplementation(async (url) => {
    if (url.startsWith('/api/research/briefs')) return { data: { briefs, unread: briefs.filter(b => !b.read_at).length } }
    return { data: { jobs: serverJobs } }
  })
  wrapper = mount(ResearchReviews, {
    props: { collectionId: 'lab', papers: [], selectedIds: [], providerId: null, canWrite: true, ...props },
    global: { stubs: { ResearchBrief: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  http.get.mockReset()
  http.post.mockReset()
  http.delete.mockReset()
  serverJobs = []
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('deleting reviews', () => {
  it('can delete a review without opening it, and asks first', async () => {
    http.delete.mockResolvedValue({ data: { deleted: 1 } })
    const w = await mountReviews([review(1), review(2)])

    await w.get('[aria-label="Delete the review of Paper 1"]').trigger('click')
    expect(http.delete).not.toHaveBeenCalled()

    await w.findAll('button').find(b => b.text() === 'Delete permanently').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith(expect.stringContaining('/api/research/briefs/1?'))
    expect(w.findAll('[data-testid="review-row"]')).toHaveLength(1)
  })

  it('can back out of deleting one', async () => {
    const w = await mountReviews([review(1)])

    await w.get('[aria-label="Delete the review of Paper 1"]').trigger('click')
    await w.findAll('button').find(b => b.text() === 'Keep').trigger('click')

    expect(http.delete).not.toHaveBeenCalled()
    expect(w.find('[aria-label="Delete the review of Paper 1"]').exists()).toBe(true)
  })

  it('keeps the unread count right when an unread review goes', async () => {
    http.delete.mockResolvedValue({ data: { deleted: 1 } })
    const w = await mountReviews([review(1), review(2)])
    expect(w.text()).toContain('2 unread')

    await w.get('[aria-label="Delete the review of Paper 1"]').trigger('click')
    await w.findAll('button').find(b => b.text() === 'Delete permanently').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('1 unread')
  })

  it('deletes them all at once, says that verdicts go too, and tells the page', async () => {
    http.delete.mockResolvedValue({ data: { reviews: 2, screenings: 3, reports: 0, reports_held: 0 } })
    const w = await mountReviews([review(1), review(2)])

    await w.get('[data-testid="delete-all-reviews"]').trigger('click')
    expect(w.text()).toContain('Also removes screening verdicts.')
    expect(http.delete).not.toHaveBeenCalled()

    await w.get('[data-testid="delete-all-reviews-confirm"]').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith('/api/collections/lab/outputs', { params: { reviews: true } })
    expect(w.findAll('[data-testid="review-row"]')).toHaveLength(0)
    expect(w.emitted('changed')).toHaveLength(1)
    expect(useUiStore().toasts.map(t => t.message)).toEqual(['Deleted 2 reviews'])
  })

  it('offers no delete-all over a filtered list, where "all" would mean more than it shows', async () => {
    const w = await mountReviews([review(1)])

    await w.get('input[type="checkbox"]').setValue(true)
    await flushPromises()

    expect(w.find('[data-testid="delete-all-reviews"]').exists()).toBe(false)
  })

  it('reports a refused delete and keeps the list', async () => {
    http.delete.mockRejectedValue({ message: 'A research job is running.' })
    const w = await mountReviews([review(1)])

    await w.get('[data-testid="delete-all-reviews"]').trigger('click')
    await w.get('[data-testid="delete-all-reviews-confirm"]').trigger('click')
    await flushPromises()

    expect(useUiStore().toasts[0].message).toContain('A research job is running.')
    expect(w.findAll('[data-testid="review-row"]')).toHaveLength(1)
  })

  it('offers read-only people no way to delete', async () => {
    const w = await mountReviews([review(1)], { canWrite: false })

    expect(w.find('[aria-label="Delete the review of Paper 1"]').exists()).toBe(false)
    expect(w.find('[data-testid="delete-all-reviews"]').exists()).toBe(false)
  })
})

describe('tidying the background work list', () => {
  const job = (id, status, over = {}) => ({
    job_id: id, kind: 'brief', status, params: { depth: 'brief' }, done: 1, failed: 0, total: 1, errors: [], ...over,
  })
  const rows = (w) => w.findAll('[data-testid="job-row"]')

  it('removes one finished job from the list without touching the reviews', async () => {
    serverJobs = [job('a', 'done'), job('b', 'failed', { params: { depth: 'thorough' } })]
    http.delete.mockResolvedValue({ data: { removed: 'a' } })
    const w = await mountReviews([review(1)])

    await w.get('[aria-label="Remove Brief reviews from this list"]').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith(expect.stringContaining('/api/research/jobs/a?'))
    expect(rows(w)).toHaveLength(1)
    expect(w.findAll('[data-testid="review-row"]')).toHaveLength(1)
  })

  it('clears every finished job at once and keeps the one still running', async () => {
    serverJobs = [job('run', 'running', { done: 0 }), job('a', 'done'), job('b', 'cancelled')]
    http.delete.mockImplementation(async () => {
      serverJobs = serverJobs.filter(j => j.status === 'running')
      return { data: { removed: 2 } }
    })
    const w = await mountReviews([])

    await w.get('[data-testid="clear-finished-jobs"]').trigger('click')
    await flushPromises()

    expect(http.delete).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/research\/jobs\?/))
    expect(rows(w)).toHaveLength(1)
    expect(w.find('[data-testid="clear-finished-jobs"]').exists()).toBe(false)
  })

  it('offers Stop, not Remove, on a job that is still going', async () => {
    serverJobs = [job('run', 'running', { done: 0 })]
    const w = await mountReviews([])

    expect(rows(w)[0].text()).toContain('Stop')
    expect(w.find('[aria-label="Remove Brief reviews from this list"]').exists()).toBe(false)
    expect(w.find('[data-testid="clear-finished-jobs"]').exists()).toBe(false)
  })

  it('says so when a job could not be removed, and shows what the server still has', async () => {
    serverJobs = [job('a', 'done')]
    http.delete.mockRejectedValue({ message: 'This job is still waiting or running. Stop it first.' })
    const w = await mountReviews([])

    await w.get('[aria-label="Remove Brief reviews from this list"]').trigger('click')
    await flushPromises()

    expect(useUiStore().toasts[0].message).toContain('Stop it first')
    expect(rows(w)).toHaveLength(1)
  })

  it('offers read-only people nothing to clear', async () => {
    serverJobs = [job('a', 'done')]
    const w = await mountReviews([], { canWrite: false })

    expect(rows(w)).toHaveLength(1)
    expect(w.find('[data-testid="clear-finished-jobs"]').exists()).toBe(false)
    expect(w.find('[aria-label="Remove Brief reviews from this list"]').exists()).toBe(false)
  })
})
