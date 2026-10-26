import { describe, expect, it } from 'vitest'
import { locationLabel } from '../fileTypes'

describe('locationLabel', () => {
  it('names the unit a reader would look for', () => {
    expect(locationLabel('report.pdf', 12)).toBe('p.12')
    expect(locationLabel('deck.pptx', 3)).toBe('slide 3')
    expect(locationLabel('notes.md', 2)).toBe('§2')
    expect(locationLabel('register.csv', 1)).toBe('part 1')
  })

  it('says nothing without a location', () => {
    expect(locationLabel('report.pdf', 0)).toBe('')
    expect(locationLabel('report.pdf', null)).toBe('')
  })
})
