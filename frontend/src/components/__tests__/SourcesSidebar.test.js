import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import SourcesSidebar from '../SourcesSidebar.vue'
import { useCollectionStore } from '../../stores/collectionStore'
import { useSelectionStore } from '../../stores/selectionStore'
import { useBackgroundJobsStore } from '../../stores/backgroundJobsStore'
import { useLayoutStore } from '../../stores/layoutStore'

// The panel as a whole, mounted the way the app mounts it: this is where a
// setup-order mistake (a watcher reading state declared further down)
// surfaces, which the tree's own unit tests cannot see.

const docs = (n, root = '/lab') => Array.from({ length: n }, (_, i) => ({
  document_id: `d${i}`, filename: `F${i % 7}_doc${i}.pdf`, source_type: 'folder_sync',
  source_path: `${root}/F${i % 7}/doc${i}.pdf`, total_pages: 1, total_chunks: 1,
}))

function serve(all, { folders = [{ path: '/lab' }] } = {}) {
  http.get.mockImplementation(async (url, config = {}) => {
    const params = config.params || {}
    if (url === '/documents') {
      const offset = params.offset || 0
      const limit = params.limit || all.length
      return { data: { documents: all.slice(offset, offset + limit), total_documents: all.length, kind_counts: { docs: all.length } } }
    }
    if (url === '/documents/sync-folders') return { data: { folders } }
    if (url === '/api/expertise/packs') return { data: [] }
    if (url.endsWith('/expertise')) return { data: { packs: [] } }
    return { data: {} }
  })
}

let wrapper
beforeEach(() => {
  localStorage.clear()
  const pinia = createPinia()
  setActivePinia(pinia)
  const collections = useCollectionStore()
  collections.currentCollectionId = 'lab'
  collections.collections = [{ id: 'lab', name: 'Lab', permission: 'owner' }]
})
afterEach(() => { wrapper?.unmount(); wrapper = null; vi.clearAllMocks() })

async function mountPanel() {
  wrapper = mount(SourcesSidebar, { global: { plugins: [] }, attachTo: document.body })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('Deleting sources', () => {
  // A list that answers straight away, with a delete job the server never finishes.
  async function listPanel(all) {
    serve(all, { folders: [] })
    http.post.mockResolvedValue({
      data: { job_id: 41, collection_id: 'lab', status: 'running', total_files: 1, processed_files: 0, job_type: 'delete' },
    })
    vi.stubGlobal('EventSource', class { addEventListener() {} close() {} })
    // Before mounting: the tree loads every page, and the list keeps them.
    useLayoutStore().sourcesView = 'list'
    return mountPanel()
  }
  const row = (w, id) => w.findAll('[aria-label^="Delete source"]')
    .find(b => b.attributes('aria-label').includes(`doc${id}.pdf`)).element.closest('.group')
  const confirm = (w) => w.findAll('.modal-action button').find(b => b.text() === 'Delete').trigger('click')

  afterEach(() => {
    useBackgroundJobsStore().cleanup()
    vi.unstubAllGlobals()
    sessionStorage.clear() // the selection lives there
  })

  it('closes the dialog as soon as the job has started, instead of waiting for it', async () => {
    const w = await listPanel(docs(3))
    const modal = w.get('dialog[aria-labelledby="sidebar-delete-title"]').element
    modal.showModal = vi.fn()
    modal.close = vi.fn()

    await w.get('[aria-label="Delete source F1_doc1.pdf"]').trigger('click')
    expect(modal.showModal).toHaveBeenCalled()
    await confirm(w)
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'lab', document_ids: ['d1'] })
    expect(http.delete).not.toHaveBeenCalled()
    expect(modal.close).toHaveBeenCalled()
  })

  it('marks only the sources on their way out, leaving the others usable', async () => {
    const w = await listPanel(docs(3))
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.close = vi.fn()
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.showModal = vi.fn()

    await w.get('[aria-label="Delete source F1_doc1.pdf"]').trigger('click')
    await confirm(w)
    await flushPromises()

    expect(row(w, 1).dataset.deleting).toBe('true')
    expect(row(w, 0).dataset.deleting).toBeUndefined()
    expect(row(w, 0).querySelector('[aria-label="Delete source F0_doc0.pdf"]').disabled).toBe(false)
  })

  it('takes deleted sources out of the selection at once, so chat stops using them', async () => {
    const w = await listPanel(docs(3))
    useSelectionStore().set(['d0', 'd1'])
    await flushPromises()
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.close = vi.fn()
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.showModal = vi.fn()

    await w.get('[aria-label="Delete 2 selected sources"]').trigger('click')
    await confirm(w)
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'lab', document_ids: ['d0', 'd1'] })
    expect(useSelectionStore().currentIds).toEqual([])
  })

  it('offers all of them when select-all only reached the loaded page', async () => {
    const w = await listPanel(docs(450))
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.close = vi.fn()
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.showModal = vi.fn()

    await w.get('[aria-label="Select all sources"]').setValue(true)
    await w.get('[aria-label^="Delete 200 selected"]').trigger('click')

    expect(w.get('[data-testid="more-than-loaded"]').text()).toContain('This collection has 450')
    await w.get('[data-testid="delete-all-sources"]').trigger('click')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/documents/bulk-delete', { collection_id: 'lab', everything: true })
    expect(useSelectionStore().currentIds).toEqual([])
  })

  it('says what went wrong if the job could not be started, and keeps the sources', async () => {
    const w = await listPanel(docs(2))
    http.post.mockRejectedValue({ message: 'This collection is being re-indexed.' })
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.close = vi.fn()
    w.get('dialog[aria-labelledby="sidebar-delete-title"]').element.showModal = vi.fn()

    await w.get('[aria-label="Delete source F0_doc0.pdf"]').trigger('click')
    await confirm(w)
    await flushPromises()

    expect(w.text()).toContain('This collection is being re-indexed.')
    expect(row(w, 0).dataset.deleting).toBeUndefined()
  })

  it('reloads the list when a delete job stops early, not only when it completes', async () => {
    const w = await listPanel(docs(2))
    const jobs = useBackgroundJobsStore()
    jobs.addUploadJob({ job_id: 5, collection_id: 'lab', status: 'running', total_files: 2, processed_files: 0, job_type: 'delete' })
    await flushPromises()
    http.get.mockClear()
    serve(docs(2), { folders: [] })

    jobs.uploadJobs[0].status = 'cancelled'
    await flushPromises()

    expect(http.get.mock.calls.some(c => c[0] === '/documents')).toBe(true)
  })
})

