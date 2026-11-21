/*! Clio static search: <clio-answers> element. MIT License. https://github.com/jordanboyce/clio */
// Answers written in advance and published with the export (include=answers),
// each citing the passages it rests on. <clio-answers> lists them; the search
// box (clio-search.js) shows one above its results when a query is close
// enough in meaning to its question. Nothing is generated in the browser.
//
//   <script type="module" src="/assets/handbook/clio-answers.js"></script>
//   <clio-answers src="/assets/handbook/search/" label="Common questions"></clio-answers>
//
// Attributes: src (the export folder, default search/ beside this file), label.
// Styling: the shared --clio-* properties. Parts: label, status, results,
// result, question, answer, citation, note.

import { BASE_STYLE, adoptStyles, addition, el, emit, folderOf, loadListed, loadManifest, passageLink } from './clio-common.js'

export const ANSWER_STYLE = `
.answer-block { border: 1px solid var(--clio-border, currentColor); border-radius: var(--clio-radius, .4em);
  padding: .7em .9em; margin: 0 0 1em; }
.question { font-weight: 600; margin: 0 0 .3em; }
.answer-text { margin: 0 0 .4em; white-space: pre-line; }
.note { font-size: .8em; color: var(--clio-muted, GrayText); margin: 0; }
sup a { text-decoration: none; }
details { margin: 0 0 .6em; }
summary { cursor: pointer; font-weight: 600; }
`

const CITATION = /\[Source\s+(\d+)\]/gi

/** The answers file of an export, or null when it carries none. */
export async function loadAnswers(manifest) {
  const entry = addition(manifest, 'answers')
  if (!entry) return null
  const data = await loadListed(manifest, entry.file)
  data.manifest = manifest
  return data
}

const unit = (v) => {
  let n = 0
  for (const x of v) n += x * x
  n = Math.sqrt(n) || 1
  return Array.from(v, (x) => x / n)
}

/**
 * The published answer whose question is closest to a query vector, when it
 * is at least the export's measured threshold; else null. Only for the model
 * the answers were embedded with.
 */
export function closestAnswer(sets, vector, model) {
  if (!vector) return null
  const q = unit(vector)
  let best = null
  for (const set of sets) {
    if (!set?.threshold || (model && set.model !== model)) continue
    for (const answer of set.answers) {
      if (!answer.vector || answer.vector.length !== q.length) continue
      let score = 0
      for (let i = 0; i < q.length; i++) score += q[i] * answer.vector[i]
      if (score >= set.threshold && (!best || score > best.score)) best = { answer, set, score }
    }
  }
  return best
}

/** One answer as elements: the question, the text with its citations as links, and what it is. */
export function answerBlock(answer, set, { heading = 'p' } = {}) {
  const manifest = set.manifest
  const documents = new Map((manifest.documents || []).map((d) => [d.id, d]))
  const block = el('div', { part: 'result', className: 'answer-block' })
  block.append(el(heading, { part: 'question', className: 'question' }, answer.question))
  const text = el('p', { part: 'answer', className: 'answer-text' })
  let at = 0
  for (const match of answer.answer.matchAll(CITATION)) {
    text.append(document.createTextNode(answer.answer.slice(at, match.index)))
    const cite = answer.citations.find((c) => c.n === Number(match[1]))
    const doc = cite ? documents.get(cite.document_id) || {} : {}
    const href = cite ? passageLink({ document: doc, passage: cite, index: { baseUrl: manifest.baseUrl } }) : null
    const sup = el('sup')
    const label = `[${match[1]}]`
    sup.append(href ? el('a', { part: 'citation', href, title: doc.title || doc.name || '' }, label) : el('span', {}, label))
    text.append(sup)
    at = match.index + match[0].length
  }
  text.append(document.createTextNode(answer.answer.slice(at)))
  block.append(text)
  const when = answer.written ? ` on ${answer.written}` : ''
  const from = answer.sources?.length ? ` from ${answer.sources.join(', ')}` : ''
  block.append(el('p', { part: 'note', className: 'note' }, `${set.label || 'Written in advance.'} Written${when}${from}.`))
  return block
}

export class ClioAnswers extends HTMLElement {
  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    this.$label = el('h2', { part: 'label' })
    this.$status = el('p', { part: 'status', className: 'status', role: 'status' })
    this.$list = el('div', { part: 'results' })
    root.append(this.$label, this.$status, this.$list)
    adoptStyles(root, BASE_STYLE, ANSWER_STYLE)
  }

  async connectedCallback() {
    this.$label.textContent = this.getAttribute('label') || 'Common questions'
    this.$status.textContent = 'Loading…'
    try {
      const set = await loadAnswers(await loadManifest(folderOf(this, 'src', 'search/', import.meta.url)))
      if (!set?.answers.length) throw new Error('This export carries no answers.')
      this.$list.replaceChildren(...set.answers.map((answer) => {
        const item = el('details')
        item.append(el('summary', { part: 'question' }, answer.question))
        const block = answerBlock(answer, set)
        block.querySelector('.question').remove()
        item.append(block)
        return item
      }))
      this.$status.textContent = ''
      emit(this, 'clio-answers-ready', { count: set.answers.length })
    } catch (error) {
      this.$status.textContent = error.message
      this.hidden = true
    }
  }
}

if (!customElements.get('clio-answers')) customElements.define('clio-answers', ClioAnswers)
