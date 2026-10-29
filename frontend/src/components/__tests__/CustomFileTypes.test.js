import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))
import http from '../../utils/http'
import CustomFileTypes from '../CustomFileTypes.vue'
import { useUserStore } from '../../stores/userStore'
import { useUiStore } from '../../stores/uiStore'
import { useBackgroundJobsStore } from '../../stores/backgroundJobsStore'
import { describeFile, setCustomFileTypes } from '../../utils/fileTypes'

const ACME = {
  extension: '.acme', label: 'Acme export', kind: 'data', source: 'heuristic', enabled: true, format: 'auto',
  record_path: 'pumps', title_field: 'name', include: [], exclude: ['id'], labels: { psi: 'Pressure (psi)' },
  records_per_section: 10, max_value_chars: 500, description: '',
  updated_at: '2026-10-28T10:00:00+00:00', updated_by: 'admin@example.com', reading_updated_at: '2026-10-28T10:00:00+00:00',
}
const PREVIEW = { records: 2, sections: 1, chars: 40, text: 'Pump A\npsi: 62' }

const usageFor = (over = {}) => ({
  extension: '.acme', registered: true, enabled: true, total_documents: 3, total_out_of_date: 2,
  collections: [
    { collection_id: 'c1', name: 'Plant', documents: 2, out_of_date: 2, can_reindex: true },
    { collection_id: 'c2', name: 'Shared', documents: 1, out_of_date: 0, can_reindex: false },
  ],
  unavailable: [], ...over,
})

// jsdom has no <dialog> behaviour.
beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})

// Every mounted panel is unmounted after its test. Clearing the page is not
// enough: a panel left mounted keeps its debounced preview timer (a real
// 300 ms setTimeout), which on a busy machine fired during a later test and
// posted through the shared http mock.
const mounted = []
afterEach(() => {
  while (mounted.length) mounted.pop().unmount()
})

let routes
function route(map) {
  routes = map
  http.get.mockImplementation(async (url) => {
    const r = routes.get?.[url]
    if (r instanceof Error || r?.status) throw r
    if (r === undefined) throw { status: 404, message: 'nope' }
    return { data: typeof r === 'function' ? r() : r }
  })
}

async function mountPanel({ admin = true, types = [ACME], ai = true, extra = {} } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.adminConsole = admin
  route({ get: { '/api/custom-file-types': { types, ai: { available: ai } }, ...extra } })
  const w = mount(CustomFileTypes, { global: { plugins: [pinia] }, attachTo: document.body })
  mounted.push(w)
  await flushPromises()
  return w
}

function pickFile(w, selector, content = '{"pumps":[{"name":"Pump A"}]}', name = 'sample.acme') {
  const input = w.get(selector)
  const file = new File([content], name)
  file.text = async () => content
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  return input.trigger('change')
}

const suggestion = (over = {}) => ({
  extension: '.acme', source: 'heuristic',
  profile: { ...ACME, source: 'heuristic' }, preview: PREVIEW, ...over,
})

async function openEdit(w) {
  await w.get('[data-testid="cft-edit"]').trigger('click')
  await flushPromises()
}

