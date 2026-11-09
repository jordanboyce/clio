<template>
  <section class="space-y-2" data-testid="paper-cite">
    <div class="flex items-center justify-between gap-2">
      <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Cite</h4>
      <select v-model="style" class="select select-bordered select-xs w-auto" aria-label="Citation style">
        <option v-for="s in CITATION_STYLES" :key="s.id" :value="s.id">{{ s.label }}</option>
      </select>
    </div>

    <template v-if="entry">
      <!-- The server escapes the record; <i> marks titles. -->
      <!-- eslint-disable-next-line vue/no-v-html -->
      <p class="text-sm leading-relaxed pl-6 -indent-6 select-text" data-testid="cite-reference" v-html="entry.reference_html" />
      <p class="text-xs text-base-content/60">
        In the text: <span class="font-mono text-base-content/80 select-text">{{ entry.in_text }}</span>
      </p>
      <div v-if="entry.missing.length" class="flex flex-wrap items-center gap-2 text-xs rounded-box bg-warning/10 px-2 py-1.5" role="status">
        <TriangleAlert :size="13" class="text-warning shrink-0" aria-hidden="true" />
        <span>Not found in the source: {{ missingLabel(entry.missing) }}. Nothing is filled in by guesswork.</span>
        <button v-if="canWrite" class="btn btn-xs btn-ghost" @click="openEditor">Add {{ entry.missing.length === 1 ? 'it' : 'them' }}</button>
      </div>
      <div class="flex flex-wrap gap-1.5">
        <button class="btn btn-xs" @click="copy('reference')">
          <Check v-if="copied === 'reference'" :size="12" class="text-success" /><Copy v-else :size="12" />Copy reference
        </button>
        <button class="btn btn-xs btn-ghost" @click="copy('in_text')">
          <Check v-if="copied === 'in_text'" :size="12" class="text-success" />Copy in-text
        </button>
        <div class="dropdown dropdown-end">
          <button tabindex="0" class="btn btn-xs btn-ghost"><Download :size="12" />Export</button>
          <ul tabindex="0" class="dropdown-content menu menu-sm bg-base-100 rounded-box border border-base-300 shadow z-10 w-56 p-1">
            <li v-for="f in EXPORT_FORMATS" :key="f.id">
              <button @click="exportAs(f.id)">{{ f.label }}<span class="text-[11px] text-base-content/50">{{ f.hint }}</span></button>
            </li>
          </ul>
        </div>
        <button v-if="canWrite && !editing" class="btn btn-xs btn-ghost" @click="openEditor"><Pencil :size="12" />Correct details</button>
      </div>
    </template>
    <p v-else-if="error" class="text-xs text-error">{{ error }}</p>
    <span v-else class="loading loading-spinner loading-xs" />

    <!-- Correcting the record. What a person types is labelled as theirs and
         kept through every later re-analysis. -->
    <form v-if="editing" class="mt-2 space-y-2 rounded-box border border-base-300 p-3" data-testid="record-editor" @submit.prevent="save">
      <p class="text-xs text-base-content/60">
        Corrections are kept, marked as entered by you, even when the source is read again. Leave a field empty if the source does not have it.
      </p>
      <label class="form-control">
        <span class="label-text text-xs">Type</span>
        <select v-model="draft.kind" class="select select-bordered select-xs">
          <option v-for="k in KINDS" :key="k.id" :value="k.id">{{ k.label }}</option>
        </select>
      </label>
      <template v-for="f in visibleFields" :key="f.id">
        <label class="form-control">
          <span class="label-text text-xs flex items-center gap-2">
            {{ f.label }}
            <span v-if="userSet(f.id)" class="badge badge-xs badge-ghost">corrected</span>
            <button v-if="userSet(f.id)" type="button" class="link link-hover text-[11px] text-base-content/55" @click="revert(f.id)">use what was read</button>
          </span>
          <textarea v-if="f.multi" v-model="draft[f.id]" rows="3" class="textarea textarea-bordered textarea-xs leading-snug" :placeholder="f.placeholder" />
          <input v-else v-model="draft[f.id]" class="input input-bordered input-xs" :inputmode="f.numeric ? 'numeric' : undefined" :placeholder="f.placeholder" />
          <span v-if="f.hint" class="text-[11px] text-base-content/50 mt-0.5">{{ f.hint }}</span>
        </label>
      </template>
      <p v-if="saveError" class="text-xs text-error" role="alert">{{ saveError }}</p>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn btn-xs btn-ghost" @click="editing = false">Cancel</button>
        <button class="btn btn-xs btn-primary" :disabled="saving || !changedFields.length">
          <span v-if="saving" class="loading loading-spinner loading-xs" />Save {{ changedFields.length || '' }}
        </button>
      </div>
    </form>
  </section>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { Check, Copy, Download, Pencil, TriangleAlert } from 'lucide-vue-next'
import { researchApi } from '../utils/researchApi.js'
import { CITATION_STYLES, EXPORT_FORMATS, copyRich, downloadText, missingLabel, useCitationStyle } from '../utils/citations.js'
import { useUiStore } from '../stores/uiStore'

const props = defineProps({
  paper: { type: Object, required: true },
  collectionId: { type: String, required: true },
  canWrite: Boolean,
})
const emit = defineEmits(['changed'])
const ui = useUiStore()
const style = useCitationStyle()
const entry = ref(null)
const error = ref('')
const copied = ref('')
const editing = ref(false)
const saving = ref(false)
const saveError = ref('')

