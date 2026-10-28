import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import CoverageReportPanel from '../CoverageReportPanel.vue'

const report = (over = {}) => ({
  overall: { probes: 40, document_hit_at_5: 0.9, chunk_hit_at_10: 0.8, mrr_chunk: 0.71 },
  notes: ['This measures retrieval findability, not whether answers are correct.'],
  probe_method: { by_method: { keywords: { probes: 38 }, heading: { probes: 2 } } },
  embedding_model: 'bge-small', duration_seconds: 4.2, age: '3h', stale: false, stale_reasons: [],
  documents: [{ document_id: 'd1' }],
  weakest_documents: [{
    document_id: 'd1', filename: 'notice.txt', document_hit_at_5: 0, probes: 2,
    failing_probes: [{ chunk_id: 'd1#1', query: 'notice office unclear', preview: 'This notice applies…' }],
  }],
  chunks_never_retrieved: { count: 3 }, unprobeable_chunks: { count: 1 },
  owner_questions: { items: [
    { id: 1, status: 'hit' }, { id: 2, status: 'miss' },
  ] },
  ...over,
})
const payload = (over = {}) => ({
  data: { report: report(), run: { state: 'idle' }, questions: [], llm_questions: false, ...over },
})

async function mountPanel(props = {}) {
  const w = mount(CoverageReportPanel, { props: { collectionId: 'docs', canRun: true, pollMs: 10, ...props } })
  await flushPromises()
  return w
}

