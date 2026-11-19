// Real URLs (roadmap U8, work order C1): the open view lives in the hash,
// so Back and refresh work and a link can be shared. #/library opens the
// Library, #/library/map its citation map, #/find Find. The names are the
// ones people see; the app's internal tab ids (research, search, mcp) stay
// as they are, since saved layouts and old ?tab= links use them.
//
// The collection rides along as ?c=<id> (C9), so a shared link opens the
// collection it was copied from, not the recipient's last one. The chat
// itself stays out of the URL while chat history is kept in the browser.

import { resolveTab } from './ribbon'

// Internal tab id → the word in the URL.
const URL_NAMES = { chat: 'ask', search: 'find', research: 'library', mcp: 'connect', generate: 'reports' }
// The Library's views, by their word in the URL.
const LIBRARY_VIEWS = { papers: 'library', map: 'map', reviews: 'reviews', cite: 'write' }
const LIBRARY_URL = Object.fromEntries(Object.entries(LIBRARY_VIEWS).map(([url, view]) => [view, url]))

/** { tab, view, collection? } from a location hash such as
 * "#/library/map?c=team"; null when it names no view. */
export function parseRoute(hash) {
  const [path, query = ''] = String(hash || '').replace(/^#\/?/, '').split('?', 2)
  const parts = path.split('/').filter(Boolean)
  if (!parts.length) return null
  const tab = resolveTab(parts[0])
  if (!tab) return null
  const view = tab === 'research' ? (LIBRARY_VIEWS[parts[1]] || null) : null
  const collection = new URLSearchParams(query).get('c')
  return collection ? { tab, view, collection } : { tab, view }
}

/** The hash for a tab (and the Library's view, and the collection):
 * "#/library/map?c=team". */
export function routeHash(tab, view = null, collection = null) {
  const name = URL_NAMES[tab] || tab
  const where = collection ? `?c=${encodeURIComponent(collection)}` : ''
  if (tab === 'research' && view && view !== 'library' && LIBRARY_URL[view]) return `#/${name}/${LIBRARY_URL[view]}${where}`
  return `#/${name}${where}`
}
