<template>
  <div class="space-y-6" data-testid="research-writing">
    <!-- Check a draft -->
    <section class="space-y-3">
      <div>
        <h3 class="font-semibold">Check a draft</h3>
        <p class="text-sm text-base-content/60 max-w-prose">
          Paste a draft, a report section or an answer an AI wrote. Every quotation is looked for word for word in the
          source it cites, on the page it cites; every citation is matched to a source here, and the reference list is
          checked against the text. Nothing is saved{{ useModel ? '' : ', and nothing leaves this server' }}.
        </p>
      </div>
      <textarea
        v-model="state.text"
        rows="9"
        class="textarea textarea-bordered w-full text-sm leading-relaxed font-[inherit]"
        placeholder="Okafor et al. (2021) found that “saplings in the top conductance quartile survived drought 31% more often” (p. 1)…&#10;&#10;References&#10;Okafor, M., … (2021). Root hydraulics predict drought survival…"
        aria-label="Text to check"
        data-testid="draft-input"
      />
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <label class="flex items-center gap-2">
          <span class="text-base-content/60">Style</span>
          <select v-model="style" class="select select-bordered select-xs w-auto" aria-label="Citation style">
            <option v-for="s in CITATION_STYLES" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select>
        </label>
        <label class="flex items-center gap-1.5 cursor-pointer" :title="providerId ? '' : 'Set up a model in Settings to use this'">
          <input v-model="useModel" type="checkbox" class="checkbox checkbox-xs" :disabled="!providerId" />
          Judge cited claims with the model
        </label>
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input v-model="state.suggest" type="checkbox" class="checkbox checkbox-xs" />
          Suggest sources for uncited sentences
        </label>
        <span class="flex-1" />
        <span class="text-xs text-base-content/50 tabular-nums">{{ state.text.length.toLocaleString() }} / 60,000</span>
        <button class="btn btn-sm btn-primary" :disabled="!state.text.trim() || checking || state.text.length > 60000" data-testid="draft-check" @click="check">
          <span v-if="checking" class="loading loading-spinner loading-xs" />
          Check against {{ selectedIds.length ? `${selectedIds.length} selected` : 'all' }} source{{ selectedIds.length === 1 ? '' : 's' }}
        </button>
      </div>
      <p v-if="useModel" class="text-xs text-base-content/55">
        The model sees only the cited sentences and the passages of the sources they cite. A verdict is shown only
        when its quote is found in the passage it names.
      </p>
    </section>

    <section v-if="result" class="@container space-y-3" data-testid="draft-result">
      <div class="flex flex-wrap gap-2 text-xs" role="status">
        <span class="badge badge-sm" :class="result.summary.quotes_failed ? 'badge-error' : 'badge-success'" v-if="result.summary.quotes">
          {{ result.summary.quotes_verified }} of {{ result.summary.quotes }} quote{{ result.summary.quotes === 1 ? '' : 's' }} found in a source
        </span>
        <span class="badge badge-sm badge-ghost" v-if="result.summary.citations">
          {{ result.summary.citations_resolved }} of {{ result.summary.citations }} citation{{ result.summary.citations === 1 ? '' : 's' }} matched here
        </span>
        <span v-if="result.summary.claims_judged" class="badge badge-sm badge-ghost">
          {{ result.summary.claims_supported }} of {{ result.summary.claims_judged }} judged claims supported
        </span>
        <span v-if="result.summary.errors" class="badge badge-sm badge-error badge-outline">{{ result.summary.errors }} to fix</span>
        <span v-if="result.summary.warnings" class="badge badge-sm badge-warning badge-outline">{{ result.summary.warnings }} to check</span>
        <span v-if="result.summary.suggestions" class="badge badge-sm badge-info badge-outline">{{ result.summary.suggestions }} possible citation{{ result.summary.suggestions === 1 ? '' : 's' }}</span>
      </div>
      <p v-for="note in result.notes" :key="note" class="text-xs text-base-content/60">{{ note }}</p>

      <!-- Side by side only when this view itself is wide (not beside an open source). -->
      <div class="grid gap-4 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <!-- The draft, sentence by sentence, colored by its worst finding. -->
        <div class="rounded-box border border-base-300 p-3 max-h-[36rem] overflow-y-auto text-sm leading-7 whitespace-pre-wrap break-words" data-testid="draft-annotated">
          <template v-for="seg in segments" :key="seg.key">
            <button
              v-if="seg.severity"
              type="button"
              class="text-left rounded-sm px-0.5 -mx-0.5 underline decoration-2 underline-offset-4"
              :class="[TONES[seg.severity].mark, active?.key === seg.key ? 'bg-base-300/70' : '']"
              :aria-label="`Sentence with ${seg.ids.length} finding${seg.ids.length === 1 ? '' : 's'}: ${TONES[seg.severity].label}`"
              :aria-pressed="active?.key === seg.key"
              @click="select(seg)"
            >{{ seg.text }}</button>
            <span v-else>{{ seg.text }}</span>
          </template>
        </div>

        <!-- Findings -->
        <div class="space-y-2 min-w-0">
          <div class="flex flex-wrap items-center gap-1 text-xs">
            <button v-for="f in FILTERS" :key="f.id" class="btn btn-xs" :class="filter === f.id && !active ? 'btn-active' : 'btn-ghost'" @click="filter = f.id; active = null">
              {{ f.label }} <span class="tabular-nums text-base-content/50">{{ counts[f.id] }}</span>
            </button>
            <button v-if="active" class="btn btn-xs btn-ghost" @click="active = null">Show all</button>
          </div>
          <p v-if="!shownFindings.length" class="text-sm text-base-content/55 py-4">Nothing here.</p>
          <ul class="space-y-2 max-h-[34rem] overflow-y-auto pr-1" data-testid="draft-findings">
            <li v-for="f in shownFindings" :key="f.id" class="rounded-box border border-base-300 p-2.5 text-sm space-y-1.5" :class="TONES[f.severity].card">
              <div class="flex items-start gap-2">
                <component :is="TONES[f.severity].icon" :size="15" class="mt-0.5 shrink-0" :class="TONES[f.severity].text" aria-hidden="true" />
                <div class="min-w-0 flex-1 space-y-1">
                  <button class="block text-left text-xs text-base-content/60 line-clamp-2 hover:text-base-content" @click="select(segmentOf(f))">
                    {{ excerpt(f) }}
                  </button>
                  <p>{{ f.message }}</p>
                  <blockquote v-if="f.source_text" class="border-l-2 border-base-300 pl-2.5 text-xs text-base-content/75 italic">
                    “{{ truncate(f.source_text, 420) }}”
                  </blockquote>
                  <p v-if="f.note" class="text-xs text-base-content/60">{{ f.note }}</p>
                  <blockquote v-if="f.support?.quote" class="border-l-2 border-success/50 pl-2.5 text-xs text-base-content/75 italic">“{{ f.support.quote }}”</blockquote>
                  <details v-if="f.evidence?.length" class="text-xs">
                    <summary class="cursor-pointer text-base-content/60">Passages of the cited source ({{ f.evidence.length }})</summary>
                    <ul class="mt-1 space-y-1.5">
                      <li v-for="(e, i) in f.evidence" :key="i">
                        <a class="link link-hover text-base-content/55" :href="pageUrl(collectionId, f.document_id, e.page)" target="_blank" rel="noopener">p. {{ e.printed_page || e.page }}</a>
                        <span class="text-base-content/75"> {{ truncate(e.text, 300) }}</span>
                      </li>
                    </ul>
                  </details>
                  <div class="flex flex-wrap items-center gap-2 text-xs">
                    <button v-if="f.in_text" class="btn btn-xs btn-ghost px-1.5" @click="copyText(f.in_text, `cite-${f.id}`)">
                      <Check v-if="copied === `cite-${f.id}`" :size="11" class="text-success" /><Copy v-else :size="11" />{{ f.in_text }}
                    </button>
                    <button v-if="f.reference" class="btn btn-xs btn-ghost px-1.5" @click="copyText(f.reference, `ref-${f.id}`)">
                      <Check v-if="copied === `ref-${f.id}`" :size="11" class="text-success" /><Copy v-else :size="11" />Copy reference entry
                    </button>
                    <a v-if="f.document_id && f.page" class="link link-hover text-base-content/60" :href="pageUrl(collectionId, f.document_id, f.page)" target="_blank" rel="noopener">Open page ↗</a>
                    <button v-if="f.document_id" class="link link-hover text-base-content/60" @click="$emit('open', f.document_id)">
                      {{ truncate(result.sources?.[f.document_id]?.filename || 'Source', 40) }}
                    </button>
                  </div>
                </div>
              </div>
            </li>
          </ul>
        </div>
      </div>

      <div v-if="result.bibliography.length" class="space-y-2">
        <div class="flex items-center justify-between gap-2">
          <h4 class="text-sm font-semibold">Sources this draft cites <span class="font-normal text-base-content/55">· {{ styleLabel(result.style) }}</span></h4>
          <button class="btn btn-xs" @click="copyBibliography(result.bibliography, 'draft-bib')">
            <Check v-if="copied === 'draft-bib'" :size="12" class="text-success" /><Copy v-else :size="12" />Copy reference list
          </button>
        </div>
        <ol class="space-y-1.5 text-sm">
          <!-- eslint-disable-next-line vue/no-v-html -->
          <li v-for="b in result.bibliography" :key="b.document_id" class="pl-6 -indent-6 leading-relaxed"><span v-html="b.html" />
            <button v-if="b.missing.length" class="ml-1 badge badge-xs badge-warning badge-outline indent-0" @click="$emit('open', b.document_id)">missing {{ missingLabel(b.missing) }}</button>
          </li>
        </ol>
      </div>
    </section>

    <!-- Bibliography for the scope -->
    <section class="space-y-3 border-t border-base-300 pt-5" data-testid="research-bibliography">
      <div class="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 class="font-semibold">Reference list</h3>
          <p class="text-sm text-base-content/60">
            {{ selectedIds.length ? `The ${selectedIds.length} selected sources` : `All ${papers.length} sources` }}, formatted here from each
            source's own record. Export for Zotero, EndNote, Mendeley or LaTeX.
          </p>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <select v-model="style" class="select select-bordered select-xs w-auto" aria-label="Citation style for the reference list">
            <option v-for="s in CITATION_STYLES" :key="s.id" :value="s.id">{{ s.label }}</option>
          </select>
          <label class="flex items-center gap-1.5 text-xs cursor-pointer" title="Internal sources have no DOI or URL; say which collection and file hold them">
            <input v-model="includeLocation" type="checkbox" class="checkbox checkbox-xs" />Say where internal sources are kept
          </label>
          <button class="btn btn-xs" :disabled="!bib?.bibliography?.length" @click="copyBibliography(bibRows, 'bib')">
            <Check v-if="copied === 'bib'" :size="12" class="text-success" /><Copy v-else :size="12" />Copy all
          </button>
          <div class="dropdown dropdown-end">
            <button tabindex="0" class="btn btn-xs" :disabled="!bib?.bibliography?.length"><Download :size="12" />Export</button>
            <ul tabindex="0" class="dropdown-content menu menu-sm bg-base-100 rounded-box border border-base-300 shadow z-10 w-56 p-1">
              <li v-for="f in EXPORT_FORMATS" :key="f.id">
                <button @click="exportAs(f.id)">{{ f.label }}<span class="text-[11px] text-base-content/50">{{ f.hint }}</span></button>
              </li>
            </ul>
          </div>
        </div>
      </div>
      <div v-if="incomplete.length" class="text-xs rounded-box bg-warning/10 px-3 py-2 space-y-1" role="status">
        <p>{{ incomplete.length }} source{{ incomplete.length === 1 ? ' lacks' : 's lack' }} details a citation needs. They are cited with what is known (n.d. for no year) — open one to add what is missing.</p>
        <div class="flex flex-wrap gap-1.5">
          <button v-for="e in incomplete.slice(0, 12)" :key="e.document_id" class="badge badge-sm badge-ghost max-w-[16rem] truncate" @click="$emit('open', e.document_id)">
            {{ titleOf(e.document_id) }} · {{ missingLabel(e.missing) }}
          </button>
          <span v-if="incomplete.length > 12" class="text-base-content/55">and {{ incomplete.length - 12 }} more</span>
        </div>
      </div>
      <p v-if="bibError" class="text-xs text-error">{{ bibError }}</p>
      <ol v-else-if="bib" class="space-y-1.5 text-sm max-h-[28rem] overflow-y-auto">
        <!-- eslint-disable-next-line vue/no-v-html -->
        <li v-for="(html, i) in bib.bibliography_html" :key="i" class="pl-6 -indent-6 leading-relaxed" v-html="html" />
      </ol>
      <span v-else-if="papers.length" class="loading loading-spinner loading-sm" />
    </section>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Check, CircleAlert, CircleCheck, Copy, Download, Info, TriangleAlert } from 'lucide-vue-next'
