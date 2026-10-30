import { describe, expect, it } from 'vitest'
import {
  RIBBON_ITEMS, defaultLayout, isActive, moveItem, normalizeLayout, placeBefore, ribbonItems, toggleHidden,
  visibleItems, withDividers,
} from '../ribbon.js'
import { buildTree, folderDocIds, folderSelection, sortFiles, visibleRows } from '../sourceTree.js'

describe('ribbon layout', () => {
  it('shows a short default set, grouped, and hides admin items from non-admins', () => {
    const ids = visibleItems(defaultLayout()).map(i => i.id)
    expect(ids).toEqual(['chat', 'search', 'research', 'generate', 'expertise', 'mcp', 'collections', 'add-sources'])
    expect(ids).not.toContain('admin')
    expect(ids).not.toContain('graph')            // off by default, reachable in Research
    const dividers = withDividers(visibleItems(defaultLayout())).filter(r => r.divider).map(r => r.item.id)
    expect(dividers).toEqual(['research', 'mcp', 'add-sources'])
    expect(visibleItems(defaultLayout(), { admin: true }).map(i => i.id)).toContain('admin')
    expect(visibleItems(defaultLayout(), { chat: false }).map(i => i.id)).not.toContain('chat')
  })

  it('reorders, hides and survives new or unknown items', () => {
    let layout = moveItem(defaultLayout(), 'mcp', -10)
    expect(layout.order[0]).toBe('mcp')
    layout = placeBefore(layout, 'graph', 'mcp')
    expect(layout.order.slice(0, 2)).toEqual(['graph', 'mcp'])
    layout = toggleHidden(layout, 'search')
    expect(visibleItems(layout).map(i => i.id)).not.toContain('search')
    expect(toggleHidden(layout, 'search').hidden).not.toContain('search')

    const old = { order: ['search', 'chat', 'removed-in-a-later-release'], hidden: ['chat'] }
    const fixed = normalizeLayout(old)
    expect(fixed.order.slice(0, 2)).toEqual(['search', 'chat'])
    expect(fixed.order).toHaveLength(RIBBON_ITEMS.length)
    expect(fixed.hidden).toContain('chat')
    expect(fixed.hidden).toContain('reviews')    // new since saved: default visibility
    expect(normalizeLayout('garbage')).toEqual(defaultLayout())
    expect(normalizeLayout({ ...defaultLayout(), labels: 1 }).labels).toBe(true)
    expect(moveItem({ ...defaultLayout(), labels: true }, 'mcp', 1).labels).toBe(true)
  })

  it('knows which item is the current place', () => {
    const graph = RIBBON_ITEMS.find(i => i.id === 'graph')
    expect(isActive(graph, 'research', 'map')).toBe(true)
    expect(isActive(graph, 'research', 'library')).toBe(false)
    expect(isActive(RIBBON_ITEMS[0], 'chat')).toBe(true)
    expect(ribbonItems(defaultLayout()).every(i => i.label)).toBe(true)
  })
})

