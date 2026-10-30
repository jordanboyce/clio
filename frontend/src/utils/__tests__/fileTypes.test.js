import { describe, expect, it } from 'vitest'
import { describeFile, locationLabel } from '../fileTypes'

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

describe('describeFile', () => {
  it('draws pasted text as a note, whatever file it is stored as', () => {
    expect(describeFile('Travel policy.html')).toMatchObject({ family: 'docs', label: 'html' })
    expect(describeFile('Travel policy.html', { isNote: true })).toMatchObject({ family: 'docs', label: 'text' })
    expect(describeFile('page.html', { isWeb: true })).toMatchObject({ family: 'web', label: 'web' })
  })
})