import { researchApi, pageUrl } from '../utils/researchApi.js'
import {
  CITATION_STYLES, EXPORT_FORMATS, copyRich, downloadText, missingLabel, styleLabel, useCitationStyle,
} from '../utils/citations.js'
import { useUiStore } from '../stores/uiStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  papers: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  providerId: { type: String, default: null },
})
defineEmits(['open'])

const ui = useUiStore()
const style = useCitationStyle()

// The draft survives switching views, not a reload: it may be confidential,
// so it is never written to browser storage.
const state = reactive(draftState)
const useModel = ref(false)
const checking = ref(false)
const result = ref(null)
// The sentence (or reference entry) picked in the draft: { key, ids }.
const active = ref(null)
const filter = ref('problems')
const copied = ref('')

const TONES = {
  error: { label: 'needs fixing', icon: CircleAlert, text: 'text-error', mark: 'decoration-error/70', card: 'border-error/30' },
  warning: { label: 'worth checking', icon: TriangleAlert, text: 'text-warning', mark: 'decoration-warning/80', card: 'border-warning/30' },
  info: { label: 'for information', icon: Info, text: 'text-info', mark: 'decoration-info/50 decoration-dotted', card: '' },
  ok: { label: 'verified', icon: CircleCheck, text: 'text-success', mark: 'decoration-success/50', card: '' },
}
const FILTERS = [
  { id: 'problems', label: 'To fix or check' },
  { id: 'ok', label: 'Verified' },
  { id: 'info', label: 'Notes & suggestions' },
  { id: 'all', label: 'All' },
]

