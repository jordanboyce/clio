import { describe, expect, it } from 'vitest'
import { fitPanel, panelBudget, readWidth } from '../panels'

describe('side panels keep the answer column (C6)', () => {
  it('reads a saved width within its limits, and survives a corrupt one', () => {
    expect(readWidth('900', 320, 220, 600)).toBe(600)
    expect(readWidth('100', 320, 220, 600)).toBe(220)
    expect(readWidth('NaNpx', 320, 220, 600)).toBe(320)
    expect(readWidth(null, 320, 220, 600)).toBe(320)
  })

  it('shrinks two wide panels on a 1280px projector so the centre keeps 480px', () => {
    const budget = panelBudget(1280, 2)
    const left = fitPanel(600, 220, budget)
    const right = fitPanel(600, 240, budget)
    expect(1280 - 56 - left - right).toBeGreaterThanOrEqual(480)
    expect(fitPanel(320, 220, panelBudget(1920, 2))).toBe(320)   // room enough: unchanged
  })
})
