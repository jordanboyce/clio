import axios from 'axios'

// The one axios instance every surface should use.
//
// What it adds over bare axios:
//  - a 30s default timeout, so a hung backend surfaces as an error instead
//    of a spinner forever (pass { timeout: 0 } for uploads and other
//    legitimately long calls);
//  - offline detection: a network-layer failure flips uiStore.offline (the
//    banner in App.vue), and the first response that gets through clears it;
//    a client-side timeout does not (it comes back as isTimeout instead);
//  - 429 handling: the backend's shared contract (rate limiter and daily
//    token budget both send {"detail", "retry_after_seconds"}) becomes one
//    friendly toast here instead of per-surface handling;
//  - error normalization: catch blocks receive {status, message, isRateLimit,
//    isNetwork, isTimeout} without digging through err.response themselves.
//
// The chat SSE stream uses fetch() and cannot ride this interceptor — it
// handles 429/network in ChatTab.vue directly.

import { useUiStore } from '../stores/uiStore'
import { beginBootstrap, clearBootstrap, lookupBootstrap } from './bootstrap'

export const http = axios.create({ timeout: 30000 })

// Startup reads come from one GET /api/bootstrap when they can (utils/bootstrap.js).
// The adapter is the last step before the network, so interceptors, timeouts
// and error handling all behave as for a real response.
const networkAdapter = axios.getAdapter(axios.defaults.adapter)
http.defaults.adapter = async (config) => {
  if ((config.method || 'get').toLowerCase() !== 'get') {
    clearBootstrap()
    return networkAdapter(config)
  }
  if (!config.skipBootstrap) {
    const hit = await lookupBootstrap(config.url, config.params)
    if (hit) {
      return { data: hit.body, status: hit.status, statusText: 'OK', headers: {}, config, request: null }
    }
  }
  return networkAdapter(config)
}

/** Fetch the startup reads for the collection the app opens on. Call once, before mounting. */
export function startBootstrap(collectionId) {
  return beginBootstrap(() => http
    .get('/api/bootstrap', {
      params: collectionId ? { collection_id: collectionId } : {},
      skipBootstrap: true,
      quiet: true,
    })
    .then((r) => r.data))
}

// D3: FastAPI's 422 answers with a list of {loc, msg}; say "field: message"
// for each instead of "Unprocessable Entity".
export function validationMessage(errors) {
  const parts = (errors || []).slice(0, 4).map((e) => {
    const loc = Array.isArray(e?.loc) ? e.loc.filter(p => !['body', 'query', 'path', 'header'].includes(p)) : []
    const field = loc.length ? String(loc[loc.length - 1]).replaceAll('_', ' ') : ''
    const msg = String(e?.msg || 'is not valid').replace(/^Value error, /, '')
    return field ? `${field}: ${msg}` : msg
  })
  return parts.join('; ') || 'Some of what was sent is not valid.'
}

// A response Clio wrote itself (its error envelope), as against a proxy's
// or a gateway's own error page.
function fromClio(response) {
  const data = response?.data
  return Boolean(data && typeof data === 'object' && ('detail' in data || 'code' in data))
}

function fallbackMessage(status, statusText) {
  if (status === 502 || status === 504) return 'A service this needed did not answer. Retrying is safe.'
  if (status === 503) return 'The server is busy or still starting. Retrying is safe.'
  return statusText || 'Request failed'
}

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

    // A request that ran past its client-side timeout has no response either,
    // but the server answered nothing about itself: it is usually busy on this
    // one request (a slow model writing starter questions). Calling that an
    // outage put up the offline banner over a healthy backend, so it leaves
    // the flag as it was and says what actually happened.
    if (error?.code === 'ECONNABORTED' || error?.code === 'ETIMEDOUT') {
      return Promise.reject({
        status: 0,
        message: 'The server took too long to answer this request.',
        isNetwork: false,
        isTimeout: true,
        isRateLimit: false,
      })
    }

    // "Backend unreachable" arrives in different costumes depending on the
    // deployment: a direct connection fails at the network layer (no
    // response), cloudflared answers 502/504 for a dead origin, and the
    // Vite dev proxy answers 502. All of them mean the same thing to the
    // user: the server is down, not their request. D3: a 502/503/504 that
    // Clio itself wrote (its JSON envelope: a model provider that did not
    // answer, the search model still loading) is not an outage, and keeps
    // its own sentence.
    const gatewayDown =
      error.response && [502, 503, 504].includes(error.response.status) && !fromClio(error.response)
    if (!error.response || gatewayDown) {
      ui.offline = true
      return Promise.reject({
        status: error.response?.status || 0,
        message: error.response
          ? 'The server did not answer (it may be restarting). Retrying is safe.'
          : 'Cannot reach the server. It may be restarting.',
        isNetwork: true,
        isRateLimit: false,
      })
    }

    // D2: signed out (an expired session, a proxy that dropped the sign-in).
    // One prompt for the whole app, and the pollers stop asking.
    if (error.response.status === 401) {
      ui.authRequired = true
      return Promise.reject({
        status: 401,
        message: 'You are signed out. Sign in again to continue.',
        isNetwork: false,
        isRateLimit: false,
        isAuth: true,
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
          : Array.isArray(data?.detail)
            ? validationMessage(data.detail)
            : null

    if (status === 429) {
      const seconds = data?.retry_after_seconds
      const message =
        detailText ||
        `Rate limit reached — try again in ${seconds ?? 'a few'}s.`
      const now = Date.now()
      // `quiet: true` on a request marks it as background (suggestions,
      // status reads): its caller degrades on its own, so a toast would only
      // alarm someone who did nothing.
      if (!error.config?.quiet && now - lastRateLimitToastAt > 3000) {
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
      message: detailText || fallbackMessage(status, error.response.statusText),
      isNetwork: false,
      isRateLimit: false,
      isStorageLimit: status === 413 && data?.detail?.error === 'storage_limit_exceeded',
      detail: data?.detail,
    })
  }
)

export default http
