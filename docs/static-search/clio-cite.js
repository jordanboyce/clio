/*! Clio static search: <clio-cite> element. MIT License. https://github.com/jordanboyce/clio */
// A passage of an exported collection, quoted in an article with its
// source's title, page and link.
//
//   <script type="module" src="/assets/handbook/clio-cite.js"></script>
//   <clio-cite src="/assets/handbook/search/" passage="d5a1…_3_0"></clio-cite>
//
// Attributes: passage (its id: the `id` in passages.json, or what a reader
// page's anchor names), src (the export folder, default search/ beside this
// file), words (quote only the first N words). The passage files are read
// once for a page however many quotations it has. A passage the export does
// not hold says so on the page rather than showing nothing.
// Styling: the shared --clio-* properties. Parts: quote, caption, title, meta, status.

import { BASE_STYLE, adoptStyles, el, folderOf, loadListed, loadManifest, passageLink } from './clio-common.js'

const STYLE = `
figure { margin: 0 0 1em; }
blockquote { margin: 0; padding: .1em 0 .1em 1em; border-left: 3px solid var(--clio-border, currentColor); }
figcaption { font-size: .85em; color: var(--clio-muted, GrayText); margin-top: .35em; }
.status { color: var(--clio-muted, GrayText); }
`

// One manifest and one copy of each passage file per export folder, shared by
// every <clio-cite> on the page.
const manifests = new Map()
const files = new Map()

function manifestFor(folder) {
  if (!manifests.has(folder)) manifests.set(folder, loadManifest(folder))
  return manifests.get(folder)
}

function fileFor(manifest, entry) {
  const key = manifest.baseUrl + entry.file + entry.sha256
  if (!files.has(key)) {
    files.set(key, loadListed(manifest, entry.file).then((rows) => new Map(rows.map((p) => [p.id, p]))))
  }
  return files.get(key)
}

/** The passage with this id, read from the export's passage files in order, each at most once. */
export async function findPassage(manifest, id) {
  for (const entry of manifest.passages?.files || []) {
    const found = (await fileFor(manifest, entry)).get(id)
    if (found) return found
  }
  return null
}

export class ClioCite extends HTMLElement {
  static get observedAttributes() { return ['passage', 'src', 'words'] }

  constructor() {
    super()
    this.attachShadow({ mode: 'open' })
    adoptStyles(this.shadowRoot, BASE_STYLE, STYLE)
  }

  connectedCallback() { this.load() }

  attributeChangedCallback() { if (this.isConnected) this.load() }

  async load() {
    const turn = (this.turn = (this.turn || 0) + 1)
    const id = (this.getAttribute('passage') || '').replace(/^#?p-/, '')
    const status = el('span', { part: 'status', className: 'status', role: 'status' }, 'Loading the quotation…')
    this.shadowRoot.replaceChildren(status)
    try {
      const manifest = await manifestFor(folderOf(this, 'src', 'search/', import.meta.url))
      const passage = id ? await findPassage(manifest, id) : null
      if (turn !== this.turn) return
      if (!passage) {
        status.textContent = `Passage ${id || '(none named)'} is not in this export.`
        return
      }
      const doc = (manifest.documents || []).find((d) => d.id === passage.document_id) || {}
      const words = Number(this.getAttribute('words')) || 0
      let text = (passage.text || '').replace(/\s+/g, ' ').trim()
      if (words) {
        const cut = text.split(' ')
        if (cut.length > words) text = cut.slice(0, words).join(' ') + '…'
      }
      const href = passageLink({ document: doc, passage, index: { baseUrl: manifest.baseUrl } })
      const figure = el('figure')
      figure.append(el('blockquote', { part: 'quote', cite: href }, text))
      const caption = el('figcaption', { part: 'caption' })
      caption.append(el(href ? 'a' : 'cite', { part: 'title', href }, doc.title || doc.name || passage.document_id))
      const where = passage.lines ? `lines ${passage.lines[0]}–${passage.lines[1]}`
        : ((doc.pages || 0) > 1 && passage.page ? `page ${passage.page}` : '')
      if (where) caption.append(el('span', { part: 'meta', className: 'meta' }, `, ${where}`))
      figure.append(caption)
      this.shadowRoot.replaceChildren(figure)
    } catch (error) {
      if (turn === this.turn) status.textContent = `The quotation could not be loaded: ${error.message}`
    }
  }
}

if (!customElements.get('clio-cite')) customElements.define('clio-cite', ClioCite)
