import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import {
  FRESH_MS, beginBootstrap, canonicalKey, clearBootstrap, lookupBootstrap, resetBootstrap,
} from '../bootstrap'
import http from '../http'

const SECTIONS = {
  responses: {
    '/api/features': { status: 200, body: { features: [{ id: 'chat', enabled: true }] } },
    '/documents?collection_id=default&limit=200&offset=0': { status: 200, body: { documents: [{ filename: 'pumps.txt' }] } },
    '/api/admin/review/summary': { status: 403 },
    '/api/collections/default/embedding-health': { status: 504 },
  },
}

beforeEach(() => {
  setActivePinia(createPinia())
  resetBootstrap()
})
afterEach(() => vi.useRealTimers())

describe('canonicalKey', () => {
  it('matches api/bootstrap.key_for: path, then the query sorted by name', () => {
    expect(canonicalKey('/documents', { offset: 0, collection_id: 'default', limit: 200 }))
      .toBe('/documents?collection_id=default&limit=200&offset=0')
    expect(canonicalKey('/health?collection_id=default')).toBe('/health?collection_id=default')
    expect(canonicalKey('/api/features', { q: undefined, kind: null })).toBe('/api/features')
    expect(canonicalKey('/documents/changes', { collection_id: 'a b', since: 0, limit: 20 }))
      .toBe('/documents/changes?collection_id=a+b&limit=20&since=0')
  })
})

describe('lookupBootstrap', () => {
  it('answers a 2xx section with a copy each caller may change', async () => {
    await beginBootstrap(async () => SECTIONS)

    const first = await lookupBootstrap('/api/features')
    first.body.features.push('mine')
    const second = await lookupBootstrap('/api/features')

    expect(first.status).toBe(200)
    expect(second.body.features).toHaveLength(1)
  })

  it('sends a failed section, or one it does not hold, to the network', async () => {
    await beginBootstrap(async () => SECTIONS)

    expect(await lookupBootstrap('/api/admin/review/summary')).toBeNull()
    expect(await lookupBootstrap('/api/collections/default/embedding-health')).toBeNull()
    expect(await lookupBootstrap('/api/artifacts')).toBeNull()
    expect(await lookupBootstrap('/documents', { collection_id: 'default', limit: 200, offset: 0, q: 'pump' })).toBeNull()
  })

  it('waits for a bootstrap still in flight', async () => {
    let finish
    beginBootstrap(() => new Promise((resolve) => { finish = resolve }))
    const answer = lookupBootstrap('/documents', { collection_id: 'default', limit: 200, offset: 0 })
    await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
    finish(SECTIONS)

    expect((await answer).body.documents[0].filename).toBe('pumps.txt')
  })

  it('stops answering once the answers are old', async () => {
    vi.useFakeTimers()
    await beginBootstrap(async () => SECTIONS)
    vi.advanceTimersByTime(FRESH_MS + 1)

    expect(await lookupBootstrap('/api/features')).toBeNull()
  })

  it('answers nothing after a failed bootstrap, or once cleared', async () => {
    await beginBootstrap(async () => { throw new Error('401') })
    expect(await lookupBootstrap('/api/features')).toBeNull()

    await beginBootstrap(async () => SECTIONS)
    clearBootstrap()
    expect(await lookupBootstrap('/api/features')).toBeNull()
  })
})

describe('http answers startup reads from the bootstrap', () => {
  it('serves a GET from its section without touching the network', async () => {
    await beginBootstrap(async () => SECTIONS)

    const r = await http.get('/documents', { params: { collection_id: 'default', limit: 200, offset: 0 } })

    expect(r.status).toBe(200)
    expect(r.data.documents[0].filename).toBe('pumps.txt')
  })

  it('a write clears the answers, so a read after it is fresh', async () => {
    await beginBootstrap(async () => SECTIONS)

    await http.post('http://127.0.0.1:9/api/features', {}, { timeout: 50 }).catch(() => {})

    expect(await lookupBootstrap('/api/features')).toBeNull()
  })
})
