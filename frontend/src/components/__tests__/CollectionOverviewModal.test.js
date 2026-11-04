import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const http = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../../utils/http', () => ({ default: http }))

import CollectionOverviewModal from '../CollectionOverviewModal.vue'

const stats = (overrides = {}) => ({
  collection_id: 'c1',
  total_documents: 1240,
  total_pages: 8000,
  total_chunks: 52000,
  total_bytes: 5 * 1024 * 1024,
  oldest_upload: '2026-01-15T12:00:00',
  newest_upload: '2026-03-15T12:00:00',
  documents_without_recorded_size: 0,
  documents_without_chunks: 0,
  held_documents: 0,
  distinct_extensions: 2,
  by_extension: [
    { extension: '.pdf', documents: 1000, bytes: 4 * 1024 * 1024, chunks: 50000 },
    { extension: '.xlsx', documents: 240, bytes: 1024 * 1024, chunks: 2000 },
  ],
  by_kind: { docs: 1000, data: 240 },
  by_sensitivity: { unlabeled: 1240 },
  largest_documents: [{ document_id: 'd1', filename: 'handbook.pdf', bytes: 2 * 1024 * 1024 }],
  ...overrides,
})

const openWith = async (data) => {
  http.get.mockResolvedValue({ data })
  const wrapper = mount(CollectionOverviewModal)
  wrapper.vm.open({ id: 'c1', name: 'Handbooks' })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  // jsdom has no native <dialog> modal behaviour
  HTMLDialogElement.prototype.showModal = function () { this.open = true }
  HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
})

describe('Collection overview', () => {
  it('reads the exact server numbers instead of the document listing', async () => {
    const text = (await openWith(stats())).text()

    expect(http.get).toHaveBeenCalledWith('/documents/stats', { params: { collection_id: 'c1' } })
    expect(text).toContain('Handbooks')
    expect(text).toContain((1240).toLocaleString())
    expect(text).toContain('5 MB')
    expect(text).toContain('pdf')
    expect(text).toContain('handbook.pdf')
  })

  it('explains what the numbers leave out only when something is left out', async () => {
    expect((await openWith(stats())).text()).not.toContain('left out')

    const text = (await openWith(stats({
      held_documents: 2, documents_without_chunks: 3, documents_without_recorded_size: 1,
    }))).text()
    expect(text).toContain('2 held for review are left out.')
    expect(text).toContain('3 without text passages')
    expect(text).toContain('1 with no recorded size')
  })

  it('shows labels once one has been applied', async () => {
    expect((await openWith(stats())).text()).not.toContain('Labels')
    const text = (await openWith(stats({ by_sensitivity: { unlabeled: 1239, internal: 1 } }))).text()
    expect(text).toContain('Labels')
    expect(text).toContain('internal 1')
  })

  it('builds the export link from the chosen scope and format', async () => {
    const wrapper = await openWith(stats())
    const link = wrapper.get('a.btn-primary')
    expect(link.attributes('href')).toBe('/documents/export?collection_id=c1&format=csv')

    const [include, format] = wrapper.findAll('select')
    await include.setValue('docs')
    await format.setValue('txt')
    expect(link.attributes('href')).toBe('/documents/export?collection_id=c1&format=txt&file_type=docs')
  })

  it('offers no export for an empty collection', async () => {
    const wrapper = await openWith(stats({
      total_documents: 0, total_pages: 0, total_chunks: 0, total_bytes: 0,
      oldest_upload: null, newest_upload: null, distinct_extensions: 0,
      by_extension: [], by_kind: {}, by_sensitivity: {}, largest_documents: [],
    }))
    expect(wrapper.find('a.btn-primary').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('Export the file list')
  })

  it('says so when the stats cannot be read', async () => {
    http.get.mockRejectedValue({ message: 'Collection not found' })
    const wrapper = mount(CollectionOverviewModal)
    wrapper.vm.open({ id: 'c1', name: 'Handbooks' })
    await flushPromises()
    expect(wrapper.get('.alert-error').text()).toBe('Collection not found')
  })
})
