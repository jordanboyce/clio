import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
vi.mock('../../stores/providerStore', () => ({ useProviderStore: () => ({ activeProviderId: 'ollama' }) }))
import http from '../../utils/http'
import ResearchTab from '../ResearchTab.vue'
import { useUiStore } from '../../stores/uiStore'

const paper = (id, over = {}) => ({
  document_id: id, filename: `${id}.pdf`, title: null, authors: [], analyzed: false, uploaded: '2026-11-01',
  suggested_filename: null, missing: [], ...over,
})

let wrapper
let serverJobs
let papers

function job(id, kind, status, over = {}) {
  return { job_id: id, kind, status, total: 2, done: 0, failed: 0, errors: [], finished: !['queued', 'running'].includes(status), ...over }
}

async function mountTab() {
  http.get.mockImplementation(async (url) => {
    if (url.startsWith('/api/research/papers')) {
      return { data: { papers, stats: {}, capabilities: {}, can_write: true, naming: { default_pattern: '{title}', fields: ['title'] } } }
    }
    if (url.startsWith('/api/research/screening')) return { data: { items: [], questions: [] } }
    if (url.startsWith('/api/research/jobs')) return { data: { jobs: Object.values(serverJobs) } }
    return { data: {} }
  })
  http.post.mockImplementation(async (url, body) => {
    if (url.includes('/cancel') || url.includes('/restore-name')) return { data: {} }
    if (url.startsWith('/api/research/analyze')) {
      serverJobs.a1 = job('a1', 'analyze', 'running')
      return { data: { job: serverJobs.a1, jobs: [serverJobs.a1] } }
    }
    if (url.startsWith('/api/research/jobs')) {
      serverJobs.s1 = job('s1', 'assess', 'running')
      return { data: { job: serverJobs.s1 } }
    }
    if (url.startsWith('/api/research/rename')) {
      return { data: { renamed: (body.document_ids || []).map(id => ({ document_id: id, previous_filename: `${id}.pdf`, filename: `Named ${id}.pdf` })), skipped: [] } }
    }
    return { data: {} }
  })
  wrapper = mount(ResearchTab, {
    global: { stubs: { ResearchPaperPanel: true, ResearchReviews: true, ResearchWriting: true, ResearchMap: true } },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

const analyzeButton = (w) => w.get('[data-testid="research-analyze-banner"] button')
const screenButton = (w) => w.findAll('button').find(b => b.text().startsWith('Screen'))
const spinning = (button) => button.find('.loading').exists()

beforeEach(() => {
  setActivePinia(createPinia())
  vi.useFakeTimers()
  http.get.mockReset()
  http.post.mockReset()
  serverJobs = {}
  papers = [paper('d1'), paper('d2')]
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

describe('Analyze and Screen at the same time', () => {
  it('clears both spinners: starting Screen does not orphan the Analyze poll', async () => {
    const w = await mountTab()
    await w.get('input[aria-label="Question to screen sources against"]').setValue('root depth?')

    await analyzeButton(w).trigger('click')
    await flushPromises()
    await screenButton(w).trigger('click')
    await flushPromises()
    expect(spinning(analyzeButton(w))).toBe(true)
    expect(spinning(screenButton(w))).toBe(true)

    serverJobs.a1 = job('a1', 'analyze', 'done', { done: 2 })
    papers = [paper('d1', { analyzed: true, title: 'One' }), paper('d2', { analyzed: true, title: 'Two' })]
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    // The banner (and its button) goes once nothing is left to analyze; the
    // "Read all again" button would spin if analyzing were still set.
    expect(w.find('[data-testid="research-analyze-banner"]').exists()).toBe(false)
    expect(w.findAll('button').find(b => b.text().includes('Read all again')).find('.loading').exists()).toBe(false)
    expect(spinning(screenButton(w))).toBe(true)

    serverJobs.s1 = job('s1', 'assess', 'done', { done: 2 })
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(spinning(screenButton(w))).toBe(false)
  })
})

describe('a finish that is not clean says so', () => {
  async function screenUntil(finalJob) {
    const w = await mountTab()
    await w.get('input[aria-label="Question to screen sources against"]').setValue('root depth?')
    await screenButton(w).trigger('click')
    await flushPromises()
    serverJobs.s1 = finalJob
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    return useUiStore().toasts
  }

  it('toasts a job that finished with errors', async () => {
    const toasts = await screenUntil(job('s1', 'assess', 'done_with_errors', { done: 1, failed: 1 }))
    expect(toasts).toHaveLength(1)
    expect(toasts[0].message).toContain('Screening finished with errors')
    expect(toasts[0].message).toContain('1 could not be screened')
  })

  it('toasts a budget stop that failed no paper', async () => {
    const toasts = await screenUntil(job('s1', 'assess', 'failed', { done: 1, failed: 0, current: 'Daily token budget reached' }))
    expect(toasts[0].message).toContain('Screening stopped: Daily token budget reached')
    expect(toasts[0].message).toContain('1 not done')
  })

  it('toasts a cancel', async () => {
    const toasts = await screenUntil(job('s1', 'assess', 'cancelled', { done: 0 }))
    expect(toasts[0].message).toContain('Screening was cancelled')
  })

  it('stays quiet on a clean finish', async () => {
    const toasts = await screenUntil(job('s1', 'assess', 'done', { done: 2 }))
    expect(toasts).toHaveLength(0)
  })
})

describe('the rename preview is a real modal', () => {
  it('opens with showModal and closes through the native close', async () => {
    papers = [paper('d1', { analyzed: true, title: 'One', suggested_filename: 'One.pdf' })]
    const w = await mountTab()
    const showModal = vi.spyOn(HTMLDialogElement.prototype, 'showModal')
    await w.findAll('button').find(b => b.text().startsWith('Rename to descriptive names')).trigger('click')
    await flushPromises()
    const dialog = w.get('dialog[aria-labelledby="research-rename-title"]')
    expect(showModal).toHaveBeenCalledTimes(1)
    expect(dialog.element.open).toBe(true)
    expect(dialog.attributes('open')).toBeDefined()

    // Escape closes a native modal without going through any button.
    dialog.element.close()
    await flushPromises()
    expect(dialog.element.open).toBe(false)
    // and it can be opened again (its state followed the native close)
    await w.findAll('button').find(b => b.text().startsWith('Rename to descriptive names')).trigger('click')
    expect(showModal).toHaveBeenCalledTimes(2)
  })
})

describe('the order of work (R10)', () => {
  it('shows the four steps with their counts, and screens only what is not screened yet', async () => {
    papers = [paper('d1', { analyzed: true, title: 'One', review_count: 1 }), paper('d2', { analyzed: true, title: 'Two' })]
    const w = await mountTab()
    http.get.mockImplementation(async (url) => {
      if (url.startsWith('/api/research/screening')) {
        return { data: { questions: ['root depth?'], items: [{ document_id: 'd1', verdict: 'relevant', evidence: [] }] } }
      }
      if (url.startsWith('/api/research/papers')) return { data: { papers, stats: {}, capabilities: {}, can_write: true } }
      return { data: { jobs: [] } }
    })
    const steps = w.get('[data-testid="library-steps"]')
    expect(steps.get('[data-testid="step-analyze"]').text()).toMatch(/Analyzed\s*2 of 2/)
    expect(steps.get('[data-testid="step-review"]').text()).toMatch(/Reviewed\s*1 of 2/)
    expect(steps.get('[data-testid="step-screen"]').text()).toContain('1 model call per source')

    await w.get('input[aria-label="Question to screen sources against"]').setValue('root depth?')
    await vi.advanceTimersByTimeAsync(500)
    await flushPromises()
    expect(steps.get('[data-testid="step-screen"]').text()).toMatch(/Screened\s*1 of 2/)
    expect(screenButton(w).text()).toBe('Screen 1 more source')

    await screenButton(w).trigger('click')
    await flushPromises()
    const body = http.post.mock.calls.find(([url]) => url.startsWith('/api/research/jobs'))[1]
    expect(body).toMatchObject({ kind: 'screen', document_ids: ['d2'] })
  })
})

describe('records that are not sure say so (A1)', () => {
  it('shows the file name for a doubtful title and never states doubtful authors', async () => {
    papers = [
      paper('d1', { analyzed: true, title: 'Right Title', authors: ['Ines Halvorsen'], needs_checking: {} }),
      paper('d2', {
        analyzed: true, filename: 'gulls.pdf', title: 'We count nesting gulls on city rooftops from fixed cameras',
        authors: ['Imaginary Urban Ecology'], needs_checking: { title: 'it stops mid-sentence', authors: 'reads like a journal' },
      }),
    ]
    const w = await mountTab()
    const rows = w.get('[data-testid="research-paper-list"]').findAll('li')
    expect(rows[0].text()).toContain('Right Title')
    expect(rows[0].find('[data-testid="needs-checking"]').exists()).toBe(false)
    const doubtfulRow = rows.find(r => r.text().includes('gulls.pdf'))
    expect(doubtfulRow.text()).not.toContain('We count nesting gulls')
    expect(doubtfulRow.text()).toContain('Authors need checking')
    expect(doubtfulRow.text()).not.toContain('Ecology')
    expect(doubtfulRow.find('[data-testid="needs-checking"]').exists()).toBe(true)
  })

  it('filters to the records that need checking, with a count', async () => {
    papers = [
      paper('d1', { analyzed: true, title: 'One', needs_checking: {} }),
      paper('d2', { analyzed: true, title: 'Two', needs_checking: { title: 'why' } }),
    ]
    const w = await mountTab()
    const filter = w.get('[data-testid="needs-checking-filter"]')
    expect(filter.text()).toContain('(1)')
    await filter.get('input').setValue(true)
    const rows = w.get('[data-testid="research-paper-list"]').findAll('li')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('d2.pdf')
  })
})

describe('bulk rename ticks only names built from records known to be right (A3)', () => {
  it('pre-ticks ready records and lists the rest unticked under Check these first', async () => {
    papers = [
      paper('d1', { analyzed: true, title: 'One', suggested_filename: 'One.pdf', rename_ready: true }),
      paper('d2', { analyzed: true, title: 'Two', suggested_filename: 'Two.pdf', rename_ready: false, needs_checking: { title: 'why' } }),
    ]
    const w = await mountTab()
    await w.findAll('button').find(b => b.text().startsWith('Rename to descriptive names')).trigger('click')
    await flushPromises()
    const ready = w.get('[data-testid="rename-ready"]')
    const later = w.get('[data-testid="rename-check-first"]')
    expect(ready.findAll('input[type="checkbox"]').map(i => i.element.checked)).toEqual([true])
    expect(later.text()).toContain('Check these first')
    expect(later.findAll('input[type="checkbox"]').map(i => i.element.checked)).toEqual([false])
    expect(w.findAll('button').find(b => b.text().startsWith('Rename 1')).exists()).toBe(true)
  })

  it('offers Undo all after renaming several', async () => {
    papers = [
      paper('d1', { analyzed: true, title: 'One', suggested_filename: 'One.pdf', rename_ready: true }),
      paper('d2', { analyzed: true, title: 'Two', suggested_filename: 'Two.pdf', rename_ready: true }),
    ]
    const w = await mountTab()
    await w.findAll('button').find(b => b.text().startsWith('Rename to descriptive names')).trigger('click')
    await flushPromises()
    await w.findAll('button').find(b => b.text().startsWith('Rename 2')).trigger('click')
    await flushPromises()
    const toast = useUiStore().toasts.find(t => t.action)
    expect(toast.action.label).toBe('Undo all')
    toast.action.run()
    await flushPromises()
    const restores = http.post.mock.calls.filter(([url]) => url.includes('/restore-name'))
    expect(restores).toHaveLength(2)
  })

  it('says Analyze first instead of a disabled button with no reason', async () => {
    papers = [paper('d1')]
    const w = await mountTab()
    expect(w.get('[data-testid="rename-needs-analysis"]').text()).toContain('analyze first')
  })
})

describe('one job strip for the Library (A5)', () => {
  it('comes back after a reload with the right count, and Stop cancels the job', async () => {
    // A reload: the tab mounts while an analysis started earlier still runs.
    serverJobs.a9 = job('a9', 'analyze', 'running', { total: 7, done: 3, failed: 1 })
    const w = await mountTab()
    const strip = w.get('[data-testid="library-job-strip"]')
    expect(strip.text()).toContain('Analyzing')
    expect(strip.get('[data-testid="library-job-count"]').text()).toBe('4 of 7')
    expect(strip.text()).toContain('1 failed')
    expect(spinning(analyzeButton(w))).toBe(true)

    await strip.findAll('button').find(b => b.text() === 'Stop').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls.some(([url]) => url.includes('/api/research/jobs/a9/cancel'))).toBe(true)
    expect(w.get('[data-testid="library-job-strip"]').text()).toContain('Stopping…')

    serverJobs.a9 = job('a9', 'analyze', 'cancelled', { total: 7, done: 3, failed: 1 })
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(w.find('[data-testid="library-job-strip"]').exists()).toBe(false)
    expect(useUiStore().toasts[0].message).toContain('Analysis was cancelled')
  })

  it('shows screening and reviews started elsewhere', async () => {
    serverJobs.s2 = job('s2', 'assess', 'running', { params: { question: 'root depth?' } })
    serverJobs.b2 = job('b2', 'brief', 'queued', { params: { depth: 'thorough' } })
    const w = await mountTab()
    const text = w.get('[data-testid="library-job-strip"]').text()
    expect(text).toContain('Screening for “root depth?”')
    expect(text).toContain('Preparing thorough reviews')
  })
})

