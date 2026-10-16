import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn() } }))

import http from '../../utils/http'
import { useChangesStore } from '../changesStore'
import { useCollectionStore } from '../collectionStore'
import { useUiStore } from '../uiStore'

const event = (id, extra = {}) => ({
  id, label: 'docs', summary: '1 changed', source: '/docs', added: [], changed: ['a.md'], removed: [],
  added_count: 0, changed_count: 1, removed_count: 0, commits: [], at: '2026-10-16T00:00:00Z', ...extra,
})
const feed = (events) => ({ data: { latest_id: events.length ? events[events.length - 1].id : 0, events } })

let changes, ui, collections

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  http.get.mockReset()
  collections = useCollectionStore()
  collections.currentCollectionId = 'c1'
  ui = useUiStore()
  changes = useChangesStore()
})

describe('changesStore', () => {
  it('treats the history of a collection it has never seen as already read', async () => {
    http.get.mockResolvedValue(feed([event(1), event(2)]))
    await changes.refresh()
    expect(changes.latestId).toBe(2)
    expect(changes.unseen).toHaveLength(0)
    expect(ui.toasts).toHaveLength(0)
    expect(localStorage.getItem('clio_changes_seen:c1')).toBe('2')
  })

  it('raises the badge and a toast only for changes that arrive afterwards', async () => {
    http.get.mockResolvedValue(feed([event(1)]))
    await changes.refresh()

    http.get.mockResolvedValue(feed([event(1), event(2, { label: 'api repo', summary: '3 added' })]))
    await changes.refresh()

    expect(changes.unseen.map((e) => e.id)).toEqual([2])
    expect(ui.toasts.map((t) => t.message)).toEqual(['api repo: 3 added'])

    // The same event is not announced twice.
    await changes.refresh()
    expect(ui.toasts).toHaveLength(1)
  })

  it('marking seen clears the badge and survives a reload', async () => {
    http.get.mockResolvedValue(feed([event(1)]))
    await changes.refresh()
    http.get.mockResolvedValue(feed([event(1), event(2)]))
    await changes.refresh()
    expect(changes.unseen).toHaveLength(1)

    changes.markSeen()
    expect(changes.unseen).toHaveLength(0)
    expect(localStorage.getItem('clio_changes_seen:c1')).toBe('2')

    setActivePinia(createPinia())
    useCollectionStore().currentCollectionId = 'c1'
    const reloaded = useChangesStore()
    http.get.mockResolvedValue(feed([event(1), event(2)]))
    await reloaded.refresh()
    expect(reloaded.unseen).toHaveLength(0)
  })

  it('keeps each collection\'s bookmark separate', async () => {
    http.get.mockResolvedValue(feed([event(1)]))
    await changes.refresh()
    collections.currentCollectionId = 'c2'
    http.get.mockResolvedValue(feed([event(1), event(2), event(3)]))
    changes.onCollectionChanged()
    await vi.waitFor(() => expect(localStorage.getItem('clio_changes_seen:c2')).toBe('3'))
    expect(localStorage.getItem('clio_changes_seen:c2')).toBe('3')
    expect(localStorage.getItem('clio_changes_seen:c1')).toBe('1')
  })

  it('announces several new changes with one toast, and never the same change twice', async () => {
    http.get.mockResolvedValue(feed([event(1)]))
    await changes.refresh()

    http.get.mockResolvedValue(feed([event(1), event(2), event(3), event(4)]))
    await Promise.all([changes.refresh(), changes.refresh()])
    expect(ui.toasts.map((t) => t.message)).toEqual(['3 source updates'])
  })

  it('does not re-announce when the collection id is set again', async () => {
    http.get.mockResolvedValue(feed([event(1)]))
    await changes.refresh()
    localStorage.setItem('clio_changes_seen:c1', '0') // pretend the bookmark is old
    http.get.mockResolvedValue(feed([event(1)]))
    changes.onCollectionChanged()
    await vi.waitFor(() => expect(http.get).toHaveBeenCalledTimes(2))
    expect(ui.toasts).toHaveLength(0)
  })

  it('stays quiet when the feed cannot be fetched', async () => {
    http.get.mockRejectedValue(new Error('offline'))
    await expect(changes.refresh()).resolves.toBeUndefined()
    expect(changes.unseen).toHaveLength(0)
    expect(ui.toasts).toHaveLength(0)
  })
})
