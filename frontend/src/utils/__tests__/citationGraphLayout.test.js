import { describe, expect, it } from 'vitest'
import { panelLayout } from '../citationGraph.js'

// Map widths measured in the app: a 1000px window gives a ~588px map, a
// phone ~343px, a 1600px window ~1152px.
describe('where the map panel goes', () => {
  it('keeps the overview off the graph at a 1000px window and at phone width', () => {
    for (const width of [588, 343]) {
      const layout = panelLayout(width)
      expect(layout.docked).toBe(false)          // it could only float over the graph...
      expect(layout.autoOverview).toBe(false)    // ...so it does not open by itself
    }
  })

  it('docks it beside the graph on a wide map, leaving most of the room to the graph', () => {
    const layout = panelLayout(1152)
    expect(layout.docked && layout.autoOverview).toBe(true)
    expect(1152 - layout.widthRem * 16).toBeGreaterThan(1152 * 0.6)
  })

  it('opens it only on request in between, and narrower', () => {
    const layout = panelLayout(800)
    expect(layout.docked).toBe(true)
    expect(layout.autoOverview).toBe(false)
    expect(800 - layout.widthRem * 16).toBeGreaterThanOrEqual(800 * 0.55)
  })

  it('treats an unmeasured map as narrow', () => {
    expect(panelLayout(0)).toMatchObject({ docked: false, autoOverview: false })
  })
})
