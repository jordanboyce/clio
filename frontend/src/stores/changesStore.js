import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useCollectionStore } from './collectionStore'
import { useUiStore } from './uiStore'

// The Updates feed: what changed in the current collection's watched folders
// and git repositories. Clio has no accounts, so "seen" is this browser's own
// bookmark (the newest event id it has shown), kept per collection.
//
// Polling matches the review bell: once a minute while the tab is visible and
// straight away when the person returns to it. The first load of a collection
// treats its history as already seen, so nobody opens the app to a pile of
// old news; only changes that land after that raise the badge and a toast.
const POLL_MS = 60_000
const seenKey = (collectionId) => `clio_changes_seen:${collectionId}`

function readSeen(collectionId) {
  try {
    const raw = localStorage.getItem(seenKey(collectionId))
    return raw == null ? null : Number(raw) || 0
  } catch { return null }
}

function writeSeen(collectionId, id) {
  try { localStorage.setItem(seenKey(collectionId), String(id)) } catch { /* storage unavailable */ }
}

export const useChangesStore = defineStore('changes', () => {
  const events = ref([])
  const latestId = ref(0)
  const seenId = ref(0)
  const loadedFor = ref('')
  let announcedId = 0
  let timer = null

  const collectionStore = useCollectionStore()
  const ui = useUiStore()

  const unseen = computed(() => events.value.filter(e => e.id > seenId.value))
  const newestFirst = computed(() => [...events.value].reverse())

  async function refresh() {
    const collectionId = collectionStore.currentCollectionId
    if (!collectionId) return
    try {
      const { data } = await http.get('/documents/changes', {
        params: { collection_id: collectionId, since: 0, limit: 20 },
      })
      if (collectionStore.currentCollectionId !== collectionId) return // switched while waiting

      if (loadedFor.value !== collectionId) {
        let stored = readSeen(collectionId)
        if (stored == null) { stored = data.latest_id; writeSeen(collectionId, stored) }
        seenId.value = stored
        announcedId = Math.max(announcedId, stored)
        loadedFor.value = collectionId
      }

      // Tell the person about changes they have not been told about: one
      // toast for one change, a single summary toast for several.
      const fresh = data.events.filter(event => event.id > announcedId)
      announcedId = Math.max(announcedId, data.latest_id)
      if (fresh.length === 1) {
        ui.notify(`${fresh[0].label}: ${fresh[0].summary}`, 'info', { duration: 8000 })
      } else if (fresh.length > 1) {
        ui.notify(`${fresh.length} source updates`, 'info', { duration: 8000 })
      }
      events.value = data.events
      latestId.value = data.latest_id
    } catch {
      // A badge must never nag: keep what we last knew.
    }
  }

  function markSeen() {
    const collectionId = collectionStore.currentCollectionId
    seenId.value = latestId.value
    announcedId = Math.max(announcedId, latestId.value)
    if (collectionId) writeSeen(collectionId, latestId.value)
  }

  function reset() {
    events.value = []
    latestId.value = 0
    seenId.value = 0
    loadedFor.value = ''
    announcedId = 0
  }

  // The collection changed (or just became known): forget the previous one's
  // bookmark state, but do nothing when this collection is already loaded.
  function onCollectionChanged() {
    const current = collectionStore.currentCollectionId
    if (loadedFor.value && loadedFor.value !== current) reset()
    refresh()
  }

  function onVisible() {
    if (document.visibilityState === 'visible') refresh()
  }

  function start() {
    stop()
    refresh()
    timer = setInterval(() => {
      if (document.visibilityState === 'visible') refresh()
    }, POLL_MS)
    document.addEventListener('visibilitychange', onVisible)
  }

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    document.removeEventListener('visibilitychange', onVisible)
  }

  return { events, latestId, seenId, unseen, newestFirst, refresh, markSeen, reset, onCollectionChanged, start, stop }
})
