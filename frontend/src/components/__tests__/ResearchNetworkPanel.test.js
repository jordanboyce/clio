import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ResearchNetworkPanel from '../ResearchNetworkPanel.vue'

const NOTE = '16 nodes and 17 links from 10 sources is too small for centrality to mean much: one added or removed link can reorder the top.'
const definitions = {
  betweenness: 'How often this work lies on the shortest chain of citations between two others.',
  constraint: "Burt's constraint: lower means more of a broker.",
  community: 'A group found by Louvain.',
}
const longLabel = 'Languagemodelsasknowledgebases? arXivpreprintarXiv: 01066, URLhttps://arxiv.org/abs/ 01066'

const citation = (over = {}) => ({
  graph: 'citation',
  stats: { nodes: 16, edges: 17, betweenness_approximate: false },
  reliability: { level: 'too_small', reliable: false, note: NOTE },
  definitions,
  brokers: [{
    id: 'p1', label: 'Dense Passage Retrieval for Open-Domain Question Answering', betweenness: 0.347619, constraint: 0.166667,
    community: 0, document_id: 'p1', connects: [0, 1],
    sentence: 'Connects Retrieval and Retrieval-Augmented: cites 3 works in Retrieval and 1 in Retrieval-Augmented',
    bridges: [
      {
        community: 0, label: 'Retrieval', count: 25, own: true, cites: 1, cited_by: 1,
        edges: [
          { node: 'x1', label: longLabel, cites: { reason: 'same arXiv id', reference_id: 24 } },
          { node: 'p2', label: 'REALM', cited_by: { reason: 'title printed in the reference', reference_id: 3 } },
        ],
      },
      {
        community: 1, label: 'Retrieval-Augmented', count: 1, own: false, cites: 1,
        edges: [{ node: 'p3', label: 'RAG', cites: { reason: 'same DOI', reference_id: 5 } }],
      },
    ],
  }],
  ...over,
})

const authors = () => ({
  graph: 'authors',
  stats: { nodes: 53, edges: 90, capped: { limit: 200, kept: 200, dropped: 40 } },
  reliability: { level: 'too_small', reliable: false, note: 'Too few authors to rank.' },
  definitions,
  key_nodes: Array.from({ length: 7 }, (_, i) => ({
    id: `au${i}`, label: `Author ${i}`, betweenness: 0.1, is_broker: false, community: 0,
    sentence: `Appears in ${i + 1} sources`,
    sources: [{ document_id: `doc-${i}`, title: `Paper ${i}` }], source_count: i === 0 ? 4 : 1,
    bridges: i === 0 ? [{
      community: 1, label: 'Group B', count: 3, own: false,
      edges: [{ node: 'au9', label: 'Mira Okafor', weight: 2, sources: [{ document_id: 'doc-0', title: 'Shared Paper' }] }],
    }] : [],
  })),
  merges: [{ name: 'J. Smith', variants: ['J Smith', 'Smith, J.'], reason: 'same surname and initial' }],
  ambiguous: [{ names: ['A. Lee', 'Anna Lee'], reason: 'initial matches two people' }],
  unparsed_authors: ['et al.', '???'],
})

const entities = () => ({
  graph: 'entities', stats: { nodes: 158 }, reliability: { reliable: true, note: '' }, definitions,
  key_nodes: [{
    id: 'e1', label: 'Open Domain', sentence: 'Appears in 3 sources',
    sources: [{ document_id: 'd1', title: 'DPR' }], source_count: 3, bridges: [],
  }],
})

const mountPanel = (props = {}) => mount(ResearchNetworkPanel, { props: { citation: citation(), nodeById: {}, ...props } })

