import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import http from '../../utils/http'
import EmbeddingMismatchNotice from '../EmbeddingMismatchNotice.vue'
import { useBackgroundJobsStore } from '../../stores/backgroundJobsStore'

const mismatch = (over = {}) => ({
  ok: false, code: 'embedding_mismatch', collection_id: 'docs',
  message: 'The loaded model ...', model: 'llama-nemotron-embed',
  model_dim: 2048, index_dim: 384, fix: 'reindex', can_reindex: true, ...over,
})

async function mountNotice() {
  const w = mount(EmbeddingMismatchNotice, { props: { collectionId: 'docs' } })
  await flushPromises()
  return w
}

describe('EmbeddingMismatchNotice', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    http.get.mockReset()
    http.post.mockReset()
  })

  it('renders nothing when the model fits the index', async () => {
    http.get.mockResolvedValueOnce({ data: { ok: true } })
    const w = await mountNotice()
    expect(w.find('[data-testid="embedding-notice"]').exists()).toBe(false)
    expect(http.get).toHaveBeenCalledWith('/api/collections/docs/embedding-health')
  })

  it('renders nothing when the probe itself fails', async () => {
    http.get.mockRejectedValueOnce(new Error('network'))
    const w = await mountNotice()
    expect(w.find('[data-testid="embedding-notice"]').exists()).toBe(false)
  })

  it('names both dimensions and the model', async () => {
    http.get.mockResolvedValueOnce({ data: mismatch() })
    const w = await mountNotice()
    const text = w.get('[data-testid="mismatch-detail"]').text()
    expect(text).toContain('384-dimensional')
    expect(text).toContain('2,048-dimensional')
    expect(text).toContain('llama-nemotron-embed')
  })

  it('asks for confirmation before re-indexing, then tracks the job', async () => {
    http.get.mockResolvedValueOnce({ data: mismatch() })
    http.post.mockResolvedValueOnce({ data: { job_id: 42 } })
    const w = await mountNotice()
    const jobs = useBackgroundJobsStore()
    const poll = vi.spyOn(jobs, 'startPolling').mockImplementation(() => {})

    await w.get('button.btn-warning').trigger('click')
    expect(http.post).not.toHaveBeenCalled()          // first click only asks
    await w.get('[data-testid="confirm-reindex"]').trigger('click')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/api/collections/docs/reindex')
    expect(jobs.reindexJob).toMatchObject({ id: 42, collection_id: 'docs', status: 'pending' })
    expect(poll).toHaveBeenCalled()
    expect(w.emitted('reindex-started')[0]).toEqual([42])
  })

  it('shows the server reason when re-indexing cannot start', async () => {
    http.get.mockResolvedValueOnce({ data: mismatch() })
    http.post.mockRejectedValueOnce({ response: { data: { detail: 'Re-indexing job 3 is already running' } } })
    const w = await mountNotice()
    await w.get('button.btn-warning').trigger('click')
    await w.get('[data-testid="confirm-reindex"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Re-indexing job 3 is already running')
    expect(w.emitted('reindex-started')).toBeUndefined()
  })

  it('does not offer re-indexing to someone who cannot run it', async () => {
    http.get.mockResolvedValueOnce({ data: mismatch({ can_reindex: false }) })
    const w = await mountNotice()
    expect(w.find('button').exists()).toBe(false)
    expect(w.text()).toContain("owner to re-index")
  })

  it('explains an unavailable model without a re-index button', async () => {
    http.get.mockResolvedValueOnce({ data: { ok: false, code: 'embedding_unavailable', fix: 'model', message: 'hub unreachable' } })
    const w = await mountNotice()
    expect(w.text()).toContain('hub unreachable')
    expect(w.find('button').exists()).toBe(false)
  })

  it('re-checks when a re-index finishes and the warning clears', async () => {
    http.get.mockResolvedValueOnce({ data: mismatch() }).mockResolvedValueOnce({ data: { ok: true } })
    const w = await mountNotice()
    expect(w.find('[data-testid="embedding-notice"]').exists()).toBe(true)
    const jobs = useBackgroundJobsStore()
    jobs.setReindexJob({ id: 1, collection_id: 'docs', status: 'running', total_documents: 1, processed_documents: 0 })
    await flushPromises()
    jobs.setReindexJob({ id: 1, collection_id: 'docs', status: 'completed', total_documents: 1, processed_documents: 1 })
    await flushPromises()
    expect(http.get).toHaveBeenCalledTimes(2)
    expect(w.find('[data-testid="embedding-notice"]').exists()).toBe(false)
  })
})
