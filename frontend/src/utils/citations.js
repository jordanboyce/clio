import { ref, watch } from 'vue'

// Citation helpers shared by Research, the source panel and Ask. The server
// builds every citation (one implementation of the style rules, from the
// source's own record); this file only chooses the style, copies, downloads
// and splices citations into an answer.

export const CITATION_STYLES = [
  { id: 'apa', label: 'APA 7' },
  { id: 'chicago', label: 'Chicago (author-date)' },
  { id: 'mla', label: 'MLA 9' },
  { id: 'ieee', label: 'IEEE' },
  { id: 'harvard', label: 'Harvard' },
]

export const EXPORT_FORMATS = [
  { id: 'bibtex', label: 'BibTeX (.bib)', hint: 'LaTeX, JabRef, Overleaf' },
  { id: 'ris', label: 'RIS (.ris)', hint: 'EndNote, Zotero, Mendeley' },
  { id: 'csl-json', label: 'CSL-JSON (.json)', hint: 'Zotero, Pandoc' },
]

// What a missing field is called on screen.
export const FIELD_LABELS = {
  title: 'title', authors: 'authors', year: 'year', venue: 'journal', publisher: 'publisher',
  container_title: 'book title',
}

const STYLE_KEY = 'clio.research.citationStyle'

function readStyle() {
  try {
    const value = window.localStorage.getItem(STYLE_KEY)
    return CITATION_STYLES.some(s => s.id === value) ? value : 'apa'
  } catch { return 'apa' }
}

// One style for the whole app, remembered in this browser.
const sharedStyle = ref(typeof window === 'undefined' ? 'apa' : readStyle())
watch(sharedStyle, (value) => {
  try { window.localStorage.setItem(STYLE_KEY, value) } catch { /* private window */ }
})

export function useCitationStyle() {
  return sharedStyle
}

export function styleLabel(id) {
  return CITATION_STYLES.find(s => s.id === id)?.label || id
}

// Copy with formatting where the browser allows it (italic titles survive
// a paste into Word or Google Docs), plain text otherwise.
export async function copyRich(text, html) {
  if (html && typeof window !== 'undefined' && window.ClipboardItem && navigator.clipboard?.write) {
    try {
      await navigator.clipboard.write([new window.ClipboardItem({
        'text/plain': new Blob([text], { type: 'text/plain' }),
        'text/html': new Blob([html], { type: 'text/html' }),
      })])
      return
    } catch { /* fall through to plain text */ }
  }
  await navigator.clipboard.writeText(text)
}

export function downloadText(content, filename, mediaType = 'text/plain') {
  const url = URL.createObjectURL(new Blob([content], { type: `${mediaType};charset=utf-8` }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// The collection a chat or search source came from: its page link carries it.
export function sourceCollectionId(source) {
  try {
    const url = new URL(source.page_url || source.pdf_url, 'http://x')
    return url.searchParams.get('collection_id')
  } catch { return null }
}

export function missingLabel(missing = []) {
  return missing.map(f => FIELD_LABELS[f] || f.replace('_', ' ')).join(', ')
}

const escapeHtml = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

// An answer with its [Source N] markers replaced by real citations, and a
// reference list in the chosen style. `cited` is the server's reply for the
// answer's sources, in order: cited.passages[N-1] belongs to [Source N].
// Repeats of one citation in a row ("[Source 1] [Source 2]" on one page)
// collapse to one.
export function answerWithCitations(content, cited) {
  const passages = cited?.passages || []
  const replace = (text) => String(text || '').replace(
    /(?:\s*\[Source\s+(\d+)\])+/g,
    (group) => {
      const numbers = [...group.matchAll(/\[Source\s+(\d+)\]/g)].map(m => Number(m[1]))
      const labels = [...new Set(numbers.map(n => passages[n - 1]?.in_text).filter(Boolean))]
      if (!labels.length) return group
      return ' ' + mergeInText(labels, cited.style)
    },
  )
  const body = replace(content).replace(/ +([.,;:])/g, '$1')
  const heading = cited.style === 'mla' ? 'Works Cited' : 'References'
  const text = `${body}\n\n${heading}\n\n${(cited.bibliography || []).join('\n\n')}`
  const html = `${escapeHtml(body).replace(/\n/g, '<br>')}<h3>${heading}</h3>`
    + (cited.bibliography_html || []).map(e => `<p>${e}</p>`).join('')
  return { text, html }
}

// "(Okafor, 2021, p. 4) (Hale, 2018)" -> "(Okafor, 2021, p. 4; Hale, 2018)";
// IEEE "[1] [2, p. 3]" stays as written.
export function mergeInText(labels, style) {
  if (labels.length < 2 || style === 'ieee') return labels.join(style === 'ieee' ? ', ' : ' ')
  return '(' + labels.map(l => l.replace(/^\(|\)$/g, '')).join('; ') + ')'
}

// Citations for an answer's sources, aligned with them: result.passages[i]
// cites sources[i]. Sources from several collections are cited per
// collection and their reference lists joined.
export async function citeSources(sources, style, fallbackCollectionId, api) {
  const groups = new Map()
  sources.forEach((source, index) => {
    const cid = sourceCollectionId(source) || fallbackCollectionId
    if (!cid || !source.document_id) return
    if (!groups.has(cid)) groups.set(cid, [])
    groups.get(cid).push({ index, document_id: source.document_id, page: source.page_number || null })
  })
  const passages = new Array(sources.length).fill(null)
  const bibliography = []
  const bibliographyHtml = []
  for (const [cid, rows] of groups) {
    const out = await api.citations(cid, { passages: rows.map(({ document_id, page }) => ({ document_id, page })), style })
    rows.forEach((row, i) => { passages[row.index] = out.passages[i] })
    bibliography.push(...out.bibliography)
    bibliographyHtml.push(...out.bibliography_html)
  }
  return { style, passages, bibliography, bibliography_html: bibliographyHtml }
}
