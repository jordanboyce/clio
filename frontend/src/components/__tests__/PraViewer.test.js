import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => {
  const h = { get: vi.fn(), post: vi.fn() }
  return { default: h, http: h }
})
import { http } from '../../utils/http'
import PraEventTree from '../PraEventTree.vue'
import PraFaultTreeOutline from '../PraFaultTreeOutline.vue'
import PraAnalysis from '../PraAnalysis.vue'
import PraViewer from '../PraViewer.vue'

const be = (name, probability = 1e-3) => ({ kind: 'basic_event', name, description: `${name} fails`, probability })

const MODEL = {
  document_id: 'm1', filename: 'g.zip', source_format: 'pra-mard', name: 'G-PWR', model_format: 'saphire-mard',
  source_files: ['G.FTL'], counts: { fault_trees: 2, gates: 3, basic_events: 4, event_trees: 1, sequences: 3, cut_set_lists: 1 },
  fault_trees: [
    { name: 'AFW-FT', top: 'AFW-FT', description: 'Auxiliary feedwater', gate_count: 3, has_cut_sets: false },
    { name: 'LPI-FT', top: 'LPI-FT', description: 'Low pressure injection', gate_count: 1, has_cut_sets: true },
  ],
  event_trees: [{ name: 'TRANS', initiating_event: 'IE-TRANS', description: 'Transient', functional_events: ['RPS', 'AFW-FT'], sequences: 3 }],
  end_states: [{ name: 'CD', description: 'Core damage' }],
  cut_set_lists: [{ context: 'LPI-FT', kind: 'fault_tree', event_tree: null, cut_sets: 12, source: 'model', truncation: null }],
  warnings: ['Probabilities are SAPHIRE\'s calculated values (\'Calc. Prob\'); CCF groups are not expanded.'],
  notice: 'Screening estimate',
}

const EVENT_TREE = {
  name: 'TRANS', initiating_event: 'IE-TRANS', initiating_event_frequency: 0.8,
  functional_events: [{ name: 'RPS', fault_tree: null, description: 'Reactor trip' }, { name: 'AFW-FT', fault_tree: 'AFW-FT', description: '' }],
  sequences: [
    { name: '01', end_state: 'OK', transfer: null, path: [{ functional_event: 'RPS', state: 'success' }, { functional_event: 'AFW-FT', state: 'success' }], logic: [], frequency: null, has_cut_sets: false },
    { name: '02', end_state: 'CD', transfer: null, path: [{ functional_event: 'RPS', state: 'success' }, { functional_event: 'AFW-FT', state: 'failure' }], logic: [], frequency: null, has_cut_sets: true },
    { name: '03', end_state: null, transfer: 'ATWS', path: [{ functional_event: 'RPS', state: 'failure' }], logic: [], frequency: null, has_cut_sets: false },
  ],
}

