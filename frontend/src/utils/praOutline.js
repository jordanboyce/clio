// State of a fault tree's logic outline: the nested structure GET
// /api/pra/models/{id}/fault-trees/{tree} returns, which nodes are open, and
// the flat list of visible rows the outline draws (one row per node, so a
// tree of 800+ gates is a list, not 800 nested components).
//
// A node is addressed by its path of child positions ("0", "0.2", "0.2.1"):
// a shared gate appears once per place it is used, and each place opens on
// its own. Gates the server did not expand carry `more: true`; the outline
// fetches them on demand and grafts the children in place.

export const ROOT_KEY = '0'

const isGate = (node) => node?.kind === 'gate'

// A gate met again below itself: the server marks those within one reply;
// a grafted subtree can only be checked against the ancestors here.
function cycles(node, ancestors) {
  return Boolean(node.cycle) || (isGate(node) && ancestors.has(node.name))
}

export function canExpand(node, ancestors = new Set()) {
  return isGate(node) && !cycles(node, ancestors) && (Boolean(node.more) || (node.children?.length || 0) > 0)
}

// The visible rows, in reading order.
export function flattenOutline(root, expanded) {
  const rows = []
  if (!root) return rows
  const walk = (node, key, level, parentKey, posinset, setsize, ancestors) => {
    const expandable = canExpand(node, ancestors)
    const open = expandable && expanded.has(key)
    rows.push({
      key, node, level, parentKey, posinset, setsize, expandable,
      expanded: open,
      cycle: isGate(node) && cycles(node, ancestors),
      needsLoad: open && Boolean(node.more) && !node.children,
    })
    if (!open || !node.children) return
    const inner = new Set(ancestors).add(node.name)
    node.children.forEach((child, i) => walk(child, `${key}.${i}`, level + 1, key, i + 1, node.children.length, inner))
  }
  walk(root, ROOT_KEY, 1, null, 1, 1, new Set())
  return rows
}

export function nodeAt(root, key) {
  if (!root || !key) return null
  const parts = key.split('.').map(Number)
  let node = root
  for (const i of parts.slice(1)) {
    node = node?.children?.[i]
    if (!node) return null
  }
  return node
}

// Open the first levels, stopping before the outline grows past maxRows:
// enough to read the tree's shape at a glance without a wall of rows.
export function initialExpansion(root, { maxRows = 120, maxLevel = 3 } = {}) {
  const expanded = new Set()
  if (!root) return expanded
  // A small tree opens whole.
  const all = expandAllLoaded(root, expanded)
  if (flattenOutline(root, all).length <= maxRows) return all
  let visible = 1
  let frontier = [{ node: root, key: ROOT_KEY, ancestors: new Set() }]
  for (let level = 1; level <= maxLevel && frontier.length; level++) {
    const next = []
    for (const { node, key, ancestors } of frontier) {
      if (!canExpand(node, ancestors) || !node.children) continue
      if (visible + node.children.length > maxRows && level > 1) return expanded
      expanded.add(key)
      visible += node.children.length
      const inner = new Set(ancestors).add(node.name)
      node.children.forEach((child, i) => next.push({ node: child, key: `${key}.${i}`, ancestors: inner }))
    }
    frontier = next
  }
  return expanded
}

// Every loaded gate below `key` (the whole tree by default), up to a cap so
// "open all" on a 2,000-node reply stays a list a browser can draw.
export function expandAllLoaded(root, expanded, { key = ROOT_KEY, maxRows = 5000 } = {}) {
  const next = new Set(expanded)
  const start = nodeAt(root, key)
  if (!start) return next
  let rows = 0
  const walk = (node, k, ancestors) => {
    if (rows++ > maxRows || !canExpand(node, ancestors) || !node.children) return
    next.add(k)
    const inner = new Set(ancestors).add(node.name)
    node.children.forEach((child, i) => walk(child, `${k}.${i}`, inner))
  }
  walk(start, key, ancestorNames(root, key))
  return next
}

function ancestorNames(root, key) {
  const names = new Set()
  const parts = key.split('.')
  for (let i = 1; i < parts.length; i++) names.add(nodeAt(root, parts.slice(0, i).join('.'))?.name)
  return names
}

// Put a lazily fetched gate's children in place; the fetched root is the
// gate itself.
export function graft(node, fetched) {
  if (!node || !fetched) return node
  node.children = fetched.children || []
  delete node.more
  delete node.input_count
  if (fetched.description && !node.description) node.description = fetched.description
  return node
}

// The first loaded place a gate or event appears (depth first), and the keys
// to open so it shows.
export function findInOutline(root, name) {
  if (!root || !name) return null
  const walk = (node, key, ancestors) => {
    if (node.name === name) return key
    if (!node.children || cycles(node, ancestors)) return null
    const inner = new Set(ancestors).add(node.name)
    for (let i = 0; i < node.children.length; i++) {
      const hit = walk(node.children[i], `${key}.${i}`, inner)
      if (hit) return hit
    }
    return null
  }
  const key = walk(root, ROOT_KEY, new Set())
  if (!key) return null
  const parts = key.split('.')
  const open = []
  for (let i = 1; i < parts.length; i++) open.push(parts.slice(0, i).join('.'))
  return { key, open }
}

// The WAI-ARIA tree keys. Returns what the outline should do:
// { focus } to move, { expand } / { collapse } to toggle a row, { expandSiblings }
// for "*", { activate } for Enter, or null when the key is not the tree's.
export function outlineKeyAction(rows, focusKey, key) {
  const i = rows.findIndex(r => r.key === focusKey)
  if (i < 0) return rows.length ? { focus: rows[0].key } : null
  const row = rows[i]
  switch (key) {
    case 'ArrowDown': return i + 1 < rows.length ? { focus: rows[i + 1].key } : { focus: row.key }
    case 'ArrowUp': return i > 0 ? { focus: rows[i - 1].key } : { focus: row.key }
    case 'Home': return { focus: rows[0].key }
    case 'End': return { focus: rows[rows.length - 1].key }
    case 'ArrowRight':
      if (row.expandable && !row.expanded) return { expand: row.key }
      if (row.expanded && rows[i + 1]?.parentKey === row.key) return { focus: rows[i + 1].key }
      return { focus: row.key }
    case 'ArrowLeft':
      if (row.expanded) return { collapse: row.key }
      return row.parentKey ? { focus: row.parentKey } : { focus: row.key }
    case '*':
      return { expandSiblings: rows.filter(r => r.parentKey === row.parentKey && r.expandable).map(r => r.key) }
    case 'Enter':
    case ' ':
      return { activate: row.key }
    default:
      return null
  }
}
