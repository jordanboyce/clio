// The reference client for static-site exports lives with its guide in
// docs/static-search/. It is plain JavaScript with no dependencies, so it is
// tested here with the rest of the frontend.
import { describe, expect, it } from 'vitest'
import {
  ModelMismatch, buildIndex, checkModel, createSearch, keywordRank, rank,
} from '../../../../docs/static-search/clio-static-search.js'

const passages = [
  { id: 'a_p1_c0', document_id: 'a', page: 1, text: 'Staff accrue 25 days of holiday a year.' },
  { id: 'a_p2_c0', document_id: 'a', page: 2, text: 'Unused holiday carries over.' },
  { id: 'b_p1_c0', document_id: 'b', page: 1, text: 'Expenses are reimbursed within thirty days.' },
]
const unitRows = [[1, 0, 0, 0], [0.6, 0.8, 0, 0], [0, 0, 1, 0]]
const scale = 127

function manifest(extra = {}) {
  return {
    format: 'clio-static-search', version: 1,
    collection: { name: 'Handbooks' },
    embedding: {
      model: 'BAAI/bge-small-en-v1.5', dimension: 4, pooling: 'cls', query_prefix: 'Q: ',
      browser: { model: 'Xenova/bge-small-en-v1.5', dtype: 'fp32' },
      probe: { text: 'probe', vector: [0, 0, 0, 1] },
      ...extra,
    },
    vectors: { file: 'vectors.bin', count: 3, dimension: 4, quantization: 'int8', scale },
    passages: { count: 3, files: [{ file: 'passages-0001.json', count: 2 }, { file: 'passages-0002.json', count: 1 }] },
    documents: [{ id: 'a', name: 'holidays.txt', pages: 2 }, { id: 'b', name: 'expenses.txt', pages: 1 }],
  }
}
const int8 = () => new Int8Array(unitRows.flat().map((x) => Math.round(x * scale))).buffer

// A stand-in model: fixed vectors for the texts the tests embed.
const embedder = (vectors, model = 'Xenova/bge-small-en-v1.5') => ({
  model,
  embed: async (text) => vectors[text] || [0, 0, 0, 1],
})

function site(files) {
  return async (url) => {
    const name = url.split('/').pop()
    if (!(name in files)) return { ok: false, status: 404 }
    return { ok: true, json: async () => files[name], arrayBuffer: async () => files[name] }
  }
}
const files = () => ({
  'manifest.json': manifest(), 'vectors.bin': int8(),
  'passages-0001.json': passages.slice(0, 2), 'passages-0002.json': passages.slice(2),
})

describe('ranking', () => {
  it('scores 8-bit vectors on the same scale as the full-precision ones', () => {
    const index = buildIndex(manifest(), passages, int8())
    const hits = rank(index, [3, 4, 0, 0], { k: 2 }) // not unit length: the client normalises
    expect(hits.map((h) => h.passage.id)).toEqual(['a_p2_c0', 'a_p1_c0'])
    expect(hits[0].score).toBeCloseTo(1.0, 2)
    expect(hits[1].score).toBeCloseTo(0.6, 2)
    expect(hits[0].document.name).toBe('holidays.txt')
  })

  it('reads full-precision vectors too', () => {
    const m = manifest()
    m.vectors = { ...m.vectors, quantization: 'float32', scale: 1 }
    const index = buildIndex(m, passages, new Float32Array(unitRows.flat()).buffer)
    expect(rank(index, [0, 0, 1, 0], { k: 1 })[0]).toMatchObject({ score: 1, passage: { id: 'b_p1_c0' } })
  })

  it('refuses files that do not line up, and a query of the wrong size', () => {
    expect(() => buildIndex(manifest(), passages.slice(1), int8())).toThrow('2 passages for 3 vectors')
    expect(() => buildIndex(manifest(), passages, new Int8Array(8).buffer)).toThrow('not 3 x 4')
    expect(() => rank(buildIndex(manifest(), passages, int8()), [1, 0, 0])).toThrow(ModelMismatch)
  })
})

describe('the same model on both sides', () => {
  it('accepts a model that reproduces the probe vector', async () => {
    const result = await checkModel(manifest(), embedder({ probe: [0, 0.001, 0, 2] }))
    expect(result.checked).toBe('probe')
    expect(result.cosine).toBeGreaterThan(0.999)
  })

  it('refuses one that does not, whatever it is called', async () => {
    await expect(checkModel(manifest(), embedder({ probe: [0, 0, 0.2, 1] })))
      .rejects.toThrow(/does not reproduce the vectors of BAAI\/bge-small-en-v1.5/)
    await expect(checkModel(manifest(), embedder({ probe: [1, 0, 0] }))).rejects.toThrow('gives 3')
  })

  it('falls back to the name when the export carries no probe', async () => {
    const m = manifest({ probe: null })
    expect((await checkModel(m, embedder({}))).checked).toBe('name')
    await expect(checkModel(m, embedder({}, 'Xenova/all-MiniLM-L6-v2'))).rejects.toThrow(ModelMismatch)
  })
})

describe('createSearch', () => {
  it('loads split passage files and searches by meaning', async () => {
    const search = await createSearch('https://example.org/search', {
      fetch: site(files()),
      embedder: (m) => embedder({ [m.embedding.probe.text]: [0, 0, 0, 1], expenses: [0, 0, 1, 0] }),
    })
    expect(search.semantic).toBe(true)
    expect(search.index.passages).toHaveLength(3)
    const hits = await search('expenses', { k: 1 })
    expect(hits[0].passage.id).toBe('b_p1_c0')
  })

  it('turns search by meaning off for the wrong model and still finds words', async () => {
    const search = await createSearch('https://example.org/search/', {
      fetch: site(files()), embedder: embedder({ probe: [1, 0, 0, 0] }),
    })
    expect(search.semantic).toBe(false)
    expect(search.reason).toContain('Search by meaning is off')
    const hits = await search('holiday year')
    expect(hits.map((h) => h.passage.id)).toEqual(['a_p1_c0', 'a_p2_c0'])
  })

  it('does the same when the model cannot be loaded at all', async () => {
    const search = await createSearch('https://example.org/search/', {
      fetch: site(files()), embedder: async () => { throw new Error('model download blocked') },
    })
    expect(search.semantic).toBe(false)
    expect(search.reason).toBe('model download blocked')
    expect(await search('zebra')).toEqual([])
  })

  it('says so when the folder is not an export', async () => {
    await expect(createSearch('https://example.org/x/', { fetch: site({}) })).rejects.toThrow('manifest.json (404)')
    await expect(createSearch('https://example.org/x/', { fetch: site({ 'manifest.json': { format: 'other' } }) }))
      .rejects.toThrow('not a Clio static search export')
  })
})

describe('keyword fallback', () => {
  it('weighs rarer words more', () => {
    const index = buildIndex(manifest(), passages, int8())
    expect(keywordRank(index, 'holiday accrue')[0].passage.id).toBe('a_p1_c0')
  })
})
