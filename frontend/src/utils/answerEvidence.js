import { marked } from 'marked'
import DOMPurify from 'dompurify'

// Source links stay on this deployment, even when history contains an old host.
export function sourceHref(source) {
  try {
    const url = new URL(source.page_url || source.pdf_url, window.location.origin)
    const expected = `/documents/${encodeURIComponent(source.document_id)}/pdf`
    if (url.pathname !== expected || !['http:', 'https:'].includes(url.protocol)) return ''
    return `${url.pathname}${url.search}${url.hash}`
  } catch { return '' }
}

// The thumbnail of the cited page, on this deployment. Empty when the server
// sent none (formats with no page rendering) or the URL is not the preview
// endpoint for this document.
export function previewHref(source) {
  if (!source?.preview_url) return ''
  try {
    const url = new URL(source.preview_url, window.location.origin)
    const expected = `/documents/${encodeURIComponent(source.document_id)}/preview`
    if (url.pathname !== expected || !['http:', 'https:'].includes(url.protocol)) return ''
    return `${url.pathname}${url.search}`
  } catch { return '' }
}

// Alt text for a page thumbnail: what the picture is of, for a reader who
// cannot see it. The passage itself is read out next to it.
export function previewAlt(source) {
  const page = source?.page_number
  return page && source?.source_format === 'pdf'
    ? `Page ${page} of ${source.filename}`
    : `Preview of ${source?.filename || 'the source'}`
}

// A bracket that starts with "Source N" and may continue with more sources or
// a restated location. Bare numbers in brackets ("[2]") are left alone: they
// are too ambiguous to turn into evidence links.
const CITATION_GROUP = /\[Source\s+\d+(?:\s*[,;:](?:\s*Source)?[^\]]*)?\]/gi

export function renderEvidenceMarkdown(text, sources = []) {
  const root = document.createElement('div')
  root.innerHTML = DOMPurify.sanitize(marked.parse(String(text || ''), { gfm: true, breaks: true }), {
    FORBID_TAGS: ['button', 'input', 'form', 'textarea', 'select'],
    ALLOW_DATA_ATTR: false,
  })
  // Generate controls only from prose text nodes; never rewrite HTML attributes,
  // code samples, or links supplied by the model.
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const nodes = []
  while (walker.nextNode()) nodes.push(walker.currentNode)
  for (const node of nodes) {
    if (node.parentElement.closest('a, code, pre, button')) continue
    const fragment = document.createDocumentFragment()
    let end = 0
    for (const match of node.textContent.matchAll(CITATION_GROUP)) {
      fragment.append(node.textContent.slice(end, match.index))
      // One bracket can carry several citations ("[Source 2, Source 3]") or a
      // restated location ("[Source 2: file.pdf, page 56]"); each valid number
      // becomes its own compact control and the model's wording stays in the label.
      const numbers = [...match[0].matchAll(/Source\s+(\d+)/gi)].map(m => Number(m[1]))
      const valid = numbers.filter(number => number > 0 && number <= sources.length)
      if (valid.length && valid.length === numbers.length) {
        valid.forEach((number, i) => {
          if (i) fragment.append(' ')
          const button = document.createElement('button')
          button.type = 'button'
          button.className = 'link link-primary font-medium px-1 rounded-sm'
          button.dataset.sourceNumber = String(number)
          button.setAttribute('aria-label', `Review source ${number}: ${sources[number - 1].filename}`)
          if (numbers.length === 1 && match[0] !== `[Source ${number}]`) button.title = match[0]
          button.textContent = `[Source ${number}]`
          fragment.append(button)
        })
      } else {
        fragment.append(match[0])
      }
      end = match.index + match[0].length
    }
    if (end) {
      fragment.append(node.textContent.slice(end))
      node.replaceWith(fragment)
    }
  }
  for (const link of root.querySelectorAll('a')) {
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  }
  return root.innerHTML
}

export function answerWithReferences(message) {
  const references = (message.sources || []).map((source, index) => {
    const href = sourceHref(source)
    const location = source.page_number ? `, page / section ${source.page_number}` : ''
    const url = href ? `\n${new URL(href, window.location.origin).href}` : ''
    return `[Source ${index + 1}] ${source.filename}${location}${url}\n${source.text_snippet || ''}`
  })
  return references.length
    ? `${message.content}\n\nRetrieved evidence (review before relying on the answer):\n\n${references.join('\n\n')}`
    : message.content
}
