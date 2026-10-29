import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppRibbon from '../AppRibbon.vue'
import SourcesTree from '../SourcesTree.vue'
import { defaultLayout, visibleItems } from '../../utils/ribbon.js'
import { buildTree } from '../../utils/sourceTree.js'

describe('Ribbon', () => {
  const mountRibbon = (props = {}) => mount(AppRibbon, {
    props: { items: visibleItems(defaultLayout()), layout: defaultLayout(), activeTab: 'research', researchView: 'map', ...props },
    attachTo: document.body,
  })

  it('shows every visible item with the current place marked', async () => {
    const wrapper = mountRibbon()
    const labels = wrapper.findAll('[data-ribbon-item]').map(b => b.attributes('aria-label'))
    expect(labels.slice(0, 4)).toEqual(['Ask', 'Find', 'Research', 'Reports'])
    expect(wrapper.findAll('.ribbon-divider').length).toBeGreaterThanOrEqual(4)
    expect(wrapper.get('[data-ribbon-item="research"]').attributes('aria-current')).toBeUndefined()   // map open
    expect(wrapper.find('.has-label').exists()).toBe(false)
    await wrapper.get('[data-ribbon-item="mcp"]').trigger('click')
    expect(wrapper.emitted('action')[0][0].tab).toBe('mcp')
    wrapper.unmount()
  })

  it('shows names beside the icons when asked', () => {
    const wrapper = mountRibbon({ layout: { ...defaultLayout(), labels: true }, researchView: 'library' })
    expect(wrapper.get('[data-ribbon-item="research"]').text()).toBe('Research')
    expect(wrapper.get('[data-ribbon-item="research"]').attributes('aria-current')).toBe('page')
    expect(wrapper.get('[data-ribbon-item="research"]').attributes('data-tip')).toBeUndefined()
    wrapper.unmount()
  })

  it('hides an item from its right-click menu', async () => {
    const wrapper = mountRibbon()
    await wrapper.get('[data-ribbon-item="search"]').trigger('contextmenu', { clientX: 10, clientY: 10 })
    const hide = wrapper.findAll('[role="menuitem"]').find(b => b.text().startsWith('Hide'))
    await hide.trigger('click')
    expect(wrapper.emitted('update:layout')[0][0].hidden).toContain('search')
    wrapper.unmount()
  })
})

describe('Sources tree', () => {
  const docs = [
    { document_id: '1', filename: 'notes.pdf', source_type: 'upload' },
    { document_id: '2', filename: 'x.pdf', source_type: 'local_reference', source_path: '/home/ada/papers/x.pdf' },
    { document_id: '3', filename: 'y.pdf', source_type: 'local_reference', source_path: '/home/ada/papers/y.pdf' },
  ]
  const tree = buildTree(docs, [{ path: '/home/ada/papers' }])
  const mountTree = (props = {}) => mount(SourcesTree, {
    props: { tree, collapsed: new Set(), selectedIds: [], ...props },
    attachTo: document.body,
  })

  it('draws folders then files, and clicking a file selects it', async () => {
    const wrapper = mountTree()
    const rows = wrapper.findAll('[role="treeitem"]')
    expect(rows.map(r => r.text().trim())).toEqual(['papers2', 'xpdf', 'ypdf', 'notespdf'])
    await rows[1].trigger('click')
    expect(wrapper.emitted('toggle')[0]).toEqual(['2'])
    await rows[0].trigger('click')
    expect(wrapper.emitted('toggle-folder')[0]).toEqual([tree.folders[0].path])
    wrapper.unmount()
  })

  it('selects a whole folder and offers file actions on right-click', async () => {
    const wrapper = mountTree({ selectedIds: ['2'] })
    const folderCheck = wrapper.findAll('[role="treeitem"]')[0].get('input')
    expect(folderCheck.element.indeterminate).toBe(true)
    await folderCheck.trigger('change')
    expect(wrapper.emitted('select')[0]).toEqual([['2', '3'], true])
    await wrapper.findAll('[role="treeitem"]')[2].trigger('contextmenu', { clientX: 5, clientY: 5 })
    const items = wrapper.findAll('[role="menuitem"]').map(i => i.text())
    expect(items).toContain('View indexed text')
    expect(items).not.toContain('Delete')            // read-only here
    await wrapper.findAll('[role="menuitem"]').find(i => i.text() === 'Select only this').trigger('click')
    expect(wrapper.emitted('select-only')[0]).toEqual([['3']])
    wrapper.unmount()
  })

  it('keeps the governance markers a regulated team relies on', async () => {
    const flagged = buildTree([
      { document_id: 'g1', filename: 'held.pdf', source_type: 'upload', policy_status: 'quarantined' },
      { document_id: 'g2', filename: 'secret.pdf', source_type: 'upload', sensitivity_effective: 'restricted', uploaded_by: 'ada@lab.org' },
      { document_id: 'g3', filename: 'odd.pdf', source_type: 'upload', injection_warnings: { 2: {} } },
    ])
    const wrapper = mount(SourcesTree, { props: { tree: flagged, collapsed: new Set(), selectedIds: [] } })
    const rows = wrapper.findAll('[role="treeitem"]')
    expect(rows[0].text()).toContain('held')
    expect(rows[2].text()).toContain('restricted')
    expect(rows[2].find('[title*="Added by ada@lab.org"]').exists()).toBe(true)
    await rows[0].findAll('button').find(b => b.text() === 'held').trigger('click')
    expect(wrapper.emitted('policy')[0][0].document_id).toBe('g1')
    await rows[1].get('[aria-label^="Prompt-injection note"]').trigger('click')
    expect(wrapper.emitted('injection')[0][0].document_id).toBe('g3')
    expect(wrapper.emitted('toggle')).toBeUndefined()          // a marker click is not a selection
  })

  it('hides a collapsed folder and moves with the keyboard', async () => {
    const wrapper = mountTree({ collapsed: new Set([tree.folders[0].path]) })
    expect(wrapper.findAll('[role="treeitem"]')).toHaveLength(2)
    await wrapper.get('[role="tree"]').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.emitted('toggle-folder')[0]).toEqual([tree.folders[0].path])
    wrapper.unmount()
  })
})