describe('Sources panel', () => {
  it('mounts and shows the folder tree with the whole collection loaded', async () => {
    const all = docs(1250)
    serve(all)
    const w = await mountPanel()
    expect(w.find('[data-testid="sources-tree"]').exists()).toBe(true)
    // first page of 200, then the rest in steps of 1,000
    const offsets = http.get.mock.calls.filter(c => c[0] === '/documents').map(c => c[1].params.offset)
    expect(offsets).toEqual([0, 200, 1200])
    // a big tree opens one level deep: the synced folder, its sub-folders folded
    const rows = w.findAll('[role="treeitem"]')
    expect(rows[0].text()).toContain('lab')
    expect(rows.length).toBe(1 + 7)
  })

  it('selects every source in a folder, not just the ones on screen', async () => {
    serve(docs(1250))
    const w = await mountPanel()
    const folder = w.findAll('[role="treeitem"]').find(r => r.text().startsWith('F3'))
    await folder.get('input').trigger('change')
    expect(useSelectionStore().currentIds.length).toBe(Math.ceil((1250 - 3) / 7))
  })

  it('keeps the detailed list a click away', async () => {
    serve(docs(5))
    const w = await mountPanel()
    await w.get('[aria-label="Show as a detailed list"]').trigger('click')
    expect(w.find('[data-testid="sources-tree"]').exists()).toBe(false)
    expect(localStorage.getItem('clio_sources_view')).toBe('list')
  })
})

describe('Synced sources', () => {
  const synced = (extra) => ({
    path: '/lab', exists: true, git: true, git_branch: 'main', git_head: 'abcdef0123456789',
    last_synced_at: new Date(Date.now() - 3 * 3600_000).toISOString(), ...extra,
  })

  it('says why a checkout is out of date and highlights Sync', async () => {
    serve(docs(3), { folders: [synced({ out_of_date: true, stale_reason: '2 new commits since the last sync' })] })
    const w = await mountPanel()
    const state = w.get('[data-testid="synced-folders"] [data-testid="source-state"]')
    expect(state.text()).toContain('Out of date: 2 new commits since the last sync')
    expect(state.text()).toContain('synced 3h ago')
    expect(w.get('[data-testid="source-sync"]').classes()).toContain('text-warning')
  })

  it('reads as current, with the last check, when nothing changed', async () => {
    const folder = synced({ watch: true, last_checked_at: new Date(Date.now() - 5 * 60_000).toISOString() })
    serve(docs(3), { folders: [folder] })
    const w = await mountPanel()
    const text = w.get('[data-testid="synced-folders"] [data-testid="source-state"]').text()
    expect(text).toContain('main @ abcdef0')
    expect(text).toContain('Synced 3h ago')
    expect(text).toContain('checked 5m ago')
    expect(text).not.toContain('Out of date')
  })

  it('lets Sync remake a remote clone whose directory is gone', async () => {
    serve(docs(3), { folders: [synced({
      exists: false, git_url: 'https://git.example/team/repo.git', label: 'repo',
      out_of_date: true, stale_reason: 'The clone is gone; Sync makes it again',
    })] })
    const w = await mountPanel()
    const row = w.get('[data-testid="synced-folders"]')
    expect(row.text()).not.toContain('Folder not found')
    expect(row.text()).toContain('The clone is gone')
    const sync = row.get('[data-testid="source-sync"]')
    expect(sync.attributes('disabled')).toBeUndefined()
    expect(sync.attributes('title')).toContain('Fetch the latest commits')
    expect(row.findAll('button').some(b => b.text() === 'Prune')).toBe(false) // a clone always prunes
  })
})

