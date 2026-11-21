// A second component, to show what clio-common.js leaves a component to write:
// <clio-count src="search/"> says how many passages an export holds.
import { BASE_STYLE, adoptStyles, el, emit, folderOf, loadManifest } from '/clio-common.js'

class ClioCount extends HTMLElement {
  async connectedCallback() {
    const root = this.shadowRoot || this.attachShadow({ mode: 'open' })
    const status = el('p', { part: 'status', className: 'status', role: 'status' }, 'Loading…')
    root.replaceChildren(status)
    adoptStyles(root, BASE_STYLE)
    try {
      const manifest = await loadManifest(folderOf(this, 'src', 'search/', import.meta.url))
      status.textContent = `${manifest.passages.count} passages from ${manifest.collection.name}`
      emit(this, 'clio-count-ready', { count: manifest.passages.count })
    } catch (error) {
      status.textContent = `Not available: ${error.message}`
    }
  }
}

customElements.define('clio-count', ClioCount)
