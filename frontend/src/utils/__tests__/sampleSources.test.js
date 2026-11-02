import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import http from '../http'
import {
  SAMPLE_COLLECTION_NAME,
  SAMPLE_FILES,
  isSampleSet,
  loadSampleCollection,
} from '../sampleSources'

const docs = (names) => ({ data: { documents: names.map(filename => ({ filename })), total_documents: names.length } })
const sampleNames = SAMPLE_FILES.map(f => f.name)

const fakeStore = (owned = []) => ({
  ownedCollections: owned,
  loadCollections: vi.fn(async () => {}),
  createCollection: vi.fn(async (data) => ({ id: 'new-sample', ...data })),
})

beforeEach(() => {
  http.get.mockReset()
  http.post.mockReset()
  globalThis.fetch = vi.fn(async (url) => ({
    ok: true,
    headers: { get: () => 'text/plain' },
    blob: async () => new Blob([String(url)]),
  }))
})

describe('isSampleSet', () => {
  it('is true for exactly the sample files', async () => {
    http.get.mockResolvedValue(docs(sampleNames))
    expect(await isSampleSet('c1')).toBe(true)
  })

  it('is false once anything else is in the collection', async () => {
    http.get.mockResolvedValue(docs([...sampleNames.slice(1), 'my-report.pdf']))
    expect(await isSampleSet('c1')).toBe(false)
  })

  it('is false for a subset', async () => {
    http.get.mockResolvedValue(docs(sampleNames.slice(0, 2)))
    expect(await isSampleSet('c1')).toBe(false)
  })
})

describe('loadSampleCollection', () => {
  it('creates a private sample collection and uploads into it', async () => {
    const store = fakeStore()
    http.get.mockResolvedValue(docs([]))
    http.post.mockResolvedValue({ data: { documents_processed: 4 } })

    const collection = await loadSampleCollection(store)

    expect(collection.id).toBe('new-sample')
    expect(store.createCollection).toHaveBeenCalledWith(
      expect.objectContaining({ name: SAMPLE_COLLECTION_NAME, visibility: 'private' }),
    )
    expect(http.post).toHaveBeenCalledWith(
      '/documents/upload', expect.any(FormData),
      expect.objectContaining({ params: { collection_id: 'new-sample' } }),
    )
  })

  it('reuses an existing, already-filled sample collection without uploading again', async () => {
    const store = fakeStore([{ id: 'old-sample', name: SAMPLE_COLLECTION_NAME }])
    http.get.mockResolvedValue(docs(sampleNames))

    const collection = await loadSampleCollection(store)

    expect(collection.id).toBe('old-sample')
    expect(store.createCollection).not.toHaveBeenCalled()
    expect(http.post).not.toHaveBeenCalled()
  })
})
