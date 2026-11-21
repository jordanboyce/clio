/*! Clio static search: <clio-transcript> element. MIT License. https://github.com/jordanboyce/clio */
// A recording's transcript beside a player (include=transcripts): choose a
// line and the recording plays from there; open the page at a passage
// (#p-...) or a time (#t=95) and it starts there. The player plays the
// recording from the source's own address; with none, the transcript is
// shown alone.
//
//   <script type="module" src="/assets/handbook/clio-transcript.js"></script>
//   <clio-transcript src="/assets/handbook/search/" source="<source id>"></clio-transcript>
//
// Styling: the shared --clio-* properties. Parts: player, status, lines, line, time.

import { BASE_STYLE, adoptStyles, addition, el, emit, folderOf, loadListed, loadManifest } from './clio-common.js'

const STYLE = `
audio, video { width: 100%; margin: 0 0 .6em; }
ol { list-style: none; margin: 0; padding: 0; max-height: 28em; overflow-y: auto; }
li button { font: inherit; color: inherit; background: none; border: 0; padding: .2em .3em; text-align: left;
  width: 100%; cursor: pointer; display: flex; gap: .6em; border-radius: var(--clio-radius, .4em); }
li button[aria-current="true"] { background: var(--clio-mark, Mark); }
.time { color: var(--clio-muted, GrayText); font-variant-numeric: tabular-nums; min-width: 3.2em; }
`

const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`

export class ClioTranscript extends HTMLElement {
  constructor() {
    super()
    const root = this.attachShadow({ mode: 'open' })
    this.$status = el('p', { part: 'status', className: 'status', role: 'status' }, 'Loading the transcript…')
    this.$lines = el('ol', { part: 'lines', 'aria-label': 'Transcript' })
    root.append(this.$status, this.$lines)
    adoptStyles(root, BASE_STYLE, STYLE)
    this.onHash = () => this.followHash()
  }

  async connectedCallback() {
    window.addEventListener('hashchange', this.onHash)
    try {
      const manifest = await loadManifest(folderOf(this, 'src', 'search/', import.meta.url))
      const id = this.getAttribute('source')
      const entry = addition(manifest, 'transcripts')?.sources?.[id]
      if (!entry) throw new Error('This export has no transcript for that source.')
      this.cues = (await loadListed(manifest, entry.cues)).cues
      const doc = (manifest.documents || []).find((d) => d.id === id) || {}
      if (doc.link?.url) {
        this.player = el(entry.kind === 'video' ? 'video' : 'audio', { part: 'player', controls: true, preload: 'metadata',
          src: new URL(doc.link.url.split('#')[0], document.baseURI).href })
        const track = el('track', { kind: 'captions', label: 'Transcript', default: true,
          src: new URL(entry.vtt, manifest.baseUrl).href })
        this.player.append(track)
        this.player.addEventListener('timeupdate', () => this.mark(this.player.currentTime))
        this.shadowRoot.insertBefore(this.player, this.$status)
      }
      this.$lines.replaceChildren(...this.cues.map((cue, i) => {
        const button = el('button', { type: 'button', part: 'line', 'data-cue': i })
        button.append(el('span', { part: 'time', className: 'time' }, clock(cue.start)), el('span', {}, cue.text))
        button.addEventListener('click', () => this.seek(cue.start))
        const li = el('li')
        li.append(button)
        return li
      }))
      this.$status.textContent = doc.link?.url ? '' : 'The recording is not published with this site: the transcript only.'
      this.followHash()
      emit(this, 'clio-transcript-ready', { cues: this.cues.length })
    } catch (error) {
      this.$status.textContent = error.message
    }
  }

  disconnectedCallback() { window.removeEventListener('hashchange', this.onHash) }

  /** Start at the time or passage the page's address names. */
  followHash() {
    if (!this.cues) return
    const hash = decodeURIComponent(location.hash.slice(1))
    const time = /^t=(\d+(?:\.\d+)?)/.exec(hash)
    if (time) return this.seek(Number(time[1]), false)
    if (hash.startsWith('p-')) {
      const cue = this.cues.find((c) => `p-${String(c.passage).replace(/[^A-Za-z0-9_-]/g, '_')}` === hash)
      if (cue) this.seek(cue.start, false)
    }
  }

  seek(seconds, play = true) {
    this.position = seconds
    this.mark(seconds)
    if (!this.player) return
    this.player.currentTime = seconds
    if (play) this.player.play().catch(() => {}) // a browser may want a click first
  }

  mark(seconds) {
    let current = -1
    this.cues.forEach((c, i) => { if (c.start <= seconds) current = i })
    for (const b of this.$lines.querySelectorAll('button')) {
      const on = Number(b.dataset.cue) === current
      if (on) b.setAttribute('aria-current', 'true')
      else b.removeAttribute('aria-current')
    }
  }
}

if (!customElements.get('clio-transcript')) customElements.define('clio-transcript', ClioTranscript)