describe('CustomFileTypes', () => {
  beforeEach(() => {
    Object.values(http).forEach(f => f.mockReset())
    localStorage.clear()
  })
  afterEach(() => { vi.useRealTimers(); document.body.innerHTML = '' })

  it('lists the registered types from the open endpoint', async () => {
    const w = await mountPanel()
    const row = w.get('[data-testid="cft-row-.acme"]')
    expect(row.text()).toContain('Acme export')
    expect(row.text()).toContain('data')
    expect(row.text()).toContain('heuristic')
    expect(http.get).toHaveBeenCalledWith('/api/custom-file-types', { quiet: true })
  })

  it('shows an empty state when nothing is registered', async () => {
    const w = await mountPanel({ types: [] })
    expect(w.text()).toContain('No custom file types registered')
  })

  it('offers nothing to a non-admin, and still loads the list for the rest of the app', async () => {
    const w = await mountPanel({ admin: false })
    expect(w.find('[data-testid="cft-not-admin"]').exists()).toBe(true)
    expect(w.find('[data-testid="cft-add"]').exists()).toBe(false)
    expect(w.find('[data-testid="cft-list"]').exists()).toBe(false)
    expect(http.get).toHaveBeenCalledWith('/api/custom-file-types', { quiet: true })
  })

  it('hides the controls when the API answers 403 even though the UI thought it was admin', async () => {
    const w = await mountPanel({ types: [] })
    http.post.mockRejectedValueOnce({ status: 403, message: 'Only an admin (ADMIN_EMAILS) may manage custom file types.' })
    await w.get('[data-testid="cft-add"]').trigger('click')
    await w.get('#cft-extension').setValue('.acme')
    await pickFile(w, '#cft-sample')
    await w.get('[data-testid="cft-suggest"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="cft-forbidden"]').exists()).toBe(true)
    expect(w.find('[data-testid="cft-add"]').exists()).toBe(false)
  })

  describe('suggest', () => {
    async function suggestFlow({ ai = true, result = suggestion() } = {}) {
      const w = await mountPanel({ types: [], ai })
      http.post.mockResolvedValueOnce({ data: result })
      await w.get('[data-testid="cft-add"]').trigger('click')
      await pickFile(w, '#cft-sample')
      expect(w.get('#cft-extension').element.value).toBe('.acme')     // defaulted from the file name
      await w.get('[data-testid="cft-suggest"]').trigger('click')
      await flushPromises()
      return w
    }

    it('uploads the sample with the extension and asks for AI only when a model exists', async () => {
      const w = await suggestFlow({ ai: true })
      const [url, body] = http.post.mock.calls[0]
      expect(url).toBe('/api/custom-file-types/suggest')
      expect(body.get('extension')).toBe('.acme')
      expect(body.get('use_ai')).toBe('true')
      expect(body.get('file').name).toBe('sample.acme')
      expect(w.get('#cft-record-path').element.value).toBe('pumps')
      expect(w.get('[data-testid="cft-preview"]').text()).toContain('Pump A')
    })

    it('disables the AI option, with the reason, when no chat model is configured', async () => {
      const w = await mountPanel({ types: [], ai: false })
      await w.get('[data-testid="cft-add"]').trigger('click')
      expect(w.get('[data-testid="cft-use-ai"]').element.disabled).toBe(true)
      expect(w.get('[data-testid="cft-no-ai"]').text()).toContain('No chat model is configured')
      http.post.mockResolvedValueOnce({ data: suggestion() })
      await pickFile(w, '#cft-sample')
      await w.get('[data-testid="cft-suggest"]').trigger('click')
      await flushPromises()
      expect(http.post.mock.calls[0][1].get('use_ai')).toBe('false')
    })

    it('says so when the AI pass fell back to rules, and why', async () => {
      const w = await suggestFlow({ result: suggestion({
        ai: { requested: true, used: false, error: 'The model answered with a path that is not in the sample.' } }) })
      const notice = w.get('[data-testid="cft-ai-fallback"]')
      expect(notice.text()).toContain('fell back to rules')
      expect(notice.text()).toContain('not in the sample')
    })

    it('shows no fallback notice when AI was used, or was never requested', async () => {
      let w = await suggestFlow({ result: suggestion({ source: 'ai', ai: { requested: true, used: true } }) })
      expect(w.find('[data-testid="cft-ai-fallback"]').exists()).toBe(false)
      w.unmount()
      w = await suggestFlow({ result: suggestion({ ai: { requested: false, used: false } }) })
      expect(w.find('[data-testid="cft-ai-fallback"]').exists()).toBe(false)
    })

    it('shows the server reason when the sample cannot be read', async () => {
      const w = await mountPanel({ types: [] })
      http.post.mockRejectedValueOnce({ status: 400, message: 'Not valid JSON at line 1, column 3' })
      await w.get('[data-testid="cft-add"]').trigger('click')
      await pickFile(w, '#cft-sample')
      await w.get('[data-testid="cft-suggest"]').trigger('click')
      await flushPromises()
      expect(w.get('[data-testid="cft-suggest-error"]').text()).toContain('line 1, column 3')
    })

    it('saves the suggestion with its own source, and "manual" once the admin edits it', async () => {
      let w = await suggestFlow({ result: suggestion({ source: 'ai' }) })
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: true, usage: usageFor({ total_documents: 0, collections: [] }) } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      expect(http.put.mock.calls[0][0]).toBe('/api/custom-file-types/.acme')
      expect(http.put.mock.calls[0][1].source).toBe('ai')
      w.unmount()

      http.put.mockReset()
      w = await suggestFlow({ result: suggestion({ source: 'ai' }) })
      await w.get('#cft-title-field').setValue('label')
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: true, usage: usageFor({ total_documents: 0, collections: [] }) } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      expect(http.put.mock.calls[0][1]).toMatchObject({ source: 'manual', title_field: 'label' })
    })
  })

  describe('editing', () => {
    it('re-renders the preview once, 300 ms after the last keystroke, with the edited profile', async () => {
      const w = await mountPanel()
      // Fake timers before the editor opens: opening it schedules a preview,
      // and a real 300 ms timer from that moment would fire mid-test on a
      // slow machine and post a request this test does not expect.
      vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
      await openEdit(w)
      http.post.mockResolvedValue({ data: { preview: { ...PREVIEW, text: 'edited preview' } } })
      await pickFile(w, '#cft-preview-sample')          // an edit has no sample until one is picked
      await vi.advanceTimersByTimeAsync(300)
      // The sample's own preview reads the file and posts after two awaits;
      // let it land before clearing, or it is miscounted as an early call.
      await flushPromises()
      http.post.mockClear()

      await w.get('#cft-exclude').setValue('id, site')
      await vi.advanceTimersByTimeAsync(100)
      await w.get('#cft-exclude').setValue('id, site, psi')
      await vi.advanceTimersByTimeAsync(299)
      expect(http.post).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(1)
      await flushPromises()

      expect(http.post).toHaveBeenCalledTimes(1)
      const [url, body] = http.post.mock.calls[0]
      expect(url).toBe('/api/custom-file-types/preview')
      expect(body.extension).toBe('.acme')
      expect(body.profile.exclude).toEqual(['id', 'site', 'psi'])
      expect(body.profile.labels).toEqual({ psi: 'Pressure (psi)' })
      expect(w.get('[data-testid="cft-preview"]').text()).toContain('edited preview')
    })

    it('shows the server explanation when a draft profile does not fit the sample', async () => {
      const w = await mountPanel()
      await openEdit(w)
      http.post.mockRejectedValue({ status: 400, message: "record_path 'nope' was not found in the sample" })
      await pickFile(w, '#cft-preview-sample')
      await w.get('#cft-record-path').setValue('nope')
      await new Promise(r => setTimeout(r, 350))
      await flushPromises()
      expect(w.get('[data-testid="cft-preview-error"]').text()).toContain("'nope' was not found")
    })

    it('keeps a 400 from save inside the dialog, readable', async () => {
      const w = await mountPanel()
      await openEdit(w)
      http.put.mockRejectedValueOnce({ status: 400, message: "'include' and 'exclude' overlap on 'id'", detail: "'include' and 'exclude' overlap on 'id'" })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      expect(w.get('[data-testid="cft-save-error"]').text()).toContain("overlap on 'id'")
      expect(w.get('[data-testid="cft-save"]').exists()).toBe(true)      // dialog still open
    })

    it('keeps the stored source when nothing was changed, and sends the whole profile', async () => {
      const w = await mountPanel()
      await openEdit(w)
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: false, usage: usageFor() } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      const body = http.put.mock.calls[0][1]
      expect(body).toMatchObject({ source: 'heuristic', record_path: 'pumps', title_field: 'name', enabled: true,
        exclude: ['id'], labels: { psi: 'Pressure (psi)' }, kind: 'data', label: 'Acme export' })
    })
  })

  describe('after a save that changes how files are read', () => {
    async function savedWithFiles() {
      const w = await mountPanel()
      await openEdit(w)
      await w.get('#cft-exclude').setValue('id, site')
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: true, affected_documents: 3, usage: usageFor() } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      return w
    }

    it('says how many indexed files use the extension, per collection, with a re-index button where allowed', async () => {
      const w = await savedWithFiles()
      expect(w.get('[data-testid="cft-usage-summary"]').text()).toContain('3 indexed files use .acme')
      expect(w.get('[data-testid="cft-usage-summary"]').text()).toContain('2 were indexed with an older profile')
      expect(w.get('[data-testid="cft-usage-row-c1"]').text()).toContain('Re-index 2 files')
      expect(w.get('[data-testid="cft-usage-row-c2"]').text()).toContain("Only this collection's owner can re-index it")
      expect(w.get('[data-testid="cft-usage-row-c2"]').find('[data-testid="cft-reindex"]').exists()).toBe(false)
    })

    it('re-indexes one collection with the {extensions} body and hands the job to the jobs store', async () => {
      const w = await savedWithFiles()
      const jobs = useBackgroundJobsStore()
      const poll = vi.spyOn(jobs, 'startPolling').mockImplementation(() => {})
      http.post.mockResolvedValueOnce({ data: { job_id: 42 } })
      await w.get('[data-testid="cft-reindex"]').trigger('click')
      await flushPromises()
      expect(http.post).toHaveBeenCalledWith('/api/collections/c1/reindex-extensions', { extensions: ['.acme'] })
      expect(jobs.reindexJob).toMatchObject({ id: 42, collection_id: 'c1', status: 'pending' })
      expect(poll).toHaveBeenCalled()
    })

    it('shows the reason when another re-index is already running (409)', async () => {
      const w = await savedWithFiles()
      http.post.mockRejectedValueOnce({ status: 409, message: 'Re-indexing job 7 is already running' })
      await w.get('[data-testid="cft-reindex"]').trigger('click')
      await flushPromises()
      expect(w.get('[data-testid="cft-usage-error"]').text()).toContain('already running')
    })

    it('does not open the offer when the save changed nothing about reading, or no file uses the type', async () => {
      let w = await mountPanel()
      await openEdit(w)
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: false, usage: usageFor() } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      expect(w.find('[data-testid="cft-usage"]').exists()).toBe(false)
      w.unmount()

      w = await mountPanel()
      await openEdit(w)
      http.put.mockResolvedValueOnce({ data: { profile: ACME, reading_changed: true, usage: usageFor({ total_documents: 0, collections: [] }) } })
      await w.get('[data-testid="cft-save"]').trigger('click')
      await flushPromises()
      expect(w.find('[data-testid="cft-usage"]').exists()).toBe(false)
    })

    it('can be reopened later from the "Files" button, and refuses re-indexing a switched-off type', async () => {
      const w = await mountPanel({ extra: { '/api/custom-file-types/.acme/usage': usageFor({ enabled: false }) } })
      await w.get('[data-testid="cft-files"]').trigger('click')
      await flushPromises()
      expect(w.get('[data-testid="cft-usage-summary"]').text()).toContain('3 indexed files use .acme')
      expect(w.find('[data-testid="cft-reindex"]').exists()).toBe(false)
      expect(w.get('[data-testid="cft-usage-no-reindex"]').text()).toContain('switched off')
    })
  })

  describe('delete', () => {
    it('counts the indexed files first, says they stay searchable, and offers no re-index', async () => {
      const w = await mountPanel({ extra: { '/api/custom-file-types/.acme/usage': usageFor() } })
      await w.get('[data-testid="cft-delete"]').trigger('click')
      await flushPromises()
      const text = w.get('[data-testid="cft-delete-usage"]').text()
      expect(text).toContain('3 indexed files use .acme')
      expect(text).toContain('Plant: 2 files')
      expect(text).toContain('cannot be re-indexed once the type is gone')
      expect(w.text()).toContain('stay searchable')
      expect(w.find('[data-testid="cft-reindex"]').exists()).toBe(false)
    })

    it('deletes, tells the admin how many files stay searchable, and refreshes the list', async () => {
      const w = await mountPanel({ extra: { '/api/custom-file-types/.acme/usage': usageFor() } })
      const ui = useUiStore()
      const notify = vi.spyOn(ui, 'notify')
      await w.get('[data-testid="cft-delete"]').trigger('click')
      await flushPromises()
      http.delete.mockResolvedValueOnce({ data: { deleted: true, affected_documents: 3, usage: usageFor({ registered: false }) } })
      routes.get['/api/custom-file-types'] = { types: [], ai: { available: true } }
      await w.get('[data-testid="cft-confirm-delete"]').trigger('click')
      await flushPromises()
      expect(http.delete).toHaveBeenCalledWith('/api/custom-file-types/.acme')
      expect(notify.mock.calls.at(-1)[0]).toMatch(/Deleted \.acme\. 3 indexed files use \.acme and stay searchable/)
      expect(w.text()).toContain('No custom file types registered')
    })

    it('still lets the admin delete when the count cannot be read', async () => {
      const w = await mountPanel({ extra: { '/api/custom-file-types/.acme/usage': { status: 500, message: 'boom' } } })
      await w.get('[data-testid="cft-delete"]').trigger('click')
      await flushPromises()
      expect(w.text()).toContain('Could not count the indexed files')
      http.delete.mockResolvedValueOnce({ data: { deleted: true, affected_documents: null, usage: null } })
      await w.get('[data-testid="cft-confirm-delete"]').trigger('click')
      await flushPromises()
      expect(http.delete).toHaveBeenCalled()
    })

    it('shows a failed delete in the dialog', async () => {
      const w = await mountPanel({ extra: { '/api/custom-file-types/.acme/usage': usageFor() } })
      await w.get('[data-testid="cft-delete"]').trigger('click')
      await flushPromises()
      http.delete.mockRejectedValueOnce({ status: 404, message: 'No custom file type .acme' })
      await w.get('[data-testid="cft-confirm-delete"]').trigger('click')
      await flushPromises()
      expect(w.get('[data-testid="cft-delete-error"]').text()).toContain('No custom file type')
    })
  })

  it('switching a type off sends the whole stored profile, not just {enabled}', async () => {
    const w = await mountPanel()
    http.put.mockResolvedValueOnce({ data: { profile: { ...ACME, enabled: false } } })
    const toggle = w.get('[data-testid="cft-toggle"]')
    toggle.element.checked = false
    await toggle.trigger('change')
    await flushPromises()
    const [url, body] = http.put.mock.calls[0]
    expect(url).toBe('/api/custom-file-types/.acme')
    expect(body).toMatchObject({ enabled: false, record_path: 'pumps', title_field: 'name', exclude: ['id'], source: 'heuristic' })
    expect(body.updated_at).toBeUndefined()
  })
})

describe('registered types in the file tiles', () => {
  beforeEach(() => setCustomFileTypes([]))   // the registry is module state; earlier tests filled it
  afterEach(() => setCustomFileTypes([]))

  it('draws a registered extension with its profile kind, never as "other"', () => {
    expect(describeFile('pumps.acme').family).toBe('other')
    setCustomFileTypes([ACME, { extension: '.cfgx', kind: 'code', label: 'Config X', enabled: false }])
    expect(describeFile('pumps.ACME')).toMatchObject({ family: 'data', label: 'acme' })
    expect(describeFile('a/b/site.cfgx').family).toBe('code')       // disabled types keep their kind
  })

  it('does not let a profile take over a built-in extension', () => {
    setCustomFileTypes([{ extension: '.pdf', kind: 'code', label: 'x' }])
    expect(describeFile('report.pdf').family).toBe('docs')
  })
})