describe('Pasted text', () => {
  // jsdom has no <dialog> API; the reader only needs it to exist.
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal = vi.fn()
    HTMLDialogElement.prototype.close = vi.fn()
  })
  const note = { document_id: 'n1', filename: 'Travel policy.html', source_type: 'text', source_path: null,
    total_pages: 1, total_chunks: 1, indexed_at: '2026-10-30T10:00:00Z' }

  it('posts what was written as a new source and empties the editor', async () => {
    serve(docs(2))
    http.post.mockResolvedValue({ data: { job_id: 9, collection_id: 'lab', status: 'pending', total_files: 1, job_type: 'index' } })
    useBackgroundJobsStore().addUploadJob = vi.fn()
    const w = await mountPanel()

    await w.get('[aria-label="Paste text"]').trigger('click')
    const panel = w.get('[data-testid="text-panel"]')
    await panel.get('input').setValue('Travel policy')
    const box = panel.get('[role="textbox"]')
    box.element.innerHTML = '<h2 style="color:red">Policy</h2><p>Two <b>weeks</b>.</p>'
    await box.trigger('input')
    await panel.get('button.btn-primary').trigger('click')
    await flushPromises()

    expect(http.post).toHaveBeenCalledWith('/documents/text', {
      title: 'Travel policy', html: '<h2>Policy</h2><p>Two <b>weeks</b>.</p>', collection_id: 'lab',
    })
    expect(useBackgroundJobsStore().addUploadJob).toHaveBeenCalledWith(expect.objectContaining({ job_id: 9 }))
    expect(w.find('[data-testid="text-panel"]').exists()).toBe(false)
    expect(w.text()).toContain('1 note(s) queued')
  })

  it('keeps the text when the post fails', async () => {
    serve(docs(2))
    http.post.mockRejectedValue({ message: 'Storage limit reached' })
    const w = await mountPanel()
    await w.get('[aria-label="Paste text"]').trigger('click')
    const panel = w.get('[data-testid="text-panel"]')
    const box = panel.get('[role="textbox"]')
    box.element.innerHTML = '<p>Keep me</p>'
    await box.trigger('input')
    await panel.get('button.btn-primary').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('Storage limit reached')
    expect(w.get('[data-testid="text-panel"] [role="textbox"]').element.innerHTML).toBe('<p>Keep me</p>')
  })

  it('shows a note by its title and reads it in the panel, not as tags in a new tab', async () => {
    const all = [note, ...docs(1)]
    serve(all)
    http.get.mockImplementation(async (url, config = {}) => {
      if (url === '/documents/n1/pdf') {
        expect(config.responseType).toBe('text')
        return { data: '<!DOCTYPE html><html><head><title>Travel policy</title></head><body><h1>Policy 12</h1><p>Two <b>weeks</b>.</p><script>x()</script></body></html>' }
      }
      if (url === '/documents') return { data: { documents: all, total_documents: all.length, kind_counts: { docs: all.length } } }
      if (url === '/documents/sync-folders') return { data: { folders: [{ path: '/lab' }] } }
      if (url === '/api/expertise/packs') return { data: [] }
      if (url.endsWith('/expertise')) return { data: { packs: [] } }
      return { data: {} }
    })
    const w = await mountPanel()
    await w.get('[aria-label="Show as a detailed list"]').trigger('click')
    const row = w.findAll('.group').find(r => r.text().includes('Travel policy'))
    expect(row.text()).toContain('Travel policy')
    expect(row.text()).not.toContain('.html')
    expect(row.text()).toContain('pasted')
    expect(row.find('a[target="_blank"]').exists()).toBe(false)

    await row.get('[aria-label="Read Travel policy"]').trigger('click')
    await flushPromises()
    const body = w.get('[data-testid="note-body"]').element.innerHTML
    expect(body).toContain('<h1>Policy 12</h1><p>Two <b>weeks</b>.</p>')
    expect(body).not.toContain('script')
    expect(w.get('#sidebar-note-title').text()).toBe('Travel policy')
  })

  it('groups notes in the tree under "Pasted text"', async () => {
    serve([note, { ...note, document_id: 'n2', filename: 'Travel policy_1.html' }, ...docs(3)])
    const w = await mountPanel()
    const rows = w.findAll('[role="treeitem"]')
    expect(rows.map(r => r.text())).toEqual(expect.arrayContaining([expect.stringContaining('Pasted text')]))
  })
})