watch(() => props.collectionId, () => { result.value = null })

async function check() {
  checking.value = true
  active.value = null
  try {
    result.value = await researchApi.checkDraft(props.collectionId, {
      text: state.text,
      document_ids: props.selectedIds.length ? props.selectedIds : null,
      style: style.value,
      suggest_uncited: state.suggest,
      useModel: useModel.value && Boolean(props.providerId),
    }, props.providerId)
    state.checkedText = state.text
    filter.value = (result.value.summary.errors + result.value.summary.warnings) ? 'problems' : 'all'
  } catch (err) {
    ui.toastError(err, 'The check failed')
  } finally {
    checking.value = false
  }
}

function select(seg) {
  active.value = !seg || active.value?.key === seg.key ? null : { key: seg.key, ids: new Set(seg.ids) }
}

function segmentOf(f) {
  return segments.value.find(seg => seg.ids?.includes(f.id)) || null
}

const counts = computed(() => {
  const all = result.value?.findings || []
  return {
    problems: all.filter(f => f.severity === 'error' || f.severity === 'warning').length,
    ok: all.filter(f => f.severity === 'ok').length,
    info: all.filter(f => f.severity === 'info').length,
    all: all.length,
  }
})

const shownFindings = computed(() => {
  let list = result.value?.findings || []
  if (active.value) return list.filter(f => active.value.ids.has(f.id))
  if (filter.value === 'problems') list = list.filter(f => f.severity === 'error' || f.severity === 'warning')
  else if (filter.value !== 'all') list = list.filter(f => f.severity === filter.value)
  return list
})

