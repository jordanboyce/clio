/*! Clio static search: <clio-table> element. MIT License. https://github.com/jordanboyce/clio */
// A spreadsheet from an exported collection (include=tables), to sort,
// filter and page through, with its rows as a CSV to download. The CSV was
// made when the collection was exported, with every cell a spreadsheet
// would run as a formula turned into text.
//
//   <script type="module" src="/assets/handbook/clio-table.js"></script>
//   <clio-table src="/assets/handbook/search/" table="tables/equipment-register.json"></clio-table>
//
// Attributes: src (the export folder), table (a file the manifest lists
// under additions.tables, or a source's id for its first table), label,
// rows (per page, default 25). Styling: the shared --clio-* properties.
// Parts: label, filter, status, table, header, cell, pages, download.

import { BASE_STYLE, adoptStyles, addition, el, emit, folderOf, join, loadListed, loadManifest } from './clio-common.js'

const STYLE = `
.bar { display: flex; flex-wrap: wrap; gap: .5em 1em; align-items: center; margin: 0 0 .5em; }
input { font: inherit; color: inherit; padding: .35em .5em; background: var(--clio-background, transparent);
  border: 1px solid var(--clio-border, currentColor); border-radius: var(--clio-radius, .4em); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; font-size: .9em; min-width: 100%; }
th, td { text-align: left; padding: .3em .6em; border-bottom: 1px solid var(--clio-border, currentColor); vertical-align: top; }
td.number { text-align: right; font-variant-numeric: tabular-nums; }
th button { font: inherit; font-weight: 600; color: inherit; background: none; border: 0; padding: 0; cursor: pointer; }
.pages { display: flex; gap: .5em; align-items: center; margin-top: .5em; }
.pages button { font: inherit; }
`

const NUMERIC = new Set(['integer', 'int', 'real', 'float', 'number', 'numeric', 'currency', 'percent', 'decimal'])

function compare(a, b, numeric) {
  if (a == null || a === '') return b == null || b === '' ? 0 : 1 // empty cells last either way
  if (b == null || b === '') return -1
  if (numeric) return Number(a) - Number(b)
  // Dates written as 2026-01-12 sort as text; so do names.
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: 'base' })
}

export class ClioTable extends HTMLElement {
  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    this.$label = el('h3', { part: 'label' })
    const bar = el('div', { className: 'bar' })
    this.$filter = el('input', { part: 'filter', type: 'search', 'aria-label': 'Filter the rows', placeholder: 'Filter' })
    this.$status = el('span', { part: 'status', className: 'status', role: 'status', 'aria-live': 'polite' })
    this.$download = el('a', { part: 'download', download: '' }, 'Download as CSV')
    bar.append(this.$filter, this.$status, this.$download)
    this.$table = el('table', { part: 'table' })
    const scroll = el('div', { className: 'scroll' })
    scroll.append(this.$table)
    this.$pages = el('div', { part: 'pages', className: 'pages' })
    root.append(this.$label, bar, scroll, this.$pages)
    adoptStyles(root, BASE_STYLE, STYLE)
    this.sort = { column: -1, ascending: true }
    this.page = 0
    this.$filter.addEventListener('input', () => { this.page = 0; this.draw() })
  }

  async connectedCallback() {
    this.$status.textContent = 'Loading the table…'
    try {
      const manifest = await loadManifest(folderOf(this, 'src', 'search/', import.meta.url))
      const tables = addition(manifest, 'tables')?.tables || []
      const wanted = this.getAttribute('table')
      const entry = tables.find((t) => t.file === wanted) || tables.find((t) => t.source === wanted) || (!wanted && tables[0])
      if (!entry) throw new Error('This export does not carry that table.')
      this.data = await loadListed(manifest, entry.file)
      this.index = new Map(this.data.rows.map((r, i) => [r, i]))
      const listed = Object.values(manifest.additions || {}).flatMap((a) => a.files || []).find((f) => f.file === entry.csv)
      this.$download.href = join(manifest.baseUrl, entry.csv) + (listed ? `?v=${listed.sha256.slice(0, 16)}` : '')
      const doc = (manifest.documents || []).find((d) => d.id === entry.source) || {}
      this.$label.textContent = this.getAttribute('label') || doc.title || doc.name || 'Table'
      this.numeric = this.data.columns.map((c) => NUMERIC.has(String(c.type).toLowerCase()))
      this.draw()
      emit(this, 'clio-table-ready', { rows: this.data.rows.length })
    } catch (error) {
      this.$status.textContent = error.message
    }
  }

  rows() {
    const words = this.$filter.value.toLowerCase().split(/\s+/).filter(Boolean)
    let rows = this.data.rows
    if (words.length) {
      rows = rows.filter((r) => {
        const line = r.map((v) => (v == null ? '' : String(v))).join(' ').toLowerCase()
        return words.every((w) => line.includes(w))
      })
    }
    const { column, ascending } = this.sort
    if (column >= 0) {
      const numeric = this.numeric[column]
      // A column shown as the source wrote it sorts by its typed value.
      const keys = this.data.sort?.[String(column)]
      const key = keys ? (r) => keys[this.index.get(r)] : (r) => r[column]
      rows = [...rows].sort((a, b) => (ascending ? 1 : -1) * compare(key(a), key(b), numeric))
    }
    return rows
  }

  draw() {
    const { columns } = this.data
    const per = Number(this.getAttribute('rows')) || 25
    const rows = this.rows()
    const pages = Math.max(1, Math.ceil(rows.length / per))
    this.page = Math.min(this.page, pages - 1)
    const head = el('tr')
    columns.forEach((c, i) => {
      const sorted = this.sort.column === i
      const th = el('th', { part: 'header', scope: 'col', 'aria-sort': sorted ? (this.sort.ascending ? 'ascending' : 'descending') : 'none' })
      const button = el('button', { type: 'button' }, c.name + (sorted ? (this.sort.ascending ? ' ▲' : ' ▼') : ''))
      button.addEventListener('click', () => {
        this.sort = { column: i, ascending: sorted ? !this.sort.ascending : true }
        this.draw()
      })
      th.append(button)
      head.append(th)
    })
    const thead = el('thead')
    thead.append(head)
    const tbody = el('tbody')
    for (const r of rows.slice(this.page * per, (this.page + 1) * per)) {
      const tr = el('tr')
      r.forEach((v, i) => tr.append(el('td', { part: 'cell', className: this.numeric[i] ? 'number' : null }, v == null ? '' : v)))
      tbody.append(tr)
    }
    this.$table.replaceChildren(thead, tbody)
    const total = this.data.rows.length
    this.$status.textContent = rows.length === total ? `${total.toLocaleString()} rows` : `${rows.length.toLocaleString()} of ${total.toLocaleString()} rows`
    const prev = el('button', { type: 'button', disabled: this.page === 0 }, 'Previous')
    const next = el('button', { type: 'button', disabled: this.page >= pages - 1 }, 'Next')
    prev.addEventListener('click', () => { this.page -= 1; this.draw() })
    next.addEventListener('click', () => { this.page += 1; this.draw() })
    this.$pages.replaceChildren(...(pages > 1 ? [prev, el('span', {}, `Page ${this.page + 1} of ${pages}`), next] : []))
  }
}

if (!customElements.get('clio-table')) customElements.define('clio-table', ClioTable)
