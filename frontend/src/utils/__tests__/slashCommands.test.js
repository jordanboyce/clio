import { beforeEach, describe, expect, it, vi } from 'vitest'

const http = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('../http', () => ({ default: http }))

import { isSlashCommand, runSlashCommand } from '../slashCommands'

const stats = (overrides = {}) => ({
  total_documents: 7,
  total_pages: 14,
  total_chunks: 24,
  total_bytes: 2048,
  oldest_upload: '2026-03-02T10:00:00',
  newest_upload: '2026-03-08T10:00:00',
  by_extension: [{ extension: '.pdf', documents: 2, bytes: 1024, chunks: 8 }],
  held_documents: 0,
  ...overrides,
})

const run = (input) => runSlashCommand(input, {
  collectionId: 'c1',
  collection: { name: 'Handbooks' },
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe('/stats', () => {
  it('reports the same numbers as the collection overview', async () => {
    http.get.mockResolvedValue({ data: stats({ held_documents: 1 }) })
    const { content, error } = await run('/stats')

    expect(error).toBeUndefined()
    expect(http.get).toHaveBeenCalledWith('/documents/stats', { params: { collection_id: 'c1' } })
    expect(content).toContain('Documents:     7')
    expect(content).toContain('Size:          2 KB')
    expect(content).toContain('pdf (2)')
    expect(content).toContain('2026-03-02 → 2026-03-08')
    expect(content).toContain('1 held for review is left out.')
  })
})

describe('/export', () => {
  it('is a recognised command', () => {
    expect(isSlashCommand('/export')).toBe(true)
  })

  it('starts the download and says what it contains', async () => {
    const clicked = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function () {
      clicked.push(this.getAttribute('href'))
    })
    http.get.mockResolvedValue({ data: stats({ held_documents: 2 }) })

    const { content } = await run('/export')

    expect(clicked).toEqual(['/documents/export?collection_id=c1&format=csv'])
    expect(content).toContain('Downloading the list of 7 files in Handbooks as CSV.')
    expect(content).toContain('2 held for review are left out.')
    expect(document.querySelector('a[download]')).toBeNull()
  })

  it('does not download an empty list', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    http.get.mockResolvedValue({ data: stats({ total_documents: 0 }) })

    const { content } = await run('/export')

    expect(click).not.toHaveBeenCalled()
    expect(content).toContain('Handbooks has no documents to export.')
  })

  it('reports a stats failure instead of downloading', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    http.get.mockRejectedValue({ message: 'Collection not found' })

    const { content, error } = await run('/export')

    expect(error).toBe(true)
    expect(click).not.toHaveBeenCalled()
    expect(content).toContain('Collection not found')
  })
})

describe('/docs', () => {
  it('points at /export when the listing is cut short', async () => {
    const documents = Array.from({ length: 101 }, (_, i) => ({ filename: `f${i}.pdf` }))
    http.get.mockResolvedValue({ data: { documents } })

    const { content } = await run('/docs')

    expect(content).toContain('…and 1 more. /export downloads the full list.')
  })
})