describe('CoverageReportPanel', () => {
  beforeEach(() => { http.get.mockReset(); http.post.mockReset(); http.delete.mockReset() })
  afterEach(() => vi.useRealTimers())

  it('shows the score, the scope disclaimer and the weakest documents with their failing probes', async () => {
    http.get.mockResolvedValueOnce(payload())
    const w = await mountPanel()
    expect(http.get).toHaveBeenCalledWith('/api/collections/docs/coverage')
    expect(w.get('[data-testid="coverage-doc-hit"]').text()).toBe('90%')
    expect(w.text()).toContain('not whether answers built from them are correct')
    expect(w.get('[data-testid="coverage-weakest"]').text()).toContain('notice.txt')
    expect(w.get('[data-testid="coverage-failing-probe"]').text()).toContain('notice office unclear')
    expect(w.text()).toContain('3 probed passages never came back')
    expect(w.text()).toContain('1 passage has no searchable words')
    expect(w.text()).toContain('keywords 38, heading 2')
    expect(w.find('[data-testid="coverage-stale"]').exists()).toBe(false)
  })

  it('flags a stale report and says why', async () => {
    http.get.mockResolvedValueOnce(payload({ report: report({ stale: true, stale_reasons: ['corpus_changed'] }) }))
    const w = await mountPanel()
    expect(w.get('[data-testid="coverage-stale"]').text()).toContain('sources were added, removed or changed')
  })

  it('renders model-fallback notes beyond the disclaimer', async () => {
    http.get.mockResolvedValueOnce(payload({ report: report({
      notes: ['This measures retrieval findability, not whether answers are correct.',
        'Model-written questions failed (boom); heuristic probes ran instead.'],
    }) }))
    const w = await mountPanel()
    const notes = w.findAll('[data-testid="coverage-note"]')
    expect(notes).toHaveLength(1)
    expect(notes[0].text()).toContain('heuristic probes ran instead')
  })

  it('says so when every probed source was found', async () => {
    http.get.mockResolvedValueOnce(payload({ report: report({ weakest_documents: [] }) }))
    const w = await mountPanel()
    expect(w.find('[data-testid="coverage-weakest"]').exists()).toBe(false)
    expect(w.find('[data-testid="coverage-all-found"]').exists()).toBe(true)
  })

  it('offers a first run when there is no report', async () => {
    http.get.mockResolvedValueOnce(payload({ report: null }))
    const w = await mountPanel()
    expect(w.get('[data-testid="coverage-empty"]').text()).toContain('Run one')
    expect(w.get('[data-testid="coverage-run"]').text()).toBe('Run report')
  })

  it('gives readers the report but no controls', async () => {
    http.get.mockResolvedValueOnce(payload({ questions: [
      { id: 1, question: 'How do I rotate a key?', expected_documents: [{ filename: 'keys.md' }] }] }))
    const w = await mountPanel({ canRun: false })
    expect(w.find('[data-testid="coverage-run"]').exists()).toBe(false)
    expect(w.find('[data-testid="coverage-question-form"]').exists()).toBe(false)
    expect(w.find('[data-testid="coverage-question-delete"]').exists()).toBe(false)
    expect(w.text()).toContain('How do I rotate a key?')          // the questions stay visible
    expect(w.get('[data-testid="coverage-doc-hit"]').text()).toBe('90%')
  })

  it('tells a reader with no report to ask the owner', async () => {
    http.get.mockResolvedValueOnce(payload({ report: null }))
    const w = await mountPanel({ canRun: false })
    expect(w.get('[data-testid="coverage-empty"]').text()).toContain('owner can run one')
  })

  it('runs a report, follows progress, and shows the result', async () => {
    http.get
      .mockResolvedValueOnce(payload({ report: null }))
      .mockResolvedValueOnce(payload({ report: null, run: { state: 'running', done: 5, total: 40 } }))
      .mockResolvedValueOnce(payload())
    http.post.mockResolvedValueOnce({ data: { run: { state: 'running' } } })
    const w = await mountPanel()
    await w.get('[data-testid="coverage-run"]').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/collections/docs/coverage', {})
    const btn = w.get('[data-testid="coverage-run"]')
    expect(btn.text()).toBe('Running 5/40')
    expect(btn.attributes('disabled')).toBeDefined()
    await new Promise((r) => setTimeout(r, 30))                    // the poll timer fires
    await flushPromises()
    expect(w.get('[data-testid="coverage-doc-hit"]').text()).toBe('90%')
    expect(w.get('[data-testid="coverage-run"]').text()).toBe('Run again')
  })

  it('follows a run that is already going when the server answers 409', async () => {
    http.get
      .mockResolvedValueOnce(payload({ report: null }))
      .mockResolvedValue(payload({ report: null, run: { state: 'running', done: 1, total: 10 } }))
    http.post.mockRejectedValueOnce({ status: 409, message: 'already in progress' })
    const w = await mountPanel()
    await w.get('[data-testid="coverage-run"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="coverage-error"]').exists()).toBe(false)
    expect(w.get('[data-testid="coverage-run"]').text()).toContain('Running')
    w.unmount()                                                     // stops the poll loop
  })

  it('shows the server message when a run cannot start', async () => {
    http.get.mockResolvedValueOnce(payload({ report: null }))
    http.post.mockRejectedValueOnce({ status: 403, message: 'Only the owner can do that' })
    const w = await mountPanel()
    await w.get('[data-testid="coverage-run"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="coverage-error"]').text()).toBe('Only the owner can do that')
  })

  it('shows a failed run', async () => {
    http.get.mockResolvedValueOnce(payload({ report: null, run: { state: 'failed', error: 'disk full' } }))
    const w = await mountPanel()
    expect(w.text()).toContain('The last run failed: disk full')
  })

  describe('owner questions', () => {
    const questions = [
      { id: 1, question: 'How do I rotate a key?', expected_documents: [{ filename: 'keys.md' }] },
      { id: 2, question: 'Where is the roadmap?', expected_documents: [{ filename: 'plan.md' }] },
    ]

    it('lists them with their latest result', async () => {
      http.get.mockResolvedValueOnce(payload({ questions }))
      const w = await mountPanel()
      const badges = w.findAll('[data-testid="coverage-question-status"]').map((b) => b.text())
      expect(badges).toEqual(['found', 'not found'])
      expect(w.text()).toContain('keys.md')
    })

    it('adds a question with its expected files and refreshes', async () => {
      http.get.mockResolvedValueOnce(payload({ questions: [] })).mockResolvedValueOnce(payload({ questions }))
      http.post.mockResolvedValueOnce({ data: { id: 1 } })
      const w = await mountPanel()
      const [q, files] = w.get('[data-testid="coverage-question-form"]').findAll('input')
      await q.setValue('  How do I rotate a key? ')
      await files.setValue('keys.md, security.md ,')
      await w.get('[data-testid="coverage-question-form"]').trigger('submit')
      await flushPromises()
      expect(http.post).toHaveBeenCalledWith('/api/collections/docs/coverage/questions', {
        question: 'How do I rotate a key?', expected_documents: ['keys.md', 'security.md'],
      })
      expect(w.findAll('[data-testid="coverage-question-delete"]')).toHaveLength(2)
      expect(q.element.value).toBe('')
    })

    it('will not submit without a question and an expected file', async () => {
      http.get.mockResolvedValueOnce(payload({ questions: [] }))
      const w = await mountPanel()
      const submit = w.get('[data-testid="coverage-question-form"] button[type="submit"]')
      expect(submit.attributes('disabled')).toBeDefined()
      await w.get('[data-testid="coverage-question-form"] input').setValue('Something?')
      expect(submit.attributes('disabled')).toBeDefined()
    })

    it('shows the server reason when a question is rejected, and keeps the text', async () => {
      http.get.mockResolvedValueOnce(payload({ questions: [] }))
      http.post.mockRejectedValueOnce({ status: 400, message: 'No document named missing.md' })
      const w = await mountPanel()
      const [q, files] = w.get('[data-testid="coverage-question-form"]').findAll('input')
      await q.setValue('Where is the roadmap?')
      await files.setValue('missing.md')
      await w.get('[data-testid="coverage-question-form"]').trigger('submit')
      await flushPromises()
      expect(w.get('[data-testid="coverage-question-error"]').text()).toBe('No document named missing.md')
      expect(q.element.value).toBe('Where is the roadmap?')
    })

    it('deletes a question', async () => {
      http.get.mockResolvedValueOnce(payload({ questions })).mockResolvedValueOnce(payload({ questions: [questions[1]] }))
      http.delete.mockResolvedValueOnce({ data: { deleted: 1 } })
      const w = await mountPanel()
      await w.findAll('[data-testid="coverage-question-delete"]')[0].trigger('click')
      await flushPromises()
      expect(http.delete).toHaveBeenCalledWith('/api/collections/docs/coverage/questions/1')
      expect(w.findAll('[data-testid="coverage-question-delete"]')).toHaveLength(1)
    })
  })

  it('reloads when the collection changes', async () => {
    http.get.mockResolvedValueOnce(payload()).mockResolvedValueOnce(payload({ report: null }))
    const w = await mountPanel()
    await w.setProps({ collectionId: 'other' })
    await flushPromises()
    expect(http.get).toHaveBeenLastCalledWith('/api/collections/other/coverage')
    expect(w.find('[data-testid="coverage-doc-hit"]').exists()).toBe(false)
  })
})
