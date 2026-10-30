// Text people type or paste into a collection: what formatting is kept, and
// how it is cleaned on the way in (the editor) and on the way out (the note
// reader). The server applies the same allowlist again (services/pasted_text.py);
// this copy is what keeps a rich paste from a web page or a word processor
// from dragging scripts, tracking images and inline styles into the editor.
import DOMPurify from 'dompurify'

export const RICH_TEXT_TAGS = [
  'p', 'br', 'hr', 'div',
  'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
  'ul', 'ol', 'li',
  'strong', 'b', 'em', 'i', 'u', 's', 'sub', 'sup',
  'a', 'blockquote', 'pre', 'code',
  'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td',
]

const OPTIONS = { ALLOWED_TAGS: RICH_TEXT_TAGS, ALLOWED_ATTR: ['href'], KEEP_CONTENT: true }

/** Markup reduced to the tags above; links keep only an http(s)/mailto href. */
export function sanitizeRichText(html) {
  const clean = DOMPurify.sanitize(String(html || ''), OPTIONS)
  // DOMPurify already drops javascript: and data: hrefs; relative ones are
  // meaningless once the note is stored, so they go too.
  if (typeof document === 'undefined') return clean
  const box = document.createElement('div')
  box.innerHTML = clean
  for (const a of box.querySelectorAll('a[href]')) {
    if (!/^(https?:\/\/|mailto:)/i.test(a.getAttribute('href').trim())) a.removeAttribute('href')
  }
  return box.innerHTML
}

const escapeHtml = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** Plain text as paragraphs: blank lines separate them, single newlines break. */
export function plainTextToHtml(text) {
  const blocks = String(text || '').replace(/\r\n?/g, '\n').trim().split(/\n\s*\n/)
  return blocks
    .map(block => block.split('\n').map(l => l.trimEnd()).filter(l => l.trim()).map(escapeHtml).join('<br>'))
    .filter(Boolean)
    .map(p => `<p>${p}</p>`)
    .join('')
}

/** The body of a stored note (a whole HTML document), cleaned for display. */
export function noteBodyHtml(documentHtml) {
  const parsed = new DOMParser().parseFromString(String(documentHtml || ''), 'text/html')
  return sanitizeRichText(parsed.body ? parsed.body.innerHTML : documentHtml)
}

/** How much someone has written, as the editor and the server count it:
 *  blocks are separated by a space, as the server's text extraction reads them. */
export function readableLength(html) {
  const box = document.createElement('div')
  box.innerHTML = String(html || '').replace(/<(br|\/(?:p|div|h[1-6]|li|tr|td|th|blockquote|pre))\s*>/gi, '$& ')
  return (box.textContent || '').replace(/\s+/g, ' ').trim().length
}
