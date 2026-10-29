// The ribbon: the column of icons down the left edge, as in Obsidian.
//
// Every destination and the everyday actions sit here, one click away.
// Which items show, and in what order, is the reader's choice (right-click
// the ribbon → Customize). The layout is a per-browser preference, so it
// lives in localStorage and survives updates: items added in a later
// release are appended to a saved layout rather than lost.

// Grouped by purpose — asking, working with sources, sharing them, quick
// actions — and the ribbon draws a quiet divider where the group changes, so
// Ask and Find read as the primary pair rather than two icons among twelve.
// `hidden` marks the ones off by default: each is reachable elsewhere (the
// Research view's own tabs, the command palette), so the default ribbon
// stays short and anyone who wants them can switch them on.
export const RIBBON_ITEMS = [
  { id: 'chat', label: 'Ask', tab: 'chat', group: 'ask' },
  { id: 'search', label: 'Find', tab: 'search', group: 'ask' },
  { id: 'research', label: 'Research', tab: 'research', view: 'library', group: 'work' },
  { id: 'graph', label: 'Citation graph', tab: 'research', view: 'map', group: 'work', hidden: true },
  { id: 'reviews', label: 'Reviews', tab: 'research', view: 'reviews', group: 'work', hidden: true },
  { id: 'generate', label: 'Reports', tab: 'generate', group: 'work' },
  { id: 'expertise', label: 'Saved instructions', tab: 'expertise', group: 'work' },
  { id: 'mcp', label: 'Connect', tab: 'mcp', group: 'share' },
  { id: 'collections', label: 'Collections', tab: 'collections', group: 'share' },
  { id: 'admin', label: 'Administration', tab: 'admin', adminOnly: true, group: 'share' },
  { id: 'add-sources', label: 'Add sources', action: 'add-sources', group: 'actions' },
  { id: 'new-chat', label: 'New chat', action: 'new-chat', group: 'actions', hidden: true },
  { id: 'notes', label: 'Notes and tools', action: 'notes', group: 'actions', hidden: true },
  { id: 'palette', label: 'Command palette', action: 'palette', group: 'actions', hidden: true },
]

const STORAGE_KEY = 'clio_ribbon'
const KNOWN = new Set(RIBBON_ITEMS.map(i => i.id))

export function defaultLayout() {
  return {
    order: RIBBON_ITEMS.map(i => i.id),
    hidden: RIBBON_ITEMS.filter(i => i.hidden).map(i => i.id),
    // Icons alone suit people who know them; names beside the icons suit
    // everyone else. A preference, not a mode of the app.
    labels: false,
  }
}

/** A saved layout made valid against today's items. */
export function normalizeLayout(saved) {
  const base = defaultLayout()
  if (!saved || !Array.isArray(saved.order)) return base
  const order = saved.order.filter(id => KNOWN.has(id))
  for (const id of base.order) if (!order.includes(id)) order.push(id)
  const hidden = Array.isArray(saved.hidden) ? saved.hidden.filter(id => KNOWN.has(id)) : base.hidden
  // An item new since the layout was saved arrives with its default visibility.
  const savedIds = new Set(saved.order)
  for (const id of base.hidden) if (!savedIds.has(id) && !hidden.includes(id)) hidden.push(id)
  return { order: [...new Set(order)], hidden: [...new Set(hidden)], labels: Boolean(saved.labels) }
}

export function loadLayout() {
  try {
    return normalizeLayout(JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'))
  } catch {
    return defaultLayout()
  }
}

export function saveLayout(layout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(normalizeLayout(layout)))
  } catch { /* a preference, not data */ }
}

/** Items in layout order, with the ones this person cannot use removed. */
export function ribbonItems(layout, { admin = false, chat = true } = {}) {
  const byId = Object.fromEntries(RIBBON_ITEMS.map(i => [i.id, i]))
  return normalizeLayout(layout).order
    .map(id => byId[id])
    .filter(item => (!item.adminOnly || admin) && (chat || (item.tab !== 'chat' && item.action !== 'new-chat')))
}

export function visibleItems(layout, options) {
  const hidden = new Set(normalizeLayout(layout).hidden)
  return ribbonItems(layout, options).filter(item => !hidden.has(item.id))
}

export function moveItem(layout, id, delta) {
  const next = normalizeLayout(layout)
  const from = next.order.indexOf(id)
  const to = Math.max(0, Math.min(next.order.length - 1, from + delta))
  if (from < 0 || from === to) return next
  next.order.splice(to, 0, next.order.splice(from, 1)[0])
  return next
}

export function placeBefore(layout, id, beforeId) {
  const next = normalizeLayout(layout)
  const from = next.order.indexOf(id)
  if (from < 0 || id === beforeId) return next
  next.order.splice(from, 1)
  const to = beforeId ? next.order.indexOf(beforeId) : -1
  next.order.splice(to < 0 ? next.order.length : to, 0, id)
  return next
}

export function toggleHidden(layout, id) {
  const next = normalizeLayout(layout)
  next.hidden = next.hidden.includes(id) ? next.hidden.filter(x => x !== id) : [...next.hidden, id]
  return next
}

/** Is this item the place the app is showing now? */
/** Where a divider belongs: before an item whose group differs from the last. */
export function withDividers(items) {
  return items.map((item, i) => ({ item, divider: i > 0 && items[i - 1].group !== item.group }))
}

export function isActive(item, activeTab, researchView) {
  if (!item.tab || item.tab !== activeTab) return false
  return !item.view || item.view === researchView
}
