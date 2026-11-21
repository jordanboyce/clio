/*! Clio static search: what every component shares. MIT License. https://github.com/jordanboyce/clio */
// Reading an export made by Clio (Export, "For a website") without the search
// code: the manifest read past every cache, files fetched by checksum and
// checked against it, paths resolved the way a link's are, and the custom
// properties, parts and DOM helpers the components have in common. See
// docs/STATIC_SEARCH.md, "Writing a component".
//
//   import { loadManifest, loadListed, folderOf } from './clio-common.js'
//   const manifest = await loadManifest(folderOf(this, 'src', 'search/', import.meta.url))
//
// Source text is hostile: it was written by whoever wrote the sources. The
// helpers here only ever set textContent and attributes, never markup.

export const FORMAT = 'clio-static-search'
// v1: searched by meaning. v2: no vectors, searched by keyword.
export const VERSION = 2

export const join = (base, file) => (base.endsWith('/') ? base : base + '/') + file

export async function get(fetchFn, url, kind, init) {
  const response = await fetchFn(url, init)
  if (!response.ok) throw new Error(`Could not load ${url} (${response.status})`)
  if (kind === 'json') return response.json()
  if (kind === 'text') return response.text()
  return response.arrayBuffer()
}

export async function sha256(buffer) {
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null // a page on plain http has no SubtleCrypto: nothing to check with
  const digest = new Uint8Array(await subtle.digest('SHA-256', buffer))
  return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('')
}

function decode(buffer, kind) {
  if (kind === 'json') return JSON.parse(new TextDecoder().decode(buffer))
  if (kind === 'text') return new TextDecoder().decode(buffer)
  return buffer
}

/**
 * One file the manifest names ({ file, sha256 }). It is asked for by its
 * checksum (?v=...), so a cache that still holds last year's file cannot hand
 * it over; with `verify` its bytes are checked against the manifest, once more
 * with the cache bypassed, so a half-updated site says so instead of showing
 * the wrong thing. `kind` is 'json', 'text' or 'buffer'.
 */
export async function getListed(fetchFn, baseUrl, entry, kind, verify = true) {
  const url = join(baseUrl, entry.file) + (entry.sha256 ? `?v=${entry.sha256.slice(0, 16)}` : '')
  if (!verify || !entry.sha256 || !globalThis.crypto?.subtle) return get(fetchFn, url, kind)
  for (const init of [undefined, { cache: 'reload' }]) {
    const buffer = await get(fetchFn, url, 'buffer', init)
    if ((await sha256(buffer)) === entry.sha256) return decode(buffer, kind)
  }
  throw new Error(`${entry.file} does not match its manifest: the site holds files from two different ` +
    'exports. Upload the whole export again.')
}

/**
 * manifest.json from an export folder, never from a cache, not even after
 * revalidation: a server that validates by date answers "not modified" for a
 * manifest restored with an older date. Refuses a format it cannot read.
 * The folder is kept on the manifest as `baseUrl`.
 */
export async function loadManifest(baseUrl, { fetch: fetchFn = globalThis.fetch, maxVersion = VERSION } = {}) {
  const manifest = await get(fetchFn, join(baseUrl, 'manifest.json'), 'json', { cache: 'no-store' })
  if (manifest.format !== FORMAT) throw new Error('That folder is not a Clio static search export.')
  if (manifest.version > maxVersion) {
    throw new Error(`This export is format v${manifest.version}; this page reads up to v${maxVersion}.`)
  }
  Object.defineProperty(manifest, 'baseUrl', { value: baseUrl, enumerable: false })
  return manifest
}

/** The manifest's entry for an addition (include=...), or null when the export does not carry it. */
export const addition = (manifest, id) => manifest?.additions?.[id] || null

/** One file of an addition, by its name in the export folder, checked against the manifest. */
export async function loadListed(manifest, file, kind = 'json', { fetch: fetchFn = globalThis.fetch, verify = true } = {}) {
  const entry = Object.values(manifest.additions || {}).flatMap((a) => a.files || []).find((f) => f.file === file) ||
    (manifest.passages?.files || []).find((f) => f.file === file)
  if (!entry) throw new Error(`The export does not list ${file}.`)
  return getListed(fetchFn, manifest.baseUrl, entry, kind, verify)
}

/**
 * A folder named by an attribute, resolved as a link on the page would be;
 * without the attribute, `fallback` beside the component's own file (`here`,
 * its import.meta.url). Always ends with "/".
 */
export function folderOf(element, attribute, fallback, here) {
  const given = element.getAttribute(attribute)
  const url = new URL(given || fallback, given ? document.baseURI : here).href
  return url.endsWith('/') ? url : url + '/'
}

