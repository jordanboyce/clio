/*! Clio static search: <clio-search> element. MIT License. https://github.com/jordanboyce/clio */
// A search box to drop into any page of a website, for one or more
// collections exported by Clio (Export, "For a website"). See
// docs/STATIC_SEARCH.md, "On your website".
//
//   <script type="module" src="/assets/handbook/clio-search.js"></script>
//   <clio-search src="/assets/handbook/search/" label="Search the handbook"></clio-search>
//
// Attributes, all optional:
//   src          the export folder(s), space-separated to search several as one
//                (default: search/ beside this file)
//   runtime      the folder holding vendor/ and models/ (default: beside this
//                file); several exports can share one
//   model        auto (the export's own model if it carries one, else the CDN),
//                bundled, cdn, or off (keywords only)
//   load         focus (default: the model downloads when someone first uses
//                the box; keyword results until then) or now
//   index        now (default: the passages and vectors download with the
//                page) or focus (only when someone first uses the box, for a
//                page that shows the box but is mostly read)
//   results      how many results to show (default 8)
//   label, placeholder   the box's label and placeholder text
//   target       where result links open: _self (default) or _blank
//
// Styling: the element uses the page's font and colour. Set these on it to
// match a site: --clio-search-font, --clio-search-color, --clio-search-muted,
// --clio-search-accent, --clio-search-background, --clio-search-border,
// --clio-search-radius, --clio-search-mark. Parts for ::part(): label,
// input, status, results, result, title, meta, snippet.
//
// Events: clio-search-ready ({ semantic, reason }) once the index is loaded
// and again when search by meaning is switched on or refused;
// clio-search-results ({ query, hits }) after each search.

import { combineSearches, createSearch, localTransformers, passageLink, transformersEmbedder, words } from './clio-static-search.js'
import { BASE_STYLE, adoptStyles, emit } from './clio-common.js'

const HERE = new URL('./', import.meta.url)
const CDN_TRANSFORMERS = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@4.3.2'

// Its own properties win; the --clio-* ones every component shares are the fallback.
const STYLE = `
:host { display: block; font: var(--clio-search-font, var(--clio-font, inherit)); color: var(--clio-search-color, var(--clio-color, inherit)); }
label { display: block; margin-bottom: .35em; font-weight: 600; }
input {
  font: inherit; color: inherit; width: 100%; box-sizing: border-box; padding: .55em .75em;
  background: var(--clio-search-background, var(--clio-background, transparent));
  border: 1px solid var(--clio-search-border, var(--clio-border, currentColor)); border-radius: var(--clio-search-radius, var(--clio-radius, .4em));
}
input:focus-visible { outline: 2px solid var(--clio-search-accent, var(--clio-accent, Highlight)); outline-offset: 1px; }
.status { color: var(--clio-search-muted, var(--clio-muted, GrayText)); margin: .4em 0 .8em; }
ol { list-style: none; margin: 0; padding: 0; }
li { margin: 0 0 1em; }
.title { font-weight: 600; color: var(--clio-search-accent, var(--clio-accent, LinkText)); }
a.title { text-decoration: underline; text-underline-offset: .15em; }
.meta { color: var(--clio-search-muted, var(--clio-muted, GrayText)); }
.snippet { margin: .2em 0 0; }
mark { background: var(--clio-search-mark, var(--clio-mark, Mark)); }
`

