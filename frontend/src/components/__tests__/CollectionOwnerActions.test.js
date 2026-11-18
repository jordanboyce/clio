import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import CollectionOwnerActions from '../CollectionOwnerActions.vue'
import CoverageReportPanel from '../CoverageReportPanel.vue'
import { useCollectionStore } from '../../stores/collectionStore'

// Owner-tier actions on a collection (export, edit) follow `permission` and
// `team` from GET /api/collections, the same rule the server enforces in
// api/deps.require_collection_access. Admins on team collections are
// 'owner' there; everyone else is 'readwrite'.

const TEAM = { id: 'team', name: 'Programme X', team: true }
const MINE = { id: 'mine', name: 'My notes' }

beforeEach(() => {
  setActivePinia(createPinia())
})

function mountFor(collection) {
  return mount(CollectionOwnerActions, { props: { collection } })
}

describe('collectionStore.configureBlockedReason', () => {
  it.each([
    [{ ...TEAM, permission: 'owner' }, null],
    [{ ...MINE, permission: 'owner' }, null],
    [{ ...MINE }, null], // no field: an open install, everyone owns everything
    [{ ...TEAM, permission: 'readwrite' }, /team collection: only an admin/],
    [{ ...MINE, permission: 'readwrite' }, /Only the collection's owner/],
    [{ ...MINE, permission: 'read' }, /read-only/],
  ])('%o → %s', (collection, expected) => {
    const store = useCollectionStore()
    const reason = store.configureBlockedReason(collection)
    if (expected === null) expect(reason).toBeNull()
    else expect(reason).toMatch(expected)
  })

  it('follows the current collection', () => {
    const store = useCollectionStore()
    store.collections = [{ ...TEAM, permission: 'readwrite' }]
    store.currentCollectionId = 'team'
    expect(store.currentConfigureBlockedReason).toMatch(/only an admin/)
  })
})

describe('CollectionOwnerActions', () => {
  it('gives an admin of a team collection working Export and Edit', async () => {
    const w = mountFor({ ...TEAM, permission: 'owner' })

    await w.get('[data-testid="collection-export"]').trigger('click')
    await w.get('[data-testid="collection-edit"]').trigger('click')

    expect(w.emitted('export')[0][0].id).toBe('team')
    expect(w.emitted('edit')[0][0].id).toBe('team')
    expect(w.find('[data-testid="collection-export-blocked"]').exists()).toBe(false)
  })

  it('gives the owner of a private collection working Export and Edit', () => {
    const w = mountFor({ ...MINE, permission: 'owner' })
    expect(w.find('[data-testid="collection-export"]').exists()).toBe(true)
    expect(w.find('[data-testid="collection-edit"]').exists()).toBe(true)
  })

  it('shows a member of a team collection both actions disabled, saying an admin decides', async () => {
    const w = mountFor({ ...TEAM, permission: 'readwrite' })

    expect(w.find('[data-testid="collection-export"]').exists()).toBe(false)
    expect(w.find('[data-testid="collection-edit"]').exists()).toBe(false)
    for (const id of ['export', 'edit']) {
      const blocked = w.get(`[data-testid="collection-${id}-blocked"]`)
      expect(blocked.attributes('title')).toMatch(/team collection: only an admin/)
      expect(blocked.attributes('aria-label')).toMatch(/only an admin/)
      expect(blocked.get('button').attributes('disabled')).toBeDefined()
      await blocked.trigger('click')
    }
    expect(w.emitted('export')).toBeUndefined()
    expect(w.emitted('edit')).toBeUndefined()
  })

  it("tells a read-write collaborator it is the owner's call", () => {
    const w = mountFor({ ...MINE, permission: 'readwrite' })
    expect(w.get('[data-testid="collection-edit-blocked"]').attributes('title')).toMatch(/Only the collection's owner/)
  })

  it('shows a reader nothing at all', () => {
    const w = mountFor({ ...MINE, permission: 'read' })
    expect(w.html()).not.toMatch(/button/)
  })
})

describe('CoverageReportPanel for someone who cannot run it', () => {
  it('names who can', async () => {
    http.get.mockResolvedValueOnce({ data: { report: null, run: { state: 'idle' }, questions: [], llm_questions: false } })
    const w = mount(CoverageReportPanel, { props: { collectionId: 'team', canRun: false, whoCanRun: 'An admin', pollMs: 10 } })
    await flushPromises()

    expect(w.get('[data-testid="coverage-empty"]').text()).toContain('An admin can run one.')
    expect(w.find('[data-testid="coverage-run"]').exists()).toBe(false)
  })
})
