import { ref } from 'vue'
import { defineStore } from 'pinia'

// Global UI feedback: the toast stack and the backend-unreachable flag.
//
// Before this store existed, ~15 failure paths (collection CRUD, share
// create/revoke, job cancel, stats) logged to the console and showed the
// user nothing. Every surface now reports through here; the Toaster
// component in App.vue renders the stack. Deliberately flat API — a
// message string and a type cover every call site we have.
export const useUiStore = defineStore('ui', () => {
  const toasts = ref([])
  // True while requests are failing at the network layer (server down or
  // unreachable). Cleared by the first response that makes it through.
  const offline = ref(false)
  // Onboarding hand-off: pulses the Add Sources control after provider
  // setup completes with an empty collection; cleared on first interaction.
  const highlightAddSources = ref(false)
  // Bumped by any surface that added or removed sources outside the Sources
  // panel (the sample set, for one); the panel reloads its list on change.
  const sourcesVersion = ref(0)
  function sourcesChanged() {
    sourcesVersion.value++
  }

  let nextId = 1

  function notify(message, type = 'info', { duration = 4000 } = {}) {
    const id = nextId++
    toasts.value.push({ id, message, type })
    if (duration > 0) {
      setTimeout(() => dismiss(id), duration)
    }
    return id
  }

  function dismiss(id) {
    const i = toasts.value.findIndex((t) => t.id === id)
    if (i !== -1) toasts.value.splice(i, 1)
  }

  // Normalized-error helper: accepts anything a catch block receives —
  // the interceptor's {status, message}, an axios error, or a plain Error.
  function toastError(err, fallback = 'Something went wrong') {
    const message =
      err?.message || err?.response?.data?.detail || String(err ?? fallback)
    // Errors linger a little longer than info toasts.
    return notify(message, 'error', { duration: 6000 })
  }

  return { toasts, offline, highlightAddSources, sourcesVersion, sourcesChanged, notify, dismiss, toastError }
})