export class ClioSearch extends HTMLElement {
  static get observedAttributes() { return ['src', 'label', 'placeholder'] }

  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    root.innerHTML = `<label part="label" for="q"></label>
      <input part="input" id="q" type="search" autocomplete="off" disabled>
      <div part="status" class="status" role="status" aria-live="polite"></div>
      <div part="answer" class="answer" hidden></div>
      <ol part="results" aria-label="Results"></ol>`
    adoptStyles(root, BASE_STYLE, STYLE)
    this.$label = root.querySelector('label')
    this.$input = root.querySelector('input')
    this.$status = root.querySelector('.status')
    this.$results = root.querySelector('ol')
    this.$answer = root.querySelector('.answer')
    this.search = null
    this.latest = 0
    // The model starts loading when someone first uses the box: on focus, or
    // on the first input (focus does not fire for autofill, a script, or a
    // window that is not in front).
    this.$input.addEventListener('input', () => {
      if (this.ensureLoaded()) return // the search runs once the index is in
      this.upgrade()
      this.run(this.$input.value)
    })
    this.$input.addEventListener('focus', () => { if (!this.ensureLoaded()) this.upgrade() })
    this.$input.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { this.$input.value = ''; this.run('') }
    })
  }

  connectedCallback() {
    this.render()
    if (this.loading) return
    if (this.getAttribute('index') === 'focus') {
      this.$input.disabled = false
      this.status('Type to search.')
      return
    }
    this.loading = this.load()
  }

  /** With index="focus": start loading on first use. True while it is loading. */
  ensureLoaded() {
    if (this.search) return false
    if (!this.loading) {
      this.loading = this.load().then(() => {
        if (!this.search) return
        this.upgrade()
        if (this.$input.value.trim()) this.run(this.$input.value)
      })
    }
    return true
  }

  attributeChangedCallback(name) {
    if (name === 'src' && this.loading) this.loading = this.load()
    else this.render()
  }

  render() {
    this.$label.textContent = this.getAttribute('label') || 'Search'
    this.$input.placeholder = this.getAttribute('placeholder') || 'Ask a question or type a few words'
  }

  status(text) { this.$status.textContent = text }

  sources() {
    const given = (this.getAttribute('src') || 'search/').split(/\s+/).filter(Boolean)
    return given.map((src) => new URL(src, this.hasAttribute('src') ? document.baseURI : HERE).href)
  }

  runtime() {
    return new URL(this.getAttribute('runtime') || './', this.hasAttribute('runtime') ? document.baseURI : HERE).href
  }

  async load() {
    this.status('Loading the index…')
    // A box that waited for its first use keeps the focus and what was typed.
    const lazy = this.getAttribute('index') === 'focus'
    if (!lazy) this.$input.disabled = true
    try {
      const searches = await Promise.all(this.sources().map((src) => createSearch(src)))
      this.searches = searches
      this.search = searches.length === 1 ? searches[0] : combineSearches(searches)
    } catch (error) {
      this.status(`Search is not available: ${error.message}`)
      return
    }
    this.$input.disabled = false
    this.describe()
    this.emit('clio-search-ready', { semantic: this.search.semantic, reason: this.reason() })
    if ((this.getAttribute('load') || 'focus') === 'now') this.upgrade()
  }

  reason() {
    return (this.searches || []).map((s) => s.reason).filter(Boolean)[0] || ''
  }

  describe() {
    const searches = this.searches || []
    const passages = searches.reduce((n, s) => n + s.index.count, 0).toLocaleString()
    const names = searches.map((s) => s.index.manifest.collection.name).filter(Boolean).join(', ')
    if (this.search.semantic) this.status(`${passages} passages from ${names}, searched by meaning.`)
    else if (this.upgrading) this.status(`${passages} passages from ${names}. Loading the model for search by meaning; searching by keyword until then.`)
    else this.status(`${passages} passages from ${names}, searched by keyword.`)
  }

  /** Switch to search by meaning: load the model each export needs. */
  async upgrade() {
    const mode = this.getAttribute('model') || 'auto'
    if (this.upgradeTried || mode === 'off' || !this.searches) return
    this.upgradeTried = true
    const wanting = this.searches.filter((s) => s.canUseMeaning && !s.semantic)
    if (!wanting.length) return
    this.upgrading = true
    this.describe()
    const runtime = this.runtime()
    let library = null
    const embedderFor = async (manifest) => {
      const bundled = manifest.embedding.browser.bundled
      if (bundled && mode !== 'cdn') {
        library = library || await localTransformers({ base: runtime })
        return transformersEmbedder(manifest, { pipeline: library.pipeline, model: bundled.model })
      }
      if (mode === 'bundled') throw new Error('This export does not carry its model, and model="bundled" forbids the CDN.')
      const { pipeline } = await import(CDN_TRANSFORMERS)
      return transformersEmbedder(manifest, { pipeline })
    }
    // One model load for exports made with the same model.
    const shared = new Map()
    for (const s of wanting) {
      const key = s.index.manifest.embedding.signature || s.index.manifest.embedding.model
      if (!shared.has(key)) {
        shared.set(key, (async () => {
          await s.upgrade(embedderFor)
          return s.embedder
        })())
        await shared.get(key)
      } else {
        const embedder = await shared.get(key)
        if (embedder) await s.upgrade(embedder)
      }
    }
    this.upgrading = false
    this.describe()
    if (!this.search.semantic && this.reason()) this.status(`Searching by keyword. ${this.reason()}`)
    this.emit('clio-search-ready', { semantic: this.search.semantic, reason: this.reason() })
    if (this.$input.value.trim()) this.run(this.$input.value)
  }

  async run(value) {
    const turn = ++this.latest
    const query = value.trim()
    if (!this.search) return
    const k = Number(this.getAttribute('results')) || 8
    const hits = query ? await this.search(query, { k }) : []
    if (turn !== this.latest) return
    const answer = query ? await this.answerFor() : null
    if (turn !== this.latest) return
    this.showAnswer(answer)
    this.$results.replaceChildren(...hits.map((hit) => this.result(hit, query)))
    if (query && !hits.length) this.status('Nothing matches. Try other words.')
    else if (query) this.describe()
    this.emit('clio-search-results', { query, hits })
  }

  /**
   * A published answer (include=answers) whose question is close enough in
   * meaning to the query just searched, or null. Only when searching by
   * meaning: a keyword score cannot be compared with the measured threshold.
   */
  async answerFor() {
    const vector = this.search?.semantic ? this.search.lastVector : null
    const carrying = (this.searches || []).filter((s) => s.index.manifest.additions?.answers)
    if (!vector || !carrying.length) return null
    try {
      if (!this.answers) {
        this.answers = (async () => {
          const { loadAnswers, closestAnswer, answerBlock, ANSWER_STYLE } = await import('./clio-answers.js')
          adoptStyles(this.shadowRoot, ANSWER_STYLE)
          const sets = (await Promise.all(carrying.map((s) => loadAnswers(s.index.manifest)))).filter(Boolean)
          return { sets, closestAnswer, answerBlock }
        })()
      }
      const { sets, closestAnswer, answerBlock } = await this.answers
      const model = this.searches[0].index.manifest.embedding.model
      const found = closestAnswer(sets, vector, model)
      return found ? answerBlock(found.answer, found.set) : null
    } catch {
      return null // the results still show
    }
  }

  showAnswer(block) {
    this.$answer.replaceChildren(...(block ? [block] : []))
    this.$answer.hidden = !block
  }

  result(hit, query) {
    const item = document.createElement('li')
    item.setAttribute('part', 'result')
    const href = passageLink(hit)
    const document_ = hit.document || {}
    const title = document.createElement(href ? 'a' : 'span')
    title.className = 'title'
    title.setAttribute('part', 'title')
    title.textContent = document_.title || document_.name || hit.passage.document_id
    if (href) {
      title.href = href
      const target = this.getAttribute('target')
      if (target) {
        title.target = target
        if (target === '_blank') title.rel = 'noopener'
      }
    }
    const meta = document.createElement('div')
    meta.className = 'meta'
    meta.setAttribute('part', 'meta')
    const where = hit.passage.lines ? `lines ${hit.passage.lines[0]}–${hit.passage.lines[1]}`
      : (document_.pages > 1 && hit.passage.page ? `page ${hit.passage.page}` : '')
    const from = (this.searches || []).length > 1 ? hit.index?.manifest?.collection?.name : ''
    meta.textContent = [from, where].filter(Boolean).join(' · ')
    const snippet = document.createElement('p')
    snippet.className = 'snippet'
    snippet.setAttribute('part', 'snippet')
    highlight(snippet, excerpt(hit.passage.text, query), query)
    item.append(title)
    if (meta.textContent) item.append(meta)
    item.append(snippet)
    return item
  }

  emit(name, detail) { emit(this, name, detail) }
}