const KINDS = [
  { id: 'article', label: 'Journal or conference article' },
  { id: 'book', label: 'Book' },
  { id: 'chapter', label: 'Chapter in a book' },
  { id: 'report', label: 'Report (technical, internal, government)' },
  { id: 'thesis', label: 'Thesis or dissertation' },
  { id: 'web', label: 'Web page' },
  { id: 'dataset', label: 'Dataset' },
  { id: 'other', label: 'Other document' },
]
const FIELDS = [
  { id: 'title', label: 'Title', kinds: '*' },
  { id: 'authors', label: 'Authors, one per line', multi: true, kinds: '*', placeholder: 'Mira Okafor\nOkafor, Mira',
    hint: 'An organization as author: type it in braces, {Idaho Test Laboratory}, or use Organization below.' },
  { id: 'organization', label: 'Organization (author or institution)', kinds: '*' },
  { id: 'year', label: 'Year', numeric: true, kinds: '*' },
  { id: 'venue', label: 'Journal or conference', kinds: ['article'] },
  { id: 'container_title', label: 'Book title', kinds: ['chapter'] },
  { id: 'editors', label: 'Editors, one per line', multi: true, kinds: ['chapter'] },
  { id: 'volume', label: 'Volume', kinds: ['article', 'chapter', 'book'] },
  { id: 'issue', label: 'Issue', kinds: ['article'] },
  { id: 'pages', label: 'Pages', kinds: ['article', 'chapter'], placeholder: '1184–1199' },
  { id: 'report_number', label: 'Report number', kinds: ['report', 'thesis', 'dataset', 'other'] },
  { id: 'edition', label: 'Edition', kinds: ['book', 'chapter', 'report'], placeholder: '2' },
  { id: 'publisher', label: 'Publisher', kinds: ['book', 'chapter', 'report', 'thesis', 'dataset', 'web', 'other'] },
  { id: 'place', label: 'Place of publication', kinds: ['book', 'chapter', 'report'] },
  { id: 'doi', label: 'DOI', kinds: '*', placeholder: '10.1000/xyz' },
  { id: 'isbn', label: 'ISBN', kinds: ['book', 'chapter', 'report'] },
  { id: 'url', label: 'URL', kinds: ['web', 'dataset', 'report', 'other'] },
]
const draft = reactive({})
const original = ref({})
const visibleFields = computed(() => FIELDS.filter(f => f.kinds === '*' || f.kinds.includes(draft.kind)))

function valueOf(field) {
  const p = props.paper
  const d = p.details || {}
  if (['authors', 'editors'].includes(field)) return ((field === 'authors' ? p.authors : d.editors) || []).join('\n')
  if (field === 'pages') return d.pages || ''
  if (['title', 'year', 'venue', 'doi', 'isbn', 'kind'].includes(field)) return p[field] == null ? '' : String(p[field])
  return d[field] == null ? '' : String(d[field])
}

function userSet(field) {
  return props.paper.provenance?.[field]?.source === 'user'
}

function openEditor() {
  // The type the citation uses (a guessed "article" with no journal is cited as a document).
  const values = { kind: (userSet('kind') && valueOf('kind')) || entry.value?.kind || valueOf('kind') || 'other' }
  for (const f of FIELDS) values[f.id] = valueOf(f.id)
  Object.assign(draft, values)
  original.value = { ...values }
  saveError.value = ''
  editing.value = true
}

const changedFields = computed(() => Object.keys(draft).filter(k => (draft[k] ?? '') !== (original.value[k] ?? '')))

async function save() {
  saving.value = true
  saveError.value = ''
  try {
    const changes = {}
    for (const k of changedFields.value) {
      changes[k] = ['authors', 'editors'].includes(k) ? draft[k].split('\n').map(s => s.trim()).filter(Boolean) : draft[k]
    }
    const updated = await researchApi.correct(props.collectionId, props.paper.document_id, changes)
    editing.value = false
    ui.notify('Details saved', 'success')
    emit('changed', updated)
  } catch (err) {
    saveError.value = err?.message || 'Could not save'
  } finally {
    saving.value = false
  }
}

async function revert(field) {
  try {
    const updated = await researchApi.correct(props.collectionId, props.paper.document_id, {}, [field])
    emit('changed', updated)
    draft[field] = ''
    // Re-open on the reverted record so the field shows what was read.
    setTimeout(openEditor, 0)
  } catch (err) {
    ui.toastError(err, 'Could not revert')
  }
}

let seq = 0
async function load() {
  const mine = ++seq
  error.value = ''
  try {
    const out = await researchApi.citations(props.collectionId, { document_ids: [props.paper.document_id], style: style.value })
    if (mine === seq) entry.value = out.entries[0] || null
  } catch (err) {
    if (mine === seq) error.value = err?.message || 'Could not build the citation'
  }
}
watch(() => [props.paper.document_id, props.paper.corrected_at, props.paper.analyzed_at, style.value], load, { immediate: true })
watch(() => props.paper.document_id, () => { editing.value = false })

let copyTimer = null
async function copy(which) {
  if (!entry.value) return
  try {
    if (which === 'reference') await copyRich(entry.value.reference, entry.value.reference_html)
    else await navigator.clipboard.writeText(entry.value.in_text)
    copied.value = which
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copied.value = '' }, 1500)
  } catch {
    ui.notify('The browser did not allow copying', 'warning')
  }
}

async function exportAs(format) {
  try {
    const out = await researchApi.citations(props.collectionId, {
      document_ids: [props.paper.document_id], style: style.value, export: format,
    })
    const base = (props.paper.suggested_filename || props.paper.filename || 'reference').replace(/\.[^.]+$/, '')
    const ext = out.export.filename.split('.').pop()
    downloadText(out.export.content, `${base}.${ext}`, out.export.media_type)
  } catch (err) {
    ui.toastError(err, 'Export failed')
  }
}
</script>
