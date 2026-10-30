import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import SourcesSidebar from '../SourcesSidebar.vue'
import { useCollectionStore } from '../../stores/collectionStore'
import { useSelectionStore } from '../../stores/selectionStore'

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
