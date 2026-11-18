import { describe, expect, it } from 'vitest'
import { describeFile } from '../fileTypes'
import {
  countsLine, cutSetProvenance, cutoff, focusFromPassage, gateLabel, isPraDoc, modelTool, parsePraHref, percent,
  praHref, praTargetFor, ratio, rrwText, sci, sequenceMarks, sequenceOutcome, sequencePathText,
} from '../praFormat'
import {
  ROOT_KEY, canExpand, expandAllLoaded, findInOutline, flattenOutline, graft, initialExpansion, nodeAt,
  outlineKeyAction,
} from '../praOutline'

describe('PRA number formats', () => {
  it('writes probabilities in scientific notation and never turns missing into 0', () => {
    expect(sci(1.57e-5)).toBe('1.57E-5')
    expect(sci(0.5)).toBe('5.00E-1')
    expect(sci(2)).toBe('2.00E0')
    expect(sci(0)).toBe('0')
    expect(sci(null)).toBe('—')
    expect(sci(undefined)).toBe('—')
    expect(sci('x')).toBe('—')
    expect(cutoff(1e-13)).toBe('1E-13')
    expect(cutoff(2.5e-12)).toBe('2.5E-12')
  })

  it('keeps ratios plain until they grow large, and shares as percentages', () => {
    expect(ratio(12.3456)).toBe('12.3')
    expect(ratio(1)).toBe('1')
    expect(ratio(123456)).toBe('1.23E5')
    expect(ratio(null)).toBe('—')
    expect(percent(0.5873)).toBe('59%')
    expect(percent(0.055)).toBe('5.5%')
    expect(percent(0.0004)).toBe('<0.1%')
    expect(percent(null)).toBe('—')
  })

  it('tells an unbounded RRW from an unknown one', () => {
    expect(rrwText({ rrw: null, fussell_vesely: 1 })).toBe('∞')
    expect(rrwText({ rrw: null, fussell_vesely: null })).toBe('—')
    expect(rrwText({ rrw: 1.25, fussell_vesely: 0.2 })).toBe('1.25')
  })

  it('names gates the way analysts read them, k-of-n as "k of n"', () => {
    expect(gateLabel({ type: 'or' })).toBe('OR')
    expect(gateLabel({ type: 'atleast', k: 3, children: [1, 2, 3, 4] })).toBe('3 of 4')
    expect(gateLabel({ type: 'atleast', k: 2, more: true, input_count: 3 })).toBe('2 of 3')
    expect(gateLabel({ type: 'atleast', k: 2 })).toBe('at least 2')
    expect(gateLabel({ type: 'weird' })).toBe('WEIRD')
  })

  it('says the model in one quiet line', () => {
    expect(countsLine({ fault_trees: 244, gates: 862, basic_events: 1199, house_events: 0, event_trees: 1, sequences: 2300, cut_set_lists: 852 }))
      .toBe('244 fault trees · 862 gates · 1,199 basic events · 1 event tree · 2,300 sequences · 852 stored cut set lists')
    expect(modelTool('saphire-mard').tool).toBe('SAPHIRE')
    expect(modelTool(undefined, 'pra-mef').format).toBe('Open-PSA Model Exchange Format')
  })

  it('knows a PRA source by its format, whatever its name', () => {
    expect(isPraDoc({ source_format: 'pra-mef' })).toBe(true)
    expect(isPraDoc({ source_format: 'xml' })).toBe(false)
    expect(describeFile('model.xml', { isPra: true })).toMatchObject({ family: 'data', label: 'pra' })
  })
})

describe('cut set provenance', () => {
  const computed = (computation, warnings = []) => ({
    source: 'computed', truncation: 1e-13, warnings,
    computation: { complete: true, stopped: null, truncation_raised: false, requested_truncation: 1e-15,
      time_budget_seconds: 20, max_cut_sets: 50000, ...computation },
  })

  it('says a stored list is the model\'s', () => {
    expect(cutSetProvenance({ source: 'model', truncation: null, warnings: [] }))
      .toEqual({ headline: 'From the model', lowerBound: false, notes: [] })
    expect(cutSetProvenance({ source: 'model', truncation: 1e-12, warnings: [] }).headline).toBe('From the model, truncated at 1E-12')
  })

  it('puts a time-budget stop in plain words and does not repeat the server', () => {
    const p = cutSetProvenance(computed({ stopped: 'time_budget' }, [
      'The 20 s time budget ran out: the list is complete down to cutoff 1e-13, not the 1e-15 asked for, so totals are lower bounds.',
      'Events without data in the logic: X',
    ]))
    expect(p.headline).toContain('complete down to 1E-13')
    expect(p.headline).toContain('1E-15 asked for')
    expect(p.headline).toContain('Totals are lower bounds')
    expect(p.lowerBound).toBe(true)
    expect(p.notes).toEqual(['Events without data in the logic: X'])
  })

  it('distinguishes a full search, a raised cutoff and a partial pass', () => {
    expect(cutSetProvenance(computed({ requested_truncation: 1e-13 })))
      .toMatchObject({ headline: 'Computed by Clio: every minimal cut set at or above 1E-13.', lowerBound: false })
    expect(cutSetProvenance(computed({ truncation_raised: true })).headline).toContain('cutoff was raised to 1E-13')
    expect(cutSetProvenance(computed({ complete: false, stopped: 'time_budget' })).headline).toContain('partial')
    expect(cutSetProvenance(computed({ max_order: 3 })).headline).toContain('up to order 3')
  })
})