// The checked text cut into sentences (clickable when they have findings)
// and the text between them, including the reference list.
const segments = computed(() => {
  if (!result.value) return []
  const text = state.checkedText || ''
  const out = []
  let pos = 0
  for (const row of result.value.sentences) {
    if (row.start > pos) out.push({ key: `g${pos}`, text: text.slice(pos, row.start) })
    out.push({ key: `s${row.index}`, text: text.slice(row.start, row.end), severity: row.severity, ids: row.findings })
    pos = row.end
  }
  if (pos < text.length) {
    // The reference list: mark entries that have findings.
    const refFindings = result.value.findings.filter(f => f.start >= pos).sort((a, b) => a.start - b.start)
    for (const f of refFindings) {
      if (f.start < pos) continue
      out.push({ key: `g${pos}`, text: text.slice(pos, f.start) })
      out.push({ key: `r${f.id}`, text: text.slice(f.start, f.end), severity: f.severity, ids: [f.id] })
      pos = f.end
    }
    out.push({ key: `g${pos}`, text: text.slice(pos) })
  }
  return out
})

function excerpt(f) {
  return truncate((state.checkedText || '').slice(f.start, f.end).replace(/\s+/g, ' '), 160)
}

function truncate(text, n) {
  const s = String(text || '')
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

let copyTimer = null
async function copyText(text, key) {
  try {
    await navigator.clipboard.writeText(text)
    copied.value = key
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = '' }, 1500)
  } catch {
    ui.notify('The browser did not allow copying', 'warning')
  }
}

