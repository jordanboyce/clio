/*! Clio static search: <clio-related> element. MIT License. https://github.com/jordanboyce/clio */
// "See also" for a page: passages from other sources that are close in
// meaning to the source the page shows, worked out when the collection was
// exported (include=related). It reads the manifest and one small file; it
// never loads the model, the vectors or the passage files.
//
//   <script type="module" src="/assets/handbook/clio-related.js"></script>
//   <clio-related src="/assets/handbook/search/"></clio-related>
//
// Attributes, all optional:
//   src      the export folder (default: search/ beside this file)
//   source   the source to show related passages for, by its id; without it,
//            the source whose address (or reader page) is this page's
//   label    the heading (default "Related passages"); results  how many (6)
//
// Styling: the shared --clio-* properties (see clio-common.js). Parts:
// label, status, results, result, title, meta, snippet.
// Event: clio-related-ready ({ source, count }).

import { BASE_STYLE, adoptStyles, addition, el, emit, folderOf, loadListed, loadManifest, passageLink } from './clio-common.js'

const STYLE = `
h2 { font-size: 1em; margin: 0 0 .5em; }
ol { list-style: none; margin: 0; padding: 0; }
li { margin: 0 0 .9em; }
.title { font-weight: 600; }
.snippet { margin: .2em 0 0; }
`

const withoutHash = (url) => url.split('#')[0]

/** The manifest's source that this page shows, by its reader page or its own address. */
function sourceOfPage(manifest) {
  const here = withoutHash(document.baseURI)
  for (const doc of manifest.documents || []) {
    if (doc.reader && withoutHash(new URL(doc.reader, manifest.baseUrl).href) === here) return doc.id
  }
  for (const doc of manifest.documents || []) {
    if (doc.link?.url && withoutHash(new URL(doc.link.url, document.baseURI).href) === here) return doc.id
  }
  return null
}

export class ClioRelated extends HTMLElement {
  static get observedAttributes() { return ['src', 'source'] }

  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    this.$label = el('h2', { part: 'label' })
    this.$status = el('p', { part: 'status', className: 'status', role: 'status', 'aria-live': 'polite' })
    this.$list = el('ol', { part: 'results' })
    root.append(this.$label, this.$status, this.$list)
    adoptStyles(root, BASE_STYLE, STYLE)
  }

  connectedCallback() { this.load() }

  attributeChangedCallback() { if (this.isConnected) this.load() }

  async load() {
    const turn = (this.turn = (this.turn || 0) + 1)
    this.$label.textContent = this.getAttribute('label') || 'Related passages'
    this.$status.textContent = 'Loading…'
    this.$list.replaceChildren()
    try {
      const manifest = await loadManifest(folderOf(this, 'src', 'search/', import.meta.url))
      const entry = addition(manifest, 'related')
      if (!entry) throw new Error('This export carries no related passages.')
      const source = this.getAttribute('source') || sourceOfPage(manifest)
      if (!source || !entry.sources?.[source]) throw new Error('This page is not one of the export\'s sources.')
      const data = await loadListed(manifest, entry.sources[source])
      if (turn !== this.turn) return
      const documents = new Map((manifest.documents || []).map((d) => [d.id, d]))
      const shown = data.related.slice(0, Number(this.getAttribute('results')) || 6)
      this.$list.replaceChildren(...shown.map((p) => this.item(p, documents.get(p.document_id), manifest)))
      this.$status.textContent = shown.length ? '' : 'No related passages.'
      this.hidden = false
      emit(this, 'clio-related-ready', { source, count: shown.length })
    } catch (error) {
      if (turn !== this.turn) return
      this.$status.textContent = error.message
      // Nothing to show: take no room on the page.
      this.hidden = true
      emit(this, 'clio-related-ready', { source: null, count: 0, reason: error.message })
    }
  }

  item(passage, document_, manifest) {
    const doc = document_ || {}
    const href = passageLink({ document: doc, passage, index: { baseUrl: manifest.baseUrl } })
    const title = el(href ? 'a' : 'span', { part: 'title', className: 'title', href },
      doc.title || doc.name || passage.document_id)
    const li = el('li', { part: 'result' })
    li.append(title)
    if ((doc.pages || 0) > 1 && passage.page) li.append(el('div', { part: 'meta', className: 'meta' }, `page ${passage.page}`))
    li.append(el('p', { part: 'snippet', className: 'snippet' }, passage.text))
    return li
  }
}

if (!customElements.get('clio-related')) customElements.define('clio-related', ClioRelated)