describe('sequence paths', () => {
  const fes = [{ name: 'RPS' }, { name: 'AFW' }, { name: 'HPI' }, { name: 'RHR' }]

  it('gives one mark per functional event, blank where the path does not ask', () => {
    const marks = sequenceMarks(fes, [
      { functional_event: 'RPS', state: 'success' },
      { functional_event: 'AFW', state: 'failure' },
      { functional_event: 'RHR', state: 'failure' },
    ])
    expect(marks.map(m => m.state)).toEqual(['success', 'failure', 'skip', 'failure'])
    expect(sequencePathText(marks)).toBe('RPS success, AFW failure, RHR failure')
  })

  it('keeps a tool\'s own branch labels and steps outside the columns', () => {
    const marks = sequenceMarks(['A', 'B'], [
      { functional_event: 'A', state: 'true' },
      { functional_event: 'B', state: 'degraded' },
      { functional_event: 'SUBST', state: 'failure' },
    ])
    expect(marks.map(m => [m.name, m.state, m.label])).toEqual([
      ['A', 'success', 'success'], ['B', 'other', 'degraded'], ['SUBST', 'failure', 'failure'],
    ])
    expect(marks[2].extra).toBe(true)
  })

  it('ends in an end state or a transfer', () => {
    expect(sequenceOutcome({ end_state: 'CD' })).toEqual({ kind: 'end_state', label: 'CD' })
    expect(sequenceOutcome({ end_state: null, transfer: 'LOOP-2' })).toEqual({ kind: 'transfer', label: 'LOOP-2' })
    expect(sequenceOutcome({}).kind).toBe('none')
  })
})

describe('opening a model from a citation', () => {
  it('reads the tree a passage is from', () => {
    expect(focusFromPassage('# Fault tree AFW-FT (part 2 of 3)\n\nFault tree AFW-FT — ...')).toEqual({ view: 'fault-trees', name: 'AFW-FT' })
    expect(focusFromPassage('Gate AFW-TRAIN-A (OR) in fault tree AFW — AFW TRAIN A FAILS')).toEqual({ view: 'fault-trees', name: 'AFW' })
    expect(focusFromPassage('Sequence 04 of event tree LOOPGR ends in CD.')).toEqual({ view: 'event-trees', name: 'LOOPGR' })
    expect(focusFromPassage('Basic events of G-PWR')).toBeNull()
  })

  it('round-trips the viewer link', () => {
    const href = praHref({ documentId: 'abc', collectionId: 'my col', focus: { view: 'event-trees', name: 'F&B' } })
    expect(href).toBe('/?pra=abc&collection_id=my+col&et=F%26B')
    expect(parsePraHref(href.slice(1))).toEqual({ documentId: 'abc', collectionId: 'my col', focus: { view: 'event-trees', name: 'F&B' } })
    expect(parsePraHref('?tab=admin')).toBeNull()
  })

  it('targets the hit\'s own collection', () => {
    const target = praTargetFor({
      document_id: 'd1', filename: 'g.zip', source_format: 'pra-mard',
      page_url: '/documents/d1/pdf?collection_id=plant#page=4', text_snippet: '# Event tree TRANS',
    })
    expect(target).toEqual({ documentId: 'd1', collectionId: 'plant', filename: 'g.zip', focus: { view: 'event-trees', name: 'TRANS' } })
    expect(praTargetFor({ document_id: 'd2', source_format: 'pdf' })).toBeNull()
  })
})