async function copyBibliography(rows, key) {
  try {
    await copyRich(rows.map(r => r.text).join('\n\n'), rows.map(r => `<p>${r.html}</p>`).join(''))
    copied.value = key
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = '' }, 1500)
  } catch {
    ui.notify('The browser did not allow copying', 'warning')
  }
}

// ── reference list for the scope ──
const includeLocation = ref(true)
const bib = ref(null)
const bibError = ref('')
const scopeIds = computed(() => props.selectedIds.length ? props.selectedIds : props.papers.map(p => p.document_id))
const bibRows = computed(() => (bib.value?.bibliography || []).map((text, i) => ({ text, html: bib.value.bibliography_html[i] })))
const incomplete = computed(() => (bib.value?.entries || []).filter(e => e.missing.length))
const titles = computed(() => Object.fromEntries(props.papers.map(p => [p.document_id, p.title || p.filename])))
const titleOf = (id) => truncate(titles.value[id] || id, 40)

let bibSeq = 0
async function loadBibliography() {
  if (!scopeIds.value.length) { bib.value = null; return }
  const mine = ++bibSeq
  bibError.value = ''
  try {
    const out = await researchApi.citations(props.collectionId, {
      document_ids: scopeIds.value, style: style.value, include_location: includeLocation.value,
    })
    if (mine === bibSeq) bib.value = out
  } catch (err) {
    if (mine === bibSeq) bibError.value = err?.message || 'Could not build the reference list'
  }
}
let bibTimer = null
watch(() => [props.collectionId, scopeIds.value.join(','), style.value, includeLocation.value,
  props.papers.map(p => `${p.analyzed_at}${p.corrected_at}`).join()], () => {
  clearTimeout(bibTimer)
  bibTimer = setTimeout(loadBibliography, 150)
}, { immediate: true })

async function exportAs(format) {
  try {
    const out = await researchApi.citations(props.collectionId, {
      document_ids: scopeIds.value, style: style.value, include_location: includeLocation.value, export: format,
    })
    downloadText(out.export.content, out.export.filename, out.export.media_type)
  } catch (err) {
    ui.toastError(err, 'Export failed')
  }
}
</script>

<script>
// Module scope: kept while the Research view switches tabs, gone on reload.
import { reactive as _reactive } from 'vue'
const draftState = _reactive({ text: '', checkedText: '', suggest: true })
</script>