describe('ResearchNetworkPanel', () => {
  it('shows the server note verbatim when unreliable, and not when reliable', () => {
    const w = mountPanel()
    const note = w.get('[data-testid="network-reliability"]')
    expect(note.attributes('role')).toBe('note')
    expect(note.text()).toBe(NOTE)
    const ok = mountPanel({ citation: citation({ reliability: { level: 'ok', reliable: true, note: 'fine' } }) })
    expect(ok.find('[data-testid="network-reliability"]').exists()).toBe(false)
  })

  it('emits focus with the paper id when a broker row is clicked', async () => {
    const w = mountPanel()
    const row = w.get('[data-testid="network-key-papers"] li button.node-row')
    expect(row.attributes('title')).toContain('Dense Passage Retrieval')
    await row.trigger('click')
    expect(w.emitted('focus')[0]).toEqual(['p1'])
    expect(w.text()).toContain('Connects Retrieval and Retrieval-Augmented')
    expect(w.text()).toContain(definitions.betweenness)
  })

  it('expands the links with direction words, reasons and definitions, and focuses a neighbour', async () => {
    const w = mountPanel()
    expect(w.find('[data-testid="network-broker-links"]').exists()).toBe(false)
    const toggle = w.get('button[aria-expanded]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    const links = w.get('[data-testid="network-broker-links"]')
    expect(links.text()).toContain('cited by this · same arXiv id')
    expect(links.text()).toContain('cites this · title printed in the reference')
    expect(links.text()).toContain(definitions.betweenness)
    expect(links.text()).toContain(definitions.constraint)
    const edge = links.findAll('button.node-row').find(b => b.attributes('title') === 'REALM')
    await edge.trigger('click')
    expect(w.emitted('focus').at(-1)).toEqual(['p2'])
  })

  it('says "and N more" when the true count exceeds the edges sent', async () => {
    const w = mountPanel()
    await w.get('button[aria-expanded]').trigger('click')
    expect(w.get('[data-testid="network-broker-links"]').text()).toContain('and 23 more')
  })

  it("keeps each row's expanded state separate", async () => {
    const c = citation()
    c.brokers.push({ ...c.brokers[0], id: 'p9', label: 'Second', bridges: [] })
    const w = mountPanel({ citation: c })
    const toggles = w.findAll('[data-testid="network-key-papers"] button[aria-expanded]')
    await toggles[1].trigger('click')
    expect(toggles[0].attributes('aria-expanded')).toBe('false')
    expect(toggles[1].attributes('aria-expanded')).toBe('true')
  })

  it('lists the first five key papers and shows the rest on request', async () => {
    const brokers = Array.from({ length: 7 }, (_, i) => ({
      id: `p${i}`, label: `Paper ${i}`, betweenness: 0.5, constraint: 0.2, sentence: 'Connects A and B: cited by 1 paper in each', bridges: [],
    }))
    const wrapper = mountPanel({ citation: citation({ brokers }) })
    expect(wrapper.findAll('[data-testid=network-broker]')).toHaveLength(5)
    await wrapper.findAll('button').find(b => b.text() === 'Show all 7').trigger('click')
    expect(wrapper.findAll('[data-testid=network-broker]')).toHaveLength(7)
  })

  it('shows a plain message when no paper links separate groups', () => {
    const w = mountPanel({ citation: citation({ brokers: [] }) })
    expect(w.get('[data-testid="network-key-papers"]').text()).toContain('No paper here links separate groups.')
  })

  it('shows only the note for an empty graph', () => {
    const w = mountPanel({ citation: citation({ brokers: [], stats: { nodes: 0, edges: 0 } }) })
    expect(w.find('[data-testid="network-key-papers"]').exists()).toBe(false)
    expect(w.find('[data-testid="network-reliability"]').exists()).toBe(true)
  })

  it('renders as-cited labels in italics', () => {
    const w = mountPanel({ nodeById: { p1: { id: 'p1', type: 'external' } } })
    expect(w.get('[data-testid="network-key-papers"] .node-row span').classes()).toContain('italic')
  })

  it("opens a source from a person's evidence and never focuses the map", async () => {
    const w = mountPanel({ authors: authors() })
    const person = w.get('[data-testid="network-key-people"]')
    await person.get('button[aria-expanded]').trigger('click')
    const evidence = person.get('[data-testid="network-evidence"]')
    await evidence.get('button.node-row').trigger('click')
    expect(w.emitted('open')[0]).toEqual(['doc-0'])
    expect(w.emitted('focus')).toBeUndefined()
    expect(evidence.text()).toContain('and 3 more')
    expect(evidence.text()).toContain('Mira Okafor')
    expect(evidence.text()).toContain('Shared Paper')
  })

  it('lists the first five people and shows the rest on request', async () => {
    const w = mountPanel({ authors: authors() })
    const people = w.get('[data-testid="network-key-people"]')
    expect(people.findAll('[data-testid="network-key-node"]')).toHaveLength(5)
    await people.get('button.btn-ghost').trigger('click')
    expect(people.findAll('[data-testid="network-key-node"]')).toHaveLength(7)
  })

  it('lists merged names with their variants, ambiguous names and unreadable strings', async () => {
    const w = mountPanel({ authors: authors() })
    const care = w.get('[data-testid="network-name-care"]')
    expect(care.text()).toContain('1 name merged')
    expect(care.text()).toContain('1 left apart')
    expect(care.text()).toContain('2 author strings could not be read')
    expect(care.text()).not.toContain('Smith, J.')
    await care.findAll('button')[0].trigger('click')
    expect(care.text()).toContain('J Smith, Smith, J.')
    expect(care.text()).toContain('same surname and initial')
    await care.findAll('button')[1].trigger('click')
    expect(care.text()).toContain('A. Lee, Anna Lee')
  })

  it('states the cap, the people note, and the concept heuristic', () => {
    const w = mountPanel({ authors: authors(), entities: entities() })
    expect(w.get('[data-testid="network-key-people"]').text()).toContain('Showing the 200 most frequent of 240')
    expect(w.get('[data-testid="network-key-people"]').text()).toContain('Too few authors to rank.')
    const concepts = w.get('[data-testid="network-key-concepts"]').text()
    expect(concepts).toContain('capitalised phrases found by a heuristic')
    expect(concepts).not.toContain('Too few')
  })

  it('renders each section alone when asked', () => {
    const w = mountPanel({ authors: authors(), entities: entities(), section: 'concepts' })
    expect(w.find('[data-testid="network-key-concepts"]').exists()).toBe(true)
    expect(w.find('[data-testid="network-key-people"]').exists()).toBe(false)
    expect(w.find('[data-testid="network-key-papers"]').exists()).toBe(false)
    expect(w.find('[data-testid="network-reliability"]').exists()).toBe(false)
  })

  it('renders nothing and does not fail when every network is null', () => {
    const w = mount(ResearchNetworkPanel, { props: { citation: null, authors: null, entities: null, nodeById: {} } })
    expect(w.find('[data-testid="network-key-papers"]').exists()).toBe(false)
    expect(w.find('[data-testid="network-key-people"]').exists()).toBe(false)
    expect(w.text()).toBe('')
    expect(() => mount(ResearchNetworkPanel)).not.toThrow()
  })
})
