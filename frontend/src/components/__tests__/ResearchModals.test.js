import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import RibbonCustomizeDialog from '../RibbonCustomizeDialog.vue'
import ResearchPaperPanel from '../ResearchPaperPanel.vue'
import { defaultLayout } from '../../utils/ribbon.js'

// jsdom has no native modal <dialog>: stand in for the two calls that matter.
let showModal
beforeEach(() => {
  setActivePinia(createPinia())
  showModal = vi.fn(function () { this.open = true })
  HTMLDialogElement.prototype.showModal = showModal
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
  http.get.mockReset()
  http.post.mockReset()
})

let wrapper
afterEach(() => { wrapper?.unmount(); wrapper = null })

describe('Customize the ribbon', () => {
  const items = [{ id: 'research', label: 'Research', group: 'work' }]

  it('opens as a modal when asked, and tells the parent on any native close', async () => {
    wrapper = mount(RibbonCustomizeDialog, { props: { open: false, items, layout: defaultLayout() }, attachTo: document.body })
    const dialog = wrapper.get('dialog').element
    expect(dialog.open).toBe(false)
    expect(wrapper.get('dialog').attributes('open')).toBeUndefined()   // no bare :open any more

    await wrapper.setProps({ open: true })
    expect(showModal).toHaveBeenCalledTimes(1)
    expect(dialog.open).toBe(true)

    dialog.close()                                   // Escape or the backdrop
    expect(wrapper.emitted('close')).toHaveLength(1)
  })

  it('Done closes it the same way', async () => {
    wrapper = mount(RibbonCustomizeDialog, { props: { open: true, items, layout: defaultLayout() }, attachTo: document.body })
    expect(showModal).toHaveBeenCalledTimes(1)       // open at mount
    await wrapper.findAll('button').find(b => b.text() === 'Done').trigger('click')
    expect(wrapper.get('dialog').element.open).toBe(false)
    expect(wrapper.emitted('close')).toHaveLength(1)
  })
})

describe('Download references', () => {
  it('is a real modal', async () => {
    http.get.mockResolvedValue({ data: {
      document_id: 'd1', filename: 'd1.pdf', title: 'Paper', authors: [], analyzed: true, references: 2, links: 0,
      reference_list: [{ id: 1, ordinal: 1, raw_text: 'A ref', status: 'unresolved', kind: 'reference' }],
      link_list: [], cited_by_list: [], assessments: [], brief_list: [], provenance: {}, details: {}, missing: [],
      capabilities: { link_download: true, registry_lookups: true }, can_write: true,
    } })
    wrapper = mount(ResearchPaperPanel, {
      props: { documentId: 'd1', collectionId: 'c1' },
      global: { stubs: { ResearchEvidence: true, ResearchBrief: true, ResearchCitePanel: true } },
      attachTo: document.body,
    })
    await flushPromises()
    const open = wrapper.findAll('button').find(b => /^Download/.test(b.text()))
    expect(open).toBeTruthy()
    await open.trigger('click')
    const dialog = wrapper.get('dialog[aria-labelledby="research-download-title"]').element
    expect(showModal).toHaveBeenCalledTimes(1)
    expect(dialog.open).toBe(true)
    await wrapper.findAll('button').find(b => b.text() === 'Cancel').trigger('click')
    expect(dialog.open).toBe(false)
  })
})
