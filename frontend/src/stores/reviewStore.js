import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useUserStore } from './userStore'

// What is waiting for an administrator: held and flagged documents, prompt-
// injection warnings worth a look, user reports (counted server-side), plus
// registration requests, which the user store already tracks. The bell in the
// header and the footer indicator both read from here.
//
// Polling is deliberately lazy: once a minute while the tab is visible, and
// straight away when the person comes back to it. The server caches the
// counts for a few seconds, so this is cheap.
const POLL_MS = 60_000

export const useReviewStore = defineStore('review', () => {
  const summary = ref({ pending: 0, critical: 0, held: 0, flagged: 0, injection: 0, reports: 0 })
  const loaded = ref(false)
  const failed = ref(false)
  let timer = null

  const userStore = useUserStore()

  const registrations = computed(() => userStore.pendingRegistrations || 0)
  const total = computed(() => (summary.value.pending || 0) + registrations.value)
  const urgent = computed(() => (summary.value.critical || 0) > 0 || (summary.value.held || 0) > 0)

  async function refresh() {
    if (!userStore.adminConsole) return
    try {
      const resp = await http.get('/api/admin/review/summary')
      summary.value = { ...summary.value, ...resp.data }
      failed.value = false
    } catch {
      // A badge must never nag: stay quiet, keep the last known counts.
      failed.value = true
    } finally {
      loaded.value = true
    }
  }

  // The admin tab loads the full queue itself; let it hand the counts over so
  // the badge changes the moment an item is resolved, not a minute later.
  function setSummary(next) {
    if (next) summary.value = { ...summary.value, ...next }
    loaded.value = true
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

  return { summary, loaded, failed, registrations, total, urgent, refresh, setSummary, start, stop }
})
