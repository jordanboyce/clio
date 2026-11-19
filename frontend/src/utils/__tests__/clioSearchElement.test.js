// <clio-search>, the search box a website drops into its pages for a
// collection exported by Clio (docs/static-search/clio-search.js).
import { afterEach, describe, expect, it, vi } from 'vitest'
import { excerpt, highlight } from '../../../../docs/static-search/clio-search.js'

const passages = [
  { id: 'a1', document_id: 'a', page: 2, text: 'Staff accrue 25 days of holiday a year.' },
  { id: 'b1', document_id: 'b', page: 1, text: 'Expenses are reimbursed within thirty days.' },
]
const manifest = {
  format: 'clio-static-search', version: 2, collection: { name: 'Handbooks' },
  embedding: { model: 'nomic-embed-text', browser: null, probe: null, reason: 'No browser build.' },
  vectors: null,
  passages: { count: 2, files: [{ file: 'passages.json', count: 2 }] },
  documents: [
    { id: 'a', name: 'leave_policy.pdf', title: 'leave policy', pages: 4,
      link: { url: 'https://example.org/leave.pdf', from: 'files', page: '#page={page}' } },
    { id: 'b', name: 'expenses.txt', title: 'expenses', pages: 1 },
  ],
}

function serve() {
  const files = { 'manifest.json': manifest, 'passages.json': passages }
  return vi.fn(async (url) => {
    const name = url.split('?')[0].split('/').pop()
    return name in files ? { ok: true, json: async () => files[name] } : { ok: false, status: 404 }
  })
}

async function mount(attributes) {
  const element = document.createElement('clio-search')
  for (const [name, value] of Object.entries(attributes)) element.setAttribute(name, value)
  const ready = new Promise((resolve) => element.addEventListener('clio-search-ready', resolve, { once: true }))
  document.body.append(element)
  await ready
  return element
}

async function type(element, text) {
  const results = new Promise((resolve) => element.addEventListener('clio-search-results', resolve, { once: true }))
  const input = element.shadowRoot.querySelector('input')
  input.value = text
  input.dispatchEvent(new Event('input'))
  return (await results).detail
}

describe('<clio-search>', () => {
  afterEach(() => {
    document.body.replaceChildren()
    vi.unstubAllGlobals()
  })

  it('searches an export dropped into a page and links each result to the page it is on', async () => {
    const fetchFn = serve()
    vi.stubGlobal('fetch', fetchFn)
    const element = await mount({ src: 'https://example.org/assets/handbook/search/', label: 'Search the handbook' })
    const root = element.shadowRoot
    expect(fetchFn.mock.calls[0][0]).toBe('https://example.org/assets/handbook/search/manifest.json')
    expect(root.querySelector('label').textContent).toBe('Search the handbook')
    expect(root.querySelector('input').disabled).toBe(false)
    expect(root.querySelector('[part=status]').textContent).toBe('2 passages from Handbooks, searched by keyword.')

    const { hits } = await type(element, 'holiday')
    expect(hits).toHaveLength(1)
    const link = root.querySelector('a.title')
    expect(link.textContent).toBe('leave policy')
    expect(link.getAttribute('href')).toBe('https://example.org/leave.pdf#page=2')
    expect(root.querySelector('[part=meta]').textContent).toBe('page 2')
    expect(root.querySelector('mark').textContent).toBe('holiday')

    await type(element, 'reimbursed')
    // A source with no address is shown without a link.
    expect(root.querySelector('a.title')).toBeNull()
    expect(root.querySelector('.title').textContent).toBe('expenses')

    await type(element, 'zebra')
    expect(root.querySelector('[part=status]').textContent).toBe('Nothing matches. Try other words.')
  })

  it('opens results in a new tab when asked, safely', async () => {
    vi.stubGlobal('fetch', serve())
    const element = await mount({ src: 'https://example.org/search/', target: '_blank' })
    await type(element, 'holiday')
    const link = element.shadowRoot.querySelector('a.title')
    expect(link.target).toBe('_blank')
    expect(link.rel).toBe('noopener')
  })

  it('says why when the export cannot be loaded', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 404 })))
    const element = document.createElement('clio-search')
    element.setAttribute('src', 'https://example.org/missing/')
    document.body.append(element)
    await vi.waitFor(() => expect(element.shadowRoot.querySelector('[part=status]').textContent)
      .toContain('Search is not available'))
    expect(element.shadowRoot.querySelector('input').disabled).toBe(true)
  })
})

describe('snippets', () => {
  it('cuts a long passage around the first word asked for', () => {
    const text = 'filler '.repeat(100) + 'the holiday rules ' + 'more '.repeat(100)
    const cut = excerpt(text, 'holiday', 120)
    expect(cut.length).toBeLessThanOrEqual(122)
    expect(cut).toContain('holiday')
    expect(cut.startsWith('…') && cut.endsWith('…')).toBe(true)
  })

  it('marks the words asked for, and never parses the passage as HTML', () => {
    const node = document.createElement('p')
    highlight(node, 'Holiday <img src=x onerror=alert(1)> holiday', 'holiday')
    expect(node.querySelectorAll('mark')).toHaveLength(2)
    expect(node.querySelector('img')).toBeNull()
    expect(node.textContent).toBe('Holiday <img src=x onerror=alert(1)> holiday')
  })
})
