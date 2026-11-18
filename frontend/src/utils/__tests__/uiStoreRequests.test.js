import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useUiStore } from '../../stores/uiStore'
import { authorsLine, checkReasons, displayTitle, doubtful } from '../researchApi.js'

beforeEach(() => setActivePinia(createPinia()))

describe('Connect a model always lands on the providers (C3)', () => {
  it('asks for the providers section', () => {
    const ui = useUiStore()
    ui.connectModel()
    expect(ui.settingsRequest.id).toBe('providers')
  })

  it('a toast can carry one action', () => {
    const ui = useUiStore()
    let ran = false
    ui.notify('Renamed 2 sources', 'success', { action: { label: 'Undo all', run: () => { ran = true } } })
    expect(ui.toasts[0].action.label).toBe('Undo all')
    ui.toasts[0].action.run()
    expect(ran).toBe(true)
  })
})

describe('doubtful records are never stated as fact (A1)', () => {
  const doubtfulPaper = {
    filename: 'gulls.pdf', title: 'We count nesting gulls', authors: ['Imaginary Urban Ecology'],
    needs_checking: { title: 'stops mid-sentence', authors: 'reads like a journal' },
  }

  it('uses the file name and a placeholder', () => {
    expect(displayTitle(doubtfulPaper)).toBe('gulls.pdf')
    expect(authorsLine(doubtfulPaper)).toBe('Authors need checking')
    expect(doubtful(doubtfulPaper, 'year')).toBe(false)
    expect(checkReasons(doubtfulPaper)).toEqual(['Title: stops mid-sentence', 'Authors: reads like a journal'])
  })

  it('states a sure record, and an organisation in place of authors', () => {
    expect(displayTitle({ filename: 'x.pdf', title: 'Right', needs_checking: {} })).toBe('Right')
    expect(authorsLine({ authors: ['Ada Byron', 'Charles Babbage', 'Mary Somerville'], needs_checking: {} })).toBe('Byron et al.')
    expect(authorsLine({ authors: [], details: { organization: 'Harbour Authority' } })).toBe('Harbour Authority')
  })
})
