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
    expect(labels.slice(0, 4)).toEqual(['Ask', 'Find', 'Research', 'Citation graph'])
    expect(wrapper.get('[data-ribbon-item="graph"]').attributes('aria-current')).toBe('page')
    expect(wrapper.get('[data-ribbon-item="research"]').attributes('aria-current')).toBeUndefined()
    await wrapper.get('[data-ribbon-item="mcp"]').trigger('click')
    expect(wrapper.emitted('action')[0][0].tab).toBe('mcp')
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

  it('hides a collapsed folder and moves with the keyboard', async () => {
    const wrapper = mountTree({ collapsed: new Set([tree.folders[0].path]) })
    expect(wrapper.findAll('[role="treeitem"]')).toHaveLength(2)
    await wrapper.get('[role="tree"]').trigger('keydown', { key: 'ArrowRight' })
    expect(wrapper.emitted('toggle-folder')[0]).toEqual([tree.folders[0].path])
    wrapper.unmount()
  })
})
