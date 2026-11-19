// Browser state per signed-in person, for a shared workstation (E10).
//
// Provider keys, chat and search history and notes live in localStorage and
// sessionStorage. Under keys with no person in them, the next person to open
// Clio in the same browser profile inherits the last one's. When the server
// says who is signed in (GET /api/me, an identity provider or an
// authenticating proxy), every key this app reads or writes is prefixed with
// a hash of that identity, before anything reads storage: main.js waits for
// prepareStorageScope() before it starts the app.
//
// One place instead of every call site: the Storage methods are wrapped once,
// on the prototype, so the ~100 existing reads and writes keep their names.
// Nothing changes where there is no identity (one person's machine, a shared
// password), so those installs keep what they have.
//
// The person's own values from before this existed are not carried over: on
// a shared profile they may well be someone else's.

import http from './http'

// Read before the app starts (or by the browser itself), so per device.
const UNSCOPED = new Set(['theme', 'clio_storage_migrated'])

let prefix = ''
let installed = false

/** FNV-1a, so an email address is not spelled out in key names. */
export function identityHash(identity) {
  let h = 0x811c9dc5
  for (const ch of String(identity).toLowerCase()) {
    h ^= ch.codePointAt(0)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h.toString(16).padStart(8, '0')
}

export function scopedKey(key) {
  if (!prefix || typeof key !== 'string' || UNSCOPED.has(key) || key.startsWith('u:')) return key
  return prefix + key
}

export function storagePrefix() {
  return prefix
}

function wrap() {
  if (installed || typeof Storage === 'undefined') return
  installed = true
  const proto = Storage.prototype
  const { getItem, setItem, removeItem } = proto
  proto.getItem = function (key) { return getItem.call(this, scopedKey(key)) }
  proto.setItem = function (key, value) { return setItem.call(this, scopedKey(key), value) }
  proto.removeItem = function (key) { return removeItem.call(this, scopedKey(key)) }
}

/** Scope storage to ``identity`` (nothing when there is none). */
export function installStorageScope(identity) {
  prefix = identity ? `u:${identityHash(identity)}:` : ''
  if (prefix) wrap()
}

/** What /api/me said, for the account menu: identity and where to sign out. */
export const signedIn = { identity: null, signOutUrl: '' }

/** Ask the server who is signed in and scope storage to them. Never rejects. */
export async function prepareStorageScope({ timeoutMs = 4000 } = {}) {
  try {
    const { data } = await http.get('/api/me', { timeout: timeoutMs, quiet: true, skipBootstrap: true })
    signedIn.identity = data?.identity || null
    signedIn.signOutUrl = data?.sign_out_url || ''
    installStorageScope(signedIn.identity)
  } catch {
    // Not reachable yet, or nobody signs in here: keys stay as they are.
  }
}

/** Forget everything this app stored in this browser, for every person. */
export function clearThisBrowser() {
  for (const storage of [window.localStorage, window.sessionStorage]) {
    try { storage.clear() } catch { /* storage unavailable */ }
  }
}
