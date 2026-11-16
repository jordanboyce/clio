// R9 renamed the Research tab to Library and the Research depth to Deep
// search. Browsers and links still hold the old names; each must still work.
import { beforeEach, describe, expect, it } from 'vitest'
import { DEPTHS, depthLabel, normalizeDepth } from '../answerDepth.js'
import { RIBBON_ITEMS, loadLayout, resolveTab, visibleItems } from '../ribbon.js'

describe('the Library tab under its old and new names', () => {
  beforeEach(() => localStorage.clear())

  it('opens from ?tab=library and from the old ?tab=research', () => {
    expect(resolveTab('library')).toBe('research')
    expect(resolveTab('research')).toBe('research')
    expect(resolveTab('search')).toBe('search')
    expect(resolveTab('nonsense')).toBeNull()
    expect(resolveTab(null)).toBeNull()
  })

  it('keeps a ribbon layout saved before the rename, with the item named Library', () => {
    // A layout from before R9: Research moved to the top, the citation graph shown.
    localStorage.setItem('clio_ribbon', JSON.stringify({
      order: ['research', 'chat', 'search', 'graph'], hidden: ['reviews', 'cite'], labels: true,
    }))
    const layout = loadLayout()
    expect(layout.order.slice(0, 4)).toEqual(['research', 'chat', 'search', 'graph'])
    expect(layout.labels).toBe(true)
    const shown = visibleItems(layout)
    expect(shown[0]).toMatchObject({ id: 'research', label: 'Library', tab: 'research' })
    expect(shown.find(i => i.id === 'graph').label).toBe('Citation map')
  })

  it('names no ribbon item Research or Citation graph', () => {
    const labels = RIBBON_ITEMS.map(i => i.label)
    expect(labels).not.toContain('Research')
    expect(labels).not.toContain('Citation graph')
  })
})

describe('the answer depth under its old and new names', () => {
  it('reads the old "research" as "deep" and refuses anything else', () => {
    expect(DEPTHS).toEqual(['quick', 'deep'])
    expect(normalizeDepth('research')).toBe('deep')
    expect(normalizeDepth('deep')).toBe('deep')
    expect(normalizeDepth('quick')).toBe('quick')
    expect(normalizeDepth('thorough')).toBeNull()
    expect(normalizeDepth(null)).toBeNull()
  })

  it('labels an old research answer as Deep search', () => {
    expect(depthLabel('research')).toBe('Deep search')
    expect(depthLabel('deep')).toBe('Deep search')
    expect(depthLabel('quick')).toBe('Quick')
  })
})
