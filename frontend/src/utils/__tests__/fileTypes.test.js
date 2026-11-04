import { describe, expect, it } from 'vitest'
import { describeFile, isTabularFile, locationLabel } from '../fileTypes'

describe('locationLabel', () => {
  it('names the unit a reader would look for', () => {
    expect(locationLabel('report.pdf', 12)).toBe('p.12')
    expect(locationLabel('deck.pptx', 3)).toBe('slide 3')
    expect(locationLabel('notes.md', 2)).toBe('§2')
    expect(locationLabel('register.csv', 1)).toBe('part 1')
    expect(locationLabel('register.tsv', 1)).toBe('part 1')
    expect(locationLabel('deck.odp', 2)).toBe('slide 2')
    expect(locationLabel('book.epub', 4)).toBe('chapter 4')
    expect(locationLabel('inbox.mbox', 7)).toBe('message 7')
  })

  it('says nothing without a location', () => {
    expect(locationLabel('report.pdf', 0)).toBe('')
    expect(locationLabel('report.pdf', null)).toBe('')
  })
})

describe('document and data families for the broader formats', () => {
  it('files documents, mail and subtitles under docs', () => {
    for (const name of ['a.rtf', 'a.odt', 'a.epub', 'a.eml', 'a.mbox', 'a.xhtml', 'a.rst', 'a.tex', 'a.log', 'a.srt', 'a.vtt', 'a.pptx']) {
      expect(describeFile(name).family, name).toBe('docs')
    }
  })

  it('files spreadsheets and delimited text under data, and queries them as tables', () => {
    for (const name of ['a.tsv', 'a.xlsm', 'a.ods']) {
      expect(describeFile(name).family, name).toBe('data')
    }
    expect(isTabularFile('a.tsv')).toBe(true)
    expect(isTabularFile('a.xlsm')).toBe(true)
    expect(isTabularFile('a.ods')).toBe(false)
  })

  it('draws notebooks as code', () => {
    expect(describeFile('analysis.ipynb').family).toBe('code')
  })
})

describe('describeFile', () => {
  it('draws pasted text as a note, whatever file it is stored as', () => {
    expect(describeFile('Travel policy.html')).toMatchObject({ family: 'docs', label: 'html' })
    expect(describeFile('Travel policy.html', { isNote: true })).toMatchObject({ family: 'docs', label: 'text' })
    expect(describeFile('page.html', { isWeb: true })).toMatchObject({ family: 'web', label: 'web' })
  })
})