describe('source tree', () => {
  const roots = [
    { path: '/home/ada/papers', git: false },
    { path: '/data/repos/abc123', git: true, git_url: 'https://github.com/ada/notes.git' },
  ]
  const docs = [
    { document_id: '1', filename: 'b.pdf', source_type: 'upload' },
    { document_id: '2', filename: 'a.pdf', source_type: 'upload', indexed_at: '2026-10-02' },
    { document_id: '3', filename: 'x.pdf', source_type: 'local_reference', source_path: '/home/ada/papers/ecology/x.pdf' },
    { document_id: '4', filename: 'y.pdf', source_type: 'local_reference', source_path: '/home/ada/papers/y.pdf' },
    { document_id: '5', filename: 'README.md', source_type: 'local_reference', source_path: '/data/repos/abc123/README.md' },
    { document_id: '6', filename: 'page.html', source_type: 'url', source_path: 'https://www.example.org/a' },
    { document_id: '7', filename: 'loose.txt', source_type: 'local_reference', source_path: '/srv/inbox/loose.txt' },
  ]

  it('shows a synced file under its own name, unless it was renamed', () => {
    const synced = [
      { document_id: 's1', filename: 'Projects_Clio_plan.pdf', source_type: 'folder_sync', source_path: '/lab/Projects/Clio/plan.pdf' },
      { document_id: 's2', filename: '2021 - Okafor - Roots.pdf', source_type: 'folder_sync', source_path: '/lab/Projects/roots.pdf' },
    ]
    const tree = buildTree(synced, [{ path: '/lab' }])
    const projects = tree.folders[0].folders[0]
    expect(projects.name).toBe('Projects')
    expect(projects.folders[0].files[0].tree_label).toBe('plan.pdf')
    expect(projects.files[0].tree_label).toBe('2021 - Okafor - Roots.pdf')
  })

  it('groups by where sources came from, folders first, then files', () => {
    const tree = buildTree(docs, roots)
    expect(tree.count).toBe(7)
    expect(tree.folders.map(f => f.name)).toEqual(['inbox', 'notes', 'papers', 'Links'])
    expect(tree.files.map(f => f.filename)).toEqual(['a.pdf', 'b.pdf'])
    const papers = tree.folders.find(f => f.name === 'papers')
    expect(papers.folders.map(f => f.name)).toEqual(['ecology'])
    expect(papers.files.map(f => f.filename)).toEqual(['y.pdf'])
    expect(papers.count).toBe(2)
    expect(tree.folders.find(f => f.name === 'notes').group).toBe('git')
    expect(tree.folders.find(f => f.name === 'Links').folders[0].name).toBe('example.org')
  })

  it('renders rows depth-first and hides collapsed folders', () => {
    const tree = buildTree(docs, roots)
    const rows = visibleRows(tree)
    expect(rows.map(r => r.kind === 'folder' ? `+${r.node.name}@${r.depth}` : `${r.doc.filename}@${r.depth}`)).toEqual([
      '+inbox@0', 'loose.txt@1', '+notes@0', 'README.md@1', '+papers@0', '+ecology@1', 'x.pdf@2', 'y.pdf@1',
      '+Links@0', '+example.org@1', 'page.html@2', 'a.pdf@0', 'b.pdf@0',
    ])
    const papersPath = tree.folders.find(f => f.name === 'papers').path
    const collapsed = visibleRows(tree, new Set([papersPath]))
    expect(collapsed.some(r => r.doc?.filename === 'x.pdf')).toBe(false)
    expect(collapsed.find(r => r.node?.name === 'papers').open).toBe(false)
  })

  it('keeps pasted text in its own group, after the folders and before Links', () => {
    const notes = [
      { document_id: 't1', filename: 'Travel policy.html', source_type: 'text', source_path: null },
      { document_id: 't2', filename: 'Agenda.html', source_type: 'text', source_path: null },
    ]
    const tree = buildTree([...docs, ...notes], roots)
    expect(tree.folders.map(f => f.name)).toEqual(['inbox', 'notes', 'papers', 'Pasted text', 'Links'])
    const group = tree.folders.find(f => f.name === 'Pasted text')
    expect(group.group).toBe('notes')
    expect(group.files.map(f => f.filename)).toEqual(['Agenda.html', 'Travel policy.html'])
    expect(tree.count).toBe(9)
  })

  it('selects a folder as its files and sorts by recency on request', () => {
    const tree = buildTree(docs, roots)
    const papers = tree.folders.find(f => f.name === 'papers')
    expect(folderDocIds(papers).sort()).toEqual(['3', '4'])
    expect(folderSelection(papers, new Set(['3']))).toBe('some')
    expect(folderSelection(papers, new Set(['3', '4']))).toBe('all')
    expect(folderSelection(papers, new Set())).toBe('none')
    expect(sortFiles(tree, 'recent').files[0].filename).toBe('a.pdf')
  })
})
