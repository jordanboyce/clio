// Real URLs (roadmap U8, work order C1): the open view lives in the hash,
// so Back and refresh work and a link can be shared. #/library opens the
// Library, #/library/map its citation map, #/find Find. The names are the
// ones people see; the app's internal tab ids (research, search, mcp) stay
// as they are, since saved layouts and old ?tab= links use them.

import { resolveTab } from './ribbon'

// Internal tab id → the word in the URL.
const URL_NAMES = { chat: 'ask', search: 'find', research: 'library', mcp: 'connect', generate: 'reports' }
// The Library's views, by their word in the URL.
const LIBRARY_VIEWS = { papers: 'library', map: 'map', reviews: 'reviews', cite: 'write' }
const LIBRARY_URL = Object.fromEntries(Object.entries(LIBRARY_VIEWS).map(([url, view]) => [view, url]))

/** { tab, view } from a location hash such as "#/library/map"; null when it names no view. */
export function parseRoute(hash) {
  const parts = String(hash || '').replace(/^#\/?/, '').split(/[/?]/).filter(Boolean)
  if (!parts.length) return null
  const tab = resolveTab(parts[0])
  if (!tab) return null
  const view = tab === 'research' ? (LIBRARY_VIEWS[parts[1]] || null) : null
  return { tab, view }
}

/** The hash for a tab (and the Library's view): "#/library/map". */
export function routeHash(tab, view = null) {
  const name = URL_NAMES[tab] || tab
  if (tab === 'research' && view && view !== 'library' && LIBRARY_URL[view]) return `#/${name}/${LIBRARY_URL[view]}`
  return `#/${name}`
}