/** Where a passage is on a page of the export (its anchor is the passage's id). */
export const anchorOf = (id) => `#p-${String(id).replace(/[^A-Za-z0-9_-]/g, '_')}`

// ── Where a passage is on the web ──────────────────────────────────────────

const fill = (template, values) => template.replace(/\{(\w+)\}/g, (_, key) => String(values[key] ?? ''))

// A text directive (#:~:text=) the browser scrolls to and highlights. Only the
// start of the passage, a few words: the page's own text may differ from
// what Clio extracted further on, and a directive that does not match is
// simply ignored.
function textDirective(text, wordCount = 8) {
  const start = (text || '').replace(/\s+/g, ' ').trim().split(' ').slice(0, wordCount).join(' ')
  if (!start) return ''
  const encode = (s) => encodeURIComponent(s).replace(/-/g, '%2D').replace(/,/g, '%2C').replace(/&/g, '%26')
  return `:~:text=${encode(start)}`
}

/**
 * Where a hit's passage is on the web, or null when the export gave its
 * source no address. The document's `link` (from the manifest) says how to
 * point inside it: a PDF opens at the page, a file in a repository at its
 * lines, and a web page scrolls to the passage's opening words. A source
 * with no address of its own links to its page in the export's reader
 * (include=reader), at the passage, when the export has one.
 */
export function passageLink(hit) {
  const link = hit?.document?.link
  if (!link?.url) return readerLink(hit)
  const passage = hit.passage || {}
  const [url, fragment = ''] = link.url.split('#')
  if (link.lines && passage.lines) {
    return url + fill(link.lines, { start: passage.lines[0], end: passage.lines[1] })
  }
  if (link.page && passage.page) return url + fill(link.page, { page: passage.page })
  if (link.text) {
    const directive = textDirective(passage.text)
    if (directive) return `${url}#${fragment}${directive}`
  }
  return link.url
}

/** The passage on its source's reader page, or null when the export has no reader. */
export function readerLink(hit) {
  const page = hit?.document?.reader
  const base = hit?.index?.baseUrl
  if (!page || base == null) return null
  const here = globalThis.document?.baseURI || globalThis.location?.href
  const folder = here ? new URL(base, here) : new URL(base)
  return new URL(page, folder).href + (hit.passage?.id != null ? anchorOf(hit.passage.id) : '')
}

// ── Look ───────────────────────────────────────────────────────────────────
// Every component takes the page's font and colours. A site sets these once
// for all of them: --clio-font, --clio-color, --clio-muted, --clio-accent,
// --clio-background, --clio-border, --clio-radius, --clio-mark. A component's
// own properties (--clio-search-accent, ...) win over them.

export const BASE_STYLE = `
:host { display: block; font: var(--clio-font, inherit); color: var(--clio-color, inherit); }
:host([hidden]) { display: none; }
.status { font-size: .85em; color: var(--clio-muted, GrayText); min-height: 1.2em; }
a { color: var(--clio-accent, LinkText); text-decoration: underline; text-underline-offset: .15em; }
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible {
  outline: 2px solid var(--clio-accent, Highlight); outline-offset: 1px;
}
.meta { font-size: .8em; color: var(--clio-muted, GrayText); }
mark { background: var(--clio-mark, Mark); color: inherit; padding: 0 .1em; }
`

/**
 * Give a shadow root its styles. A constructed stylesheet where the browser
 * has them, which a page's Content-Security-Policy allows without
 * 'unsafe-inline'; a <style> element otherwise.
 */
export function adoptStyles(root, ...css) {
  const text = css.join('\n')
  try {
    const sheet = new CSSStyleSheet()
    sheet.replaceSync(text)
    root.adoptedStyleSheets = [...root.adoptedStyleSheets, sheet]
  } catch {
    const style = document.createElement('style')
    style.textContent = text
    root.prepend(style)
  }
}

/**
 * An element with text, never markup: el('a', { part: 'title', href }, 'text').
 * Attributes named in `attrs` are set as attributes; `text` as textContent.
 */
export function el(tag, attrs = {}, text) {
  const node = document.createElement(tag)
  for (const [name, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue
    if (name === 'className') node.className = value
    else node.setAttribute(name, value === true ? '' : String(value))
  }
  if (text != null) node.textContent = String(text)
  return node
}

/** A custom event that leaves the shadow DOM. */
export function emit(node, name, detail) {
  node.dispatchEvent(new CustomEvent(name, { detail, bubbles: true, composed: true }))
}