describe('fault tree outline state', () => {
  const ev = (name) => ({ kind: 'basic_event', name, probability: 0.1 })
  const tree = () => ({
    kind: 'gate', name: 'TOP', type: 'and', children: [
      { kind: 'gate', name: 'A', type: 'or', children: [ev('E1'), ev('E2'), { kind: 'gate', name: 'DEEP', type: 'or', more: true, input_count: 2 }] },
      { kind: 'gate', name: 'B', type: 'or', children: [ev('E3'), { kind: 'gate', name: 'TOP', type: 'and', children: [ev('E9')] }] },
      { kind: 'transfer', name: 'SUPPORT', tree: 'SUPPORT-FT' },
    ],
  })

  it('opens the first levels and flattens what is open', () => {
    const root = tree()
    const expanded = initialExpansion(root)
    expect([...expanded].sort()).toEqual(['0', '0.0', '0.1'])
    const rows = flattenOutline(root, expanded)
    expect(rows.map(r => r.node.name)).toEqual(['TOP', 'A', 'E1', 'E2', 'DEEP', 'B', 'E3', 'TOP', 'SUPPORT'])
    expect(rows[1]).toMatchObject({ key: '0.0', level: 2, posinset: 1, setsize: 3, parentKey: '0', expanded: true })
    expect(rows[8]).toMatchObject({ key: '0.2', posinset: 3, expandable: false })
  })

  it('stops opening before the outline grows past its row budget', () => {
    const wide = { kind: 'gate', name: 'R', type: 'or', children: Array.from({ length: 5 }, (_, i) => ({
      kind: 'gate', name: `G${i}`, type: 'or', children: Array.from({ length: 50 }, (_, j) => ev(`E${i}-${j}`)),
    })) }
    expect([...initialExpansion(wide, { maxRows: 120 })]).toEqual(['0', '0.0', '0.1'])
  })

  it('does not open a gate that repeats one above it', () => {
    const root = tree()
    const rows = flattenOutline(root, new Set(['0', '0.1', '0.1.1']))
    const again = rows.find(r => r.key === '0.1.1')
    expect(again.cycle).toBe(true)
    expect(again.expandable).toBe(false)
    expect(rows.some(r => r.key === '0.1.1.0')).toBe(false)
    expect(canExpand({ kind: 'gate', name: 'X', cycle: true, children: [ev('E')] })).toBe(false)
  })

  it('grafts lazily loaded children in place', () => {
    const root = tree()
    const deep = nodeAt(root, '0.0.2')
    expect(deep.name).toBe('DEEP')
    expect(flattenOutline(root, new Set(['0', '0.0', '0.0.2'])).find(r => r.key === '0.0.2').needsLoad).toBe(true)
    graft(deep, { name: 'DEEP', children: [ev('E7'), ev('E8')] })
    expect(deep.more).toBeUndefined()
    const rows = flattenOutline(root, new Set(['0', '0.0', '0.0.2']))
    expect(rows.filter(r => r.parentKey === '0.0.2').map(r => r.node.name)).toEqual(['E7', 'E8'])
  })

  it('opens everything loaded, and finds a gate to reveal', () => {
    const root = tree()
    const all = expandAllLoaded(root, new Set())
    expect(all.has('0.1.1')).toBe(false) // the repeated TOP stays closed
    expect(all.has('0.0')).toBe(true)
    expect(findInOutline(root, 'E3')).toEqual({ key: '0.1.0', open: ['0', '0.1'] })
    expect(findInOutline(root, 'NOPE')).toBeNull()
  })

  it('follows the WAI-ARIA tree keys', () => {
    const root = tree()
    const rows = flattenOutline(root, new Set(['0', '0.0']))
    expect(outlineKeyAction(rows, '0', 'ArrowDown')).toEqual({ focus: '0.0' })
    expect(outlineKeyAction(rows, '0.0', 'ArrowRight')).toEqual({ focus: '0.0.0' })
    expect(outlineKeyAction(rows, '0.1', 'ArrowRight')).toEqual({ expand: '0.1' })
    expect(outlineKeyAction(rows, '0.0', 'ArrowLeft')).toEqual({ collapse: '0.0' })
    expect(outlineKeyAction(rows, '0.0.1', 'ArrowLeft')).toEqual({ focus: '0.0' })
    expect(outlineKeyAction(rows, '0.0.1', 'Home')).toEqual({ focus: ROOT_KEY })
    expect(outlineKeyAction(rows, '0.0.1', 'End')).toEqual({ focus: '0.2' })
    expect(outlineKeyAction(rows, '0.0', '*')).toEqual({ expandSiblings: ['0.0', '0.1'] })
    expect(outlineKeyAction(rows, '0.2', 'Enter')).toEqual({ activate: '0.2' })
    expect(outlineKeyAction(rows, '0.2', 'x')).toBeNull()
  })
})
