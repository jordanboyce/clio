import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const http = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn(() => Promise.resolve({})) }))
vi.mock('../../utils/http', () => ({ default: http }))

import ExportCollectionModal from '../ExportCollectionModal.vue'
import ImportCollectionModal from '../ImportCollectionModal.vue'
import { useCollectionStore } from '../../stores/collectionStore'

const model = { model: 'bge-small', label: 'bge-small · on this machine · 384 dimensions', recorded: true }

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  // jsdom has no native <dialog> modal behaviour
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
})

describe('Export dialog', () => {
  it('states which model indexed the collection and what that means for the importer', async () => {
    http.get.mockResolvedValue({ data: {
      name: 'Handbooks', documents: 2, chunks: 1200, quarantined_excluded: 1,
      embedding: model, vectors_included: true, sources_bytes: 5 * 1024 * 1024,
    } })
    const wrapper = mount(ExportCollectionModal)
    wrapper.vm.open({ id: 'c1', name: 'Handbooks' })
    await flushPromises()

    expect(http.get).toHaveBeenCalledWith('/api/collections/c1/export/preview')
    const text = wrapper.text()
    expect(text).toContain('bge-small · on this machine · 384 dimensions')
    expect(text).toContain('2 sources')
    expect(text).toContain('1 held source left out')
    expect(text).toContain('reuses the vectors')
    expect(text).toContain('5 MB')

    const link = wrapper.get('a.btn-primary')
    expect(link.attributes('href')).toBe('/api/collections/c1/export?include_sources=false')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(link.attributes('href')).toBe('/api/collections/c1/export?include_sources=true')
  })

  it('warns when the model was never recorded', async () => {
    http.get.mockResolvedValue({ data: {
      documents: 1, chunks: 3, quarantined_excluded: 0, vectors_included: false, sources_bytes: 0,
      embedding: { label: 'unknown model', recorded: false },
    } })
    const wrapper = mount(ExportCollectionModal)
    wrapper.vm.open({ id: 'c1', name: 'Old' })
    await flushPromises()
    expect(wrapper.text()).toContain('before Clio recorded its model')
    expect(wrapper.text()).toContain("can't be vouched for")
  })

  it('shows the server reason when the collection cannot be exported', async () => {
    http.get.mockRejectedValue({ response: { data: { detail: 'This collection has nothing indexed to export yet.' } } })
    const wrapper = mount(ExportCollectionModal)
    wrapper.vm.open({ id: 'c1', name: 'Empty' })
    await flushPromises()
    expect(wrapper.text()).toContain('nothing indexed to export yet')
    expect(wrapper.get('a.btn-primary').classes()).toContain('btn-disabled')
  })
})

describe('Import dialog', () => {
  const summary = {
    upload_id: 'a'.repeat(32), name: 'Handbooks', description: '', documents: 2, chunks: 1200,
    bytes: 2048, sources_included: 0, quarantined_excluded: 0, blocked_here: 1, exported_by: 'sam@example.com',
    embedding: model, action: 'reuse', reason: 'This server embeds with the same model, so the vectors are reused as they are.',
  }

  async function pickFile(wrapper) {
    const input = wrapper.get('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [new File(['x'], 'Handbooks.clio.zip')] })
    await input.trigger('change')
    await flushPromises()
  }

  it('previews first, then imports the staged upload by id, with the chosen name', async () => {
    http.post.mockResolvedValueOnce({ data: summary })
    const store = useCollectionStore()
    store.collections = [{ id: 'x', name: 'Handbooks' }]
    const wrapper = mount(ImportCollectionModal)
    await pickFile(wrapper)

    expect(http.post.mock.calls[0][0]).toBe('/api/collections/import/preview')
    const text = wrapper.text()
    expect(text).toContain('bge-small · on this machine · 384 dimensions')
    expect(text).toContain('Ready almost at once.')
    expect(text).toContain('1 source is on this server')
    expect(text).toContain('already have a collection with this name')

    http.post.mockResolvedValueOnce({ data: { id: 'new', name: 'Handbooks 2', job_id: 7, vectors: 'reused' } })
    await wrapper.get('input.input').setValue('Handbooks 2')
    await wrapper.findAll('button').find(b => b.text() === 'Import').trigger('click')
    await flushPromises()

    const [url, form] = http.post.mock.calls[1]
    expect(url).toBe('/api/collections/import')
    expect(form.get('upload_id')).toBe(summary.upload_id)
    expect(form.get('name')).toBe('Handbooks 2')
    expect(form.get('file')).toBeNull() // not uploaded a second time
    expect(wrapper.emitted('imported')[0][0].id).toBe('new')
    expect(http.delete).not.toHaveBeenCalled() // consumed, nothing to discard
  })

  it('says when the text will be embedded again, and discards a preview that is cancelled', async () => {
    http.post.mockResolvedValueOnce({ data: { ...summary, blocked_here: 0, action: 're-embed', reason: 'This server uses a different model (x).' } })
    const wrapper = mount(ImportCollectionModal)
    await pickFile(wrapper)
    expect(wrapper.text()).toContain('Will be embedded again here.')
    expect(wrapper.text()).toContain('1,200 passages, in the background')

    await wrapper.findAll('button').find(b => b.text() === 'Cancel').trigger('click')
    expect(http.delete).toHaveBeenCalledWith(`/api/collections/import/preview/${summary.upload_id}`)
  })

  it('explains a bad file and offers another', async () => {
    http.post.mockRejectedValueOnce({ response: { data: { detail: 'That file is not a Clio collection export (.clio.zip).' } } })
    const wrapper = mount(ImportCollectionModal)
    await pickFile(wrapper)
    expect(wrapper.text()).toContain('not a Clio collection export')
    expect(wrapper.text()).toContain('Choose another file')
  })
})