let wrapper
beforeEach(() => {
  setActivePinia(createPinia())
  http.get.mockReset()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('PraEventTree', () => {
  it('draws each sequence as marks across the functional events, then where it ends', async () => {
    http.get.mockResolvedValue({ data: EVENT_TREE })
    wrapper = mount(PraEventTree, { props: { collectionId: 'c', documentId: 'm1', name: 'TRANS', faultTrees: new Set(['AFW-FT']) } })
    await flushPromises()
    const rows = wrapper.findAll('tbody tr')
    expect(rows).toHaveLength(3)
    expect(rows[1].findAll('.mark-cell').map(c => c.attributes('data-state'))).toEqual(['success', 'failure'])
    expect(rows[2].findAll('.mark-cell').map(c => c.attributes('data-state'))).toEqual(['failure', 'skip'])
    expect(rows[1].attributes('aria-label')).toBe('Sequence 02: RPS success, AFW-FT failure; ends in CD')

    await rows[1].find('button[aria-label="Cut sets of sequence 02"]').trigger('click')
    expect(wrapper.emitted('open-sequence')[0]).toEqual(['02'])
    await rows[2].find('button[title="Continues in event tree ATWS"]').trigger('click')
    expect(wrapper.emitted('open-event-tree')[0]).toEqual(['ATWS'])

    // Only a functional event with a fault tree is a link.
    expect(wrapper.findAll('thead button.fe-name')).toHaveLength(1)
    await wrapper.find('[role=radiogroup] button:nth-child(2)').trigger('click')
    expect(wrapper.findAll('tbody tr').length).toBeLessThan(3)
  })
})

describe('PraFaultTreeOutline', () => {
  const STRUCTURE = {
    name: 'AFW-FT', top: 'AFW-FT', truncated: false,
    structure: {
      kind: 'gate', name: 'AFW-FT', type: 'atleast', k: 3, description: 'AFW fails', children: [
        be('P-A'), be('P-B'), be('P-C'),
        { kind: 'gate', name: 'DEEP', type: 'or', description: 'Deeper', more: true, input_count: 2 },
        { kind: 'house_event', name: 'H1', state: false },
        { kind: 'transfer', name: 'ACP', tree: 'ACP-FT' },
      ],
    },
  }

  it('is a tree with k-of-n gates, values and transfers, and loads deeper gates on demand', async () => {
    http.get.mockImplementation(async (url, cfg) => {
      if (url.endsWith('/fault-trees/DEEP')) {
        expect(cfg.params.depth).toBe(3)
        return { data: { structure: { kind: 'gate', name: 'DEEP', type: 'or', children: [be('X'), be('Y')] } } }
      }
      return { data: STRUCTURE }
    })
    wrapper = mount(PraFaultTreeOutline, { props: { collectionId: 'c', documentId: 'm1', tree: 'AFW-FT' }, attachTo: document.body })
    await flushPromises()
    const items = wrapper.findAll('[role=treeitem]')
    expect(items).toHaveLength(7)
    expect(items[0].attributes('aria-level')).toBe('1')
    expect(items[0].attributes('aria-expanded')).toBe('true')
    expect(items[0].find('.gate-type').text()).toBe('3 of 6')
    expect(items[1].find('.node-value').text()).toBe('1.00E-3')
    expect(items[5].find('.node-value').text()).toBe('FALSE')
    expect(items[1].attributes('aria-setsize')).toBe('6')

    await items[4].trigger('click')
    await flushPromises()
    const names = wrapper.findAll('[role=treeitem] .node-name').map(n => n.text())
    expect(names).toContain('X')
    expect(wrapper.find('[role=treeitem][aria-level="3"]').exists()).toBe(true)

    await wrapper.findAll('[role=treeitem]').at(-1).find('.link').trigger('click')
    expect(wrapper.emitted('open-tree')[0]).toEqual(['ACP-FT'])
  })
})

describe('PraAnalysis', () => {
  const computed = {
    context: 'AFW-FT', source: 'computed', kind: 'fault_tree', truncation: 1e-13,
    computation: { complete: true, stopped: 'time_budget', requested_truncation: 1e-15, truncation_raised: false,
      max_order: null, max_cut_sets: 50000, time_budget_seconds: 20, elapsed_seconds: 20.1, rows_explored: 944931 },
    totals: { rare_event: 2.67e-5, mcub: 2.67e-5 }, cut_set_count: 2, returned: 2,
    cut_sets: [{ rank: 1, events: ['P-A'], order: 1, probability: 1.6e-5, share: 0.6 }, { rank: 2, events: ['P-B', '/H1'], order: 2, probability: 1e-5, share: 0.4 }],
    events: { 'P-A': { description: 'Pump A', probability: 1.6e-5 } },
    warnings: ['The 20 s time budget ran out: the list is complete down to cutoff 1e-13, not the 1e-15 asked for, so totals are lower bounds.'],
    definitions: {},
  }

  it('offers to compute a tree with no stored list and says how complete the result is', async () => {
    http.get.mockResolvedValue({ data: computed })
    wrapper = mount(PraAnalysis, { props: { collectionId: 'c', documentId: 'm1', context: 'AFW-FT', kind: 'fault_tree', stored: false, tool: 'SAPHIRE' } })
    await flushPromises()
    expect(http.get).not.toHaveBeenCalled()
    await wrapper.find('[data-testid=pra-compute-button]').trigger('click')
    await flushPromises()
    const [, cfg] = http.get.mock.calls[0]
    expect(cfg.params).toMatchObject({ compute: true, truncation: 1e-15 })
    const text = wrapper.find('[data-testid=pra-provenance]').text()
    expect(text).toContain('complete down to 1E-13')
    expect(text).toContain('Totals are lower bounds')
    // The server's own sentence for the same stop is not repeated.
    expect(text).not.toContain('The 20 s time budget ran out')
    expect(wrapper.findAll('[data-testid=pra-notice]')).toHaveLength(1)
    expect(wrapper.emitted('computed')[0][0]).toMatchObject({ truncation: 1e-15 })
  })

  it('says plainly when the server is busy', async () => {
    http.get.mockRejectedValue({ status: 429, message: 'busy', isRateLimit: true })
    wrapper = mount(PraAnalysis, { props: { collectionId: 'c', documentId: 'm1', context: 'AFW-FT', kind: 'fault_tree', stored: false } })
    await flushPromises()
    await wrapper.find('[data-testid=pra-compute-button]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid=pra-analysis-error]').text()).toContain('Two other cut set computations')
  })

  it('asks for a sequence list under its event tree', async () => {
    http.get.mockResolvedValue({ data: { ...computed, source: 'model', computation: undefined, warnings: [] } })
    wrapper = mount(PraAnalysis, { props: { collectionId: 'c', documentId: 'm1', context: '02', eventTree: 'TRANS', kind: 'sequence' } })
    await flushPromises()
    expect(http.get.mock.calls[0][0]).toBe('/api/pra/models/m1/cut-sets/02')
    expect(http.get.mock.calls[0][1].params.event_tree).toBe('TRANS')
    expect(wrapper.find('[data-testid=pra-provenance]').text()).toContain('From the model')
  })
})

describe('PraViewer', () => {
  it('shows the model, its counts and warnings, and opens a tree from the list', async () => {
    http.get.mockImplementation(async (url) => {
      if (url === '/api/pra/models/m1') return { data: MODEL }
      return { data: { name: 'LPI-FT', structure: { kind: 'gate', name: 'LPI-FT', type: 'or', children: [be('V1')] } } }
    })
    wrapper = mount(PraViewer, { props: { documentId: 'm1', collectionId: 'c' }, global: { stubs: { PraEventPanel: true } } })
    await flushPromises()
    expect(wrapper.find('h2').text()).toBe('G-PWR')
    expect(wrapper.find('[data-testid=pra-counts]').text()).toContain('2 fault trees')
    expect(wrapper.find('[data-testid=pra-warnings]').text()).toContain('CCF groups are not expanded')
    const items = wrapper.findAll('[data-testid=pra-list] li button')
    expect(items.map(b => b.text())).toEqual([expect.stringContaining('AFW-FT'), expect.stringContaining('12 cut sets stored')])
    await items[1].trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid=pra-reader] h3').text()).toBe('LPI-FT')
    expect(wrapper.find('[data-testid=pra-outline-tree]').exists()).toBe(true)
  })
})

