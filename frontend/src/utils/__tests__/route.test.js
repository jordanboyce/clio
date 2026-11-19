import { describe, expect, it } from 'vitest'
import { parseRoute, routeHash } from '../route'

describe('real URLs (U8, C1)', () => {
  it('reads the view from the hash, in the words people see', () => {
    expect(parseRoute('#/library')).toEqual({ tab: 'research', view: null })
    expect(parseRoute('#/library/map')).toEqual({ tab: 'research', view: 'map' })
    expect(parseRoute('#/library/cite')).toEqual({ tab: 'research', view: 'write' })
    expect(parseRoute('#/find')).toEqual({ tab: 'search', view: null })
    expect(parseRoute('#/connect')).toEqual({ tab: 'mcp', view: null })
    expect(parseRoute('#/research')).toEqual({ tab: 'research', view: null })   // the old name
  })

  it('ignores what names no view', () => {
    expect(parseRoute('')).toBeNull()
    expect(parseRoute('#/nowhere')).toBeNull()
    expect(parseRoute('#section-3')).toBeNull()
  })

  it('writes the hash back, round trip', () => {
    for (const [tab, view] of [['research', 'map'], ['research', 'library'], ['search', null], ['chat', null], ['mcp', null]]) {
      const parsed = parseRoute(routeHash(tab, view))
      expect(parsed.tab).toBe(tab)
    }
    expect(routeHash('research', 'reviews')).toBe('#/library/reviews')
    expect(routeHash('research', 'library')).toBe('#/library')
  })
})
