// The Sources panel as a tree, the way Obsidian's and VS Code's file
// explorers show a vault: folders first, then files, by name.
//
// Clio's sources come from three places, and the tree follows them:
//   * a synced folder or git repository — its own folder, keeping the
//     sub-folders each file sits in (from the file's source path), each file
//     under its own name rather than the flattened one it is stored as;
//   * a link — under "Links", one folder per site;
//   * an upload — at the top level, like a file dropped in a vault's root.
// A file indexed in place from a folder that is not a remembered sync root
// is grouped under its parent folder.

const SEP = /[\\/]+/

function splitPath(path) {
  return String(path || '').split(SEP).filter(Boolean)
}

function rootName(root) {
  if (root.git_url) {
    const name = String(root.git_url).replace(/\.git$/, '').split(/[/:]/).filter(Boolean).pop()
    if (name) return name
  }
  return splitPath(root.path).pop() || root.path
}

function host(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return 'other'
  }
}

// Indexed from a folder on disk (in place, or copied by a folder sync).
function isFilePath(doc) {
  return Boolean(doc.source_path) && doc.source_type !== 'url' && doc.source_type !== 'upload' &&
    !/^[a-z]+:\/\//i.test(doc.source_path)
}

// A synced file is stored under its path flattened into one name
// ("Projects_Clio_paper.pdf"); in a tree that already shows the folders, the
// file's own name reads better. A name someone gave it (a rename) is kept.
function treeLabel(doc, relative) {
  const own = relative[relative.length - 1]
  const flattened = relative.join('_')
  return !doc.filename || doc.filename === flattened || doc.filename === own ? own : doc.filename
}

function folder(name, path, extra = {}) {
  return { type: 'folder', name, path, folders: new Map(), files: [], ...extra }
}

function ensure(parent, name, path, extra) {
  if (!parent.folders.has(name)) parent.folders.set(name, folder(name, path, extra))
  return parent.folders.get(name)
}

/**
 * Build the tree. ``roots`` are the collection's remembered sync folders
 * (``{path, git, git_url}``); without them, files indexed in place are
 * grouped by their parent folder.
 */
export function buildTree(documents, roots = []) {
  const top = folder('', '')
  const sortedRoots = [...roots]
    .filter(r => r?.path)
    .sort((a, b) => splitPath(b.path).length - splitPath(a.path).length)

  for (const doc of documents || []) {
    if (doc.source_type === 'url' && /^https?:\/\//i.test(doc.source_path || '')) {
      const links = ensure(top, 'Links', 'links:', { group: 'links' })
      ensure(links, host(doc.source_path), `links:${host(doc.source_path)}`, { group: 'site' }).files.push(doc)
      continue
    }
    if (isFilePath(doc)) {
      const parts = splitPath(doc.source_path)
      const root = sortedRoots.find(r => {
        const rp = splitPath(r.path)
        return rp.length < parts.length && rp.every((seg, i) => seg === parts[i])
      })
      let node
      let rest
      if (root) {
        node = ensure(top, rootName(root), `root:${root.path}`, { group: root.git ? 'git' : 'folder' })
        rest = parts.slice(splitPath(root.path).length, -1)
      } else {
        const parent = parts.length > 1 ? parts[parts.length - 2] : 'Folder'
        node = ensure(top, parent, `dir:${parts.slice(0, -1).join('/')}`, { group: 'folder' })
        rest = []
      }
      let path = node.path
      for (const seg of rest) {
        path = `${path}/${seg}`
        node = ensure(node, seg, path)
      }
      const relative = root ? parts.slice(splitPath(root.path).length) : parts.slice(-1)
      node.files.push({ ...doc, tree_label: treeLabel(doc, relative) })
      continue
    }
    top.files.push(doc)
  }
  return finish(top)
}

function byName(a, b) {
  return String(a.name ?? a.tree_label ?? a.filename).localeCompare(String(b.name ?? b.tree_label ?? b.filename), undefined,
    { numeric: true, sensitivity: 'base' })
}

function finish(node) {
  const folders = [...node.folders.values()].map(finish).sort((a, b) => {
    // "Links" sits after the folders a person made.
    if ((a.group === 'links') !== (b.group === 'links')) return a.group === 'links' ? 1 : -1
    return byName(a, b)
  })
  const files = [...node.files].sort(byName)
  const count = files.length + folders.reduce((n, f) => n + f.count, 0)
  return { type: 'folder', name: node.name, path: node.path, group: node.group, folders, files, count }
}

export function sortFiles(tree, by = 'name') {
  if (by === 'name') return tree
  const recent = (a, b) => String(b.indexed_at || '').localeCompare(String(a.indexed_at || ''))
  const walk = (node) => ({ ...node, folders: node.folders.map(walk), files: [...node.files].sort(recent) })
  return walk(tree)
}

/** Every document id under a folder. */
export function folderDocIds(node) {
  return [...node.files.map(f => f.document_id), ...node.folders.flatMap(folderDocIds)]
}

export function folderPaths(node) {
  return [node.path, ...node.folders.flatMap(folderPaths)].filter(Boolean)
}

/**
 * Rows to render, depth-first, skipping the inside of collapsed folders.
 * Folders are open unless their path is in ``collapsed``.
 */
export function visibleRows(tree, collapsed = new Set()) {
  const rows = []
  const walk = (node, depth) => {
    for (const child of node.folders) {
      const open = !collapsed.has(child.path)
      rows.push({ kind: 'folder', node: child, depth, open, key: `f:${child.path}` })
      if (open) walk(child, depth + 1)
    }
    for (const doc of node.files) rows.push({ kind: 'file', doc, depth, key: `d:${doc.document_id}` })
  }
  walk(tree, 0)
  return rows
}

/** ``all`` | ``some`` | ``none`` of a folder's documents in the selection. */
export function folderSelection(node, selected) {
  const ids = folderDocIds(node)
  if (!ids.length) return 'none'
  const n = ids.filter(id => selected.has(id)).length
  return n === 0 ? 'none' : n === ids.length ? 'all' : 'some'
}