describe('praApi reads', () => {
  it('wait out a rate limit once, quietly', async () => {
    const { praApi } = await import('../../utils/praApi')
    http.get.mockRejectedValueOnce({ status: 429, isRateLimit: true, retryAfterSeconds: 0.01 })
    http.get.mockResolvedValueOnce({ data: { name: 'ok' } })
    await expect(praApi.model('c', 'm1')).resolves.toEqual({ name: 'ok' })
    expect(http.get.mock.calls[0][1].quiet).toBe(true)
    http.get.mockRejectedValue({ status: 404, message: 'gone' })
    await expect(praApi.event('c', 'm1', 'X')).rejects.toMatchObject({ status: 404 })
    expect(http.get).toHaveBeenCalledTimes(3)
  })
})

describe('opening a PRA source from a search hit', () => {
  it('opens the viewer here, at the tree the passage is from', async () => {
    const { default: SearchResult } = await import('../SearchResult.vue')
    const { useUiStore } = await import('../../stores/uiStore')
    wrapper = mount(SearchResult, { props: { index: 0, result: {
      document_id: 'm1', filename: 'g.zip', source_format: 'pra-mard', page_number: 3,
      page_url: '/documents/m1/pdf?collection_id=plant', pdf_url: '/documents/m1/pdf?collection_id=plant',
      text_snippet: '# Fault tree AFW-FT\n\nFault tree AFW-FT — Auxiliary feedwater.',
    } } })
    const link = wrapper.find('[data-testid=open-pra-model]')
    expect(link.attributes('href')).toBe('/?pra=m1&collection_id=plant&ft=AFW-FT')
    expect(link.attributes('target')).toBeUndefined()
    await link.trigger('click')
    expect(useUiStore().praViewer).toMatchObject({ documentId: 'm1', collectionId: 'plant', focus: { view: 'fault-trees', name: 'AFW-FT' } })
  })
})
