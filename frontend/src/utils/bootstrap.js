// The app's startup reads, fetched in one request (GET /api/bootstrap, see
// api/bootstrap.py) and handed out to the stores and panels that ask for them.
//
// Opening the app used to send about 26 requests. Every store still asks for
// what it needs through `http.get`, unchanged; the http client (utils/http.js)
// asks this module first:
//
//  - while the bootstrap is in flight, a GET waits for it (at most WAIT_MS);
//  - a GET whose path and query match a section that came back 2xx, within
//    FRESH_MS of its arrival, is answered from it (a copy, so one caller's
//    edits never reach another's);
//  - anything else, including a section that failed, goes to the network as
//    before, so errors surface exactly where they always did;
//  - any write clears the cache, so nothing read after a change is stale.

export const FRESH_MS = 10000
export const WAIT_MS = 12000

let pending = null
let responses = null
let arrivedAt = 0

/** A request's path and query, parameters sorted, as api/bootstrap.key_for writes it. */
export function canonicalKey(url, params) {
  const u = new URL(url, 'http://clio.invalid')
  const pairs = [...u.searchParams.entries()]
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null) continue
    pairs.push([k, String(v)])
  }
  pairs.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0))
  const query = new URLSearchParams(pairs).toString()
  return query ? `${u.pathname}?${query}` : u.pathname
}

/** Start the bootstrap with `fetcher` (which resolves to its JSON body). */
export function beginBootstrap(fetcher) {
  pending = Promise.resolve()
    .then(fetcher)
    .then((body) => {
      responses = body?.responses || {}
      arrivedAt = Date.now()
    })
    .catch(() => { responses = null })
    .finally(() => { pending = null })
  return pending
}

/** The section answering this GET, or null to send it to the network. */
export async function lookupBootstrap(url, params) {
  if (pending) {
    let timer
    await Promise.race([pending, new Promise((resolve) => { timer = setTimeout(resolve, WAIT_MS) })])
    clearTimeout(timer)
  }
  if (!responses || Date.now() - arrivedAt > FRESH_MS) return null
  const hit = responses[canonicalKey(url, params)]
  if (!hit || hit.status < 200 || hit.status >= 300 || !('body' in hit)) return null
  return { status: hit.status, body: structuredClone(hit.body) }
}

export function clearBootstrap() {
  responses = null
}

/** For tests. */
export function resetBootstrap() {
  pending = null
  responses = null
  arrivedAt = 0
}