/** About `length` characters of a passage, around the first query word in it. */
export function excerpt(text, query, length = 320) {
  const flat = (text || '').replace(/\s+/g, ' ').trim()
  if (flat.length <= length) return flat
  const lower = flat.toLowerCase()
  const at = words(query || '').map((w) => lower.indexOf(w)).filter((i) => i >= 0).sort((a, b) => a - b)[0] ?? 0
  const start = Math.max(0, Math.min(at - Math.floor(length / 3), flat.length - length))
  const cut = flat.slice(start, start + length)
  return (start > 0 ? '…' : '') + cut + (start + length < flat.length ? '…' : '')
}

/** Fill `node` with `text`, the query's words wrapped in <mark>. Text nodes only. */
export function highlight(node, text, query) {
  const terms = [...new Set(words(query || ''))].filter((w) => w.length > 1)
  if (!terms.length) { node.textContent = text; return }
  const escaped = terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`(${escaped.join('|')})`, 'giu')
  for (const piece of text.split(pattern)) {
    if (!piece) continue
    if (terms.includes(piece.toLowerCase())) {
      const mark = document.createElement('mark')
      mark.setAttribute('part', 'mark')
      mark.textContent = piece
      node.append(mark)
    } else {
      node.append(document.createTextNode(piece))
    }
  }
}

if (!customElements.get('clio-search')) customElements.define('clio-search', ClioSearch)
