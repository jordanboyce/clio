import axios from 'axios'

// The one axios instance every surface should use.
//
// What it adds over bare axios:
//  - a 30s default timeout, so a hung backend surfaces as an error instead
//    of a spinner forever (pass { timeout: 0 } for uploads and other
//    legitimately long calls);
//  - offline detection: a network-layer failure flips uiStore.offline (the
//    banner in App.vue), and the first response that gets through clears it;
//  - 429 handling: the backend's shared contract (rate limiter and daily
//    token budget both send {"detail", "retry_after_seconds"}) becomes one
//    friendly toast here instead of per-surface handling;
//  - error normalization: catch blocks receive {status, message, isRateLimit,
//    isNetwork} without digging through err.response themselves.
//
// The chat SSE stream uses fetch() and cannot ride this interceptor — it
// handles 429/network in ChatTab.vue directly.

import { useUiStore } from '../stores/uiStore'

export const http = axios.create({ timeout: 30000 })

// Deduplicate the rate-limit toast: a burst of limited requests should not
// stack N identical toasts.
let lastRateLimitToastAt = 0

// Called per-request, never at module evaluation — Pinia isn't active yet
// when this file is first imported.
function uiStore() {
  return useUiStore()
}

http.interceptors.response.use(
  (response) => {
    const ui = uiStore()
    if (ui.offline) ui.offline = false
    return response
  },
  (error) => {
    // A request the caller aborted (search cancel, tab switch) is not an
    // outage: leave the offline flag alone and let the caller ignore it.
    if (axios.isCancel(error) || error?.code === 'ERR_CANCELED') {
      return Promise.reject(error)
    }

    const ui = uiStore()

    // "Backend unreachable" arrives in different costumes depending on the
    // deployment: a direct connection fails at the network layer (no
    // response), cloudflared answers 502/504 for a dead origin, and the
    // Vite dev proxy answers 502. All of them mean the same thing to the
    // user: the server is down, not their request.
    const gatewayDown =
      error.response && [502, 503, 504].includes(error.response.status)
    if (!error.response || gatewayDown) {
      ui.offline = true
      return Promise.reject({
        status: error.response?.status || 0,
        message: 'Cannot reach the server. It may be restarting.',
        isNetwork: true,
        isRateLimit: false,
      })
    }

    if (ui.offline) ui.offline = false
    const { status, data } = error.response
    // `detail` is a string for most errors; the storage cap (413) sends a
    // structured object whose own `detail` carries the sentence.
    const detailText =
      typeof data?.detail === 'string'
        ? data.detail
        : (data?.detail && typeof data.detail === 'object' && typeof data.detail.detail === 'string')
          ? data.detail.detail
          : null

    if (status === 429) {
      const seconds = data?.retry_after_seconds
      const message =
        detailText ||
        `Rate limit reached — try again in ${seconds ?? 'a few'}s.`
      const now = Date.now()
      if (now - lastRateLimitToastAt > 3000) {
        lastRateLimitToastAt = now
        ui.notify(message, 'warning', { duration: 6000 })
      }
      return Promise.reject({
        status,
        message,
        retryAfterSeconds: seconds,
        isNetwork: false,
        isRateLimit: true,
      })
    }

    return Promise.reject({
      status,
      message: detailText || error.response.statusText || 'Request failed',
      isNetwork: false,
      isRateLimit: false,
      isStorageLimit: status === 413 && data?.detail?.error === 'storage_limit_exceeded',
      detail: data?.detail,
    })
  }
)

export default http
