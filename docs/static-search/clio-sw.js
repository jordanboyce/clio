/*! Clio static search: service worker for a site that works offline. MIT License. https://github.com/jordanboyce/clio */
// Installed at the site's root as sw.js by an export made with
// include=offline, and registered by clio-offline.js. Three rules keep it
// from ever showing stale or mismatched content:
//
// 1. manifest.json is always asked of the network first, past every cache
//    (as the components do). Only when there is no connection is the copy
//    kept from the last visit used.
// 2. Every other file the manifest lists is asked for by its checksum
//    (?v=...), and kept under that exact address. A new export names new
//    addresses, so an old manifest can only ever find its own files, and a
//    new one never finds old ones.
// 3. Pages and scripts are asked of the network first and kept for when
//    there is none. When a new manifest arrives, the pages it lists are
//    fetched again by their checksum and kept, so offline the pages match
//    the manifest kept beside them.

const scope = self.registration.scope
const EXPORT = new URL(new URL(self.location.href).searchParams.get('export') || 'search/', scope).href
const MANIFEST = new URL('manifest.json', EXPORT).href
const PAGES = 'clio-pages'     // pages, scripts, the model: by plain address, network first
const FILES = 'clio-files'     // the export's files by checksum: never stale, pruned to the current export
const STATE = 'clio-manifest'  // the manifest last read with a connection

// The site's own pages and scripts. The first visit loads them before this
// worker is in charge, so they are fetched again here to be kept.
const SHELL = ['index.html', 'clio-page.js', 'clio-common.js', 'clio-static-search.js', 'clio-search.js', 'clio-related.js',
  'clio-answers.js', 'clio-cite.js', 'clio-table.js', 'clio-offline.js', 'clio-transcript.js', 'site.webmanifest',
  'icon-192.png',
  'icon-512.png']

self.addEventListener('install', (event) => {
  self.skipWaiting()
  event.waitUntil((async () => {
    const pages = await caches.open(PAGES)
    await Promise.all(SHELL.map(async (name) => {
      try {
        const response = await fetch(new URL(name, scope).href, { cache: 'reload' })
        if (response.ok) await pages.put(new URL(name, scope).href, response)
      } catch { /* a page the site does not have */ }
    }))
    // And the export itself, as the manifest now lists it.
    try {
      const response = await fetch(MANIFEST, { cache: 'no-store' })
      if (response.ok) {
        const text = await response.clone().text()
        await (await caches.open(STATE)).put(MANIFEST, response)
        await keep(JSON.parse(text))
      }
    } catch { /* kept on the next visit with a connection */ }
  })())
})
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET' || request.headers.has('range')) return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin || !url.href.startsWith(scope)) return
  if (url.origin + url.pathname === MANIFEST) {
    event.respondWith(manifest(event))
  } else if (url.searchParams.has('v')) {
    event.respondWith(byChecksum(request))
  } else {
    event.respondWith(networkFirst(request))
  }
})

/** The files a manifest lists, as the addresses the pages ask for them by. */
function listed(data) {
  const files = [...(data.passages?.files || [])]
  if (data.vectors) files.push({ file: data.vectors.file, sha256: data.vectors.sha256 })
  for (const entry of Object.values(data.additions || {})) {
    for (const f of entry.files || []) files.push(f)
  }
  return files.filter((f) => f.sha256).map((f) => ({
    file: f.file,
    versioned: new URL(f.file, EXPORT).href + `?v=${f.sha256.slice(0, 16)}`,
    plain: new URL(f.file, EXPORT).href,
  }))
}

async function manifest(event) {
  try {
    const response = await fetch(MANIFEST, { cache: 'no-store' })
    if (!response.ok) throw new Error(String(response.status))
    const copy = response.clone()
    event.waitUntil((async () => {
      const text = await copy.clone().text()
      await (await caches.open(STATE)).put(MANIFEST, copy)
      await keep(JSON.parse(text))
    })())
    return response
  } catch {
    const kept = await (await caches.open(STATE)).match(MANIFEST)
    return kept || new Response('{"error": "offline, and this site was never opened online"}',
      { status: 503, headers: { 'Content-Type': 'application/json' } })
  }
}

/** Keep everything a manifest lists, by checksum; keep its pages by plain address too; drop the rest. */
async function keep(data) {
  const files = await caches.open(FILES)
  const pages = await caches.open(PAGES)
  const wanted = listed(data)
  for (const f of wanted) {
    let response = await files.match(f.versioned)
    if (!response) {
      try {
        response = await fetch(f.versioned, { cache: 'reload' })
        if (!response.ok) continue
        await files.put(f.versioned, response.clone())
      } catch { continue }
    }
    // A page of this export (a reader page, say) is also asked for by its plain address.
    if (/\.(html|css|xml|txt|md|webmanifest)$/.test(f.file)) await pages.put(f.plain, response.clone())
  }
  const keepSet = new Set(wanted.map((f) => f.versioned))
  for (const request of await files.keys()) {
    if (!keepSet.has(request.url)) await files.delete(request)
  }
}

async function byChecksum(request) {
  const files = await caches.open(FILES)
  const kept = await files.match(request)
  if (kept) return kept
  const response = await fetch(request)
  if (response.ok) await files.put(request, response.clone())
  return response
}

async function networkFirst(request) {
  const pages = await caches.open(PAGES)
  try {
    const response = await fetch(request)
    if (response.ok && response.type === 'basic') await pages.put(request, response.clone())
    return response
  } catch (error) {
    const kept = await pages.match(request, { ignoreSearch: false }) ||
      (request.mode === 'navigate' ? await pages.match(new URL('index.html', scope).href) : null)
    if (kept) return kept
    throw error
  }
}
