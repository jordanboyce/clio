<template>
  <aside class="flex flex-col h-full min-h-0" data-testid="research-paper-panel" aria-label="Source details">
    <header class="flex items-start gap-2 px-4 pt-4 pb-3 border-b border-base-300">
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold leading-snug">{{ paper?.title || paper?.filename || 'Loading…' }}</h3>
        <p v-if="paper" class="text-xs text-base-content/60 mt-1">
          <span v-if="paper.authors?.length">{{ paper.authors.join(', ') }}</span>
          <span v-if="paper.year"> · {{ paper.year }}</span>
          <span v-if="paper.venue"> · {{ paper.venue }}</span>
        </p>
        <p v-if="paper && (paper.doi || paper.arxiv_id || paper.isbn)" class="text-xs mt-1 space-x-3">
          <span v-if="paper.kind === 'book'" class="badge badge-ghost badge-xs">book</span>
          <a v-if="paper.isbn" class="link link-hover" :href="`https://openlibrary.org/isbn/${paper.isbn}`" target="_blank" rel="noopener">ISBN {{ paper.isbn }}</a>
          <a v-if="paper.doi" class="link link-hover" :href="`https://doi.org/${paper.doi}`" target="_blank" rel="noopener">doi:{{ paper.doi }}</a>
          <a v-if="paper.arxiv_id" class="link link-hover" :href="`https://arxiv.org/abs/${paper.arxiv_id}`" target="_blank" rel="noopener">arXiv:{{ paper.arxiv_id }}</a>
        </p>
      </div>
      <button class="btn btn-ghost btn-xs btn-square" aria-label="Close" @click="$emit('close')"><X :size="16" /></button>
    </header>

    <div v-if="loading && !paper" class="p-4"><span class="loading loading-spinner loading-sm" /></div>

    <div v-else-if="paper" class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-5">
      <!-- File name -->
      <section class="space-y-1.5" data-testid="paper-filename">
        <div class="flex items-center gap-2 text-sm">
          <FileText :size="14" class="shrink-0 text-base-content/50" aria-hidden="true" />
          <a class="link link-hover truncate" :href="pageUrl(collectionId, paper.document_id)" target="_blank" rel="noopener" :title="paper.filename">{{ paper.filename }}</a>
        </div>
        <div v-if="paper.can_write" class="flex flex-wrap items-center gap-2">
          <template v-if="!editingName">
            <button
              v-if="paper.suggested_filename && paper.suggested_filename !== paper.filename"
              class="btn btn-xs btn-outline"
              :disabled="busy.rename"
              :title="paper.suggested_filename"
              @click="rename()"
            >Rename to “{{ truncate(paper.suggested_filename, 48) }}”</button>
            <button class="btn btn-xs btn-ghost" @click="startEdit">Edit name</button>
            <button v-if="paper.original_filename && paper.original_filename !== paper.filename" class="btn btn-xs btn-ghost" @click="restore">
              Restore “{{ truncate(paper.original_filename, 30) }}”
            </button>
          </template>
          <form v-else class="flex w-full gap-2" @submit.prevent="rename(nameDraft)">
            <input v-model="nameDraft" class="input input-bordered input-xs flex-1" aria-label="New file name" />
            <button class="btn btn-xs btn-primary" :disabled="!nameDraft.trim() || busy.rename">Save</button>
            <button type="button" class="btn btn-xs btn-ghost" @click="editingName = false">Cancel</button>
          </form>
        </div>
      </section>

      <!-- Abstract -->
      <section>
        <div class="flex items-baseline justify-between gap-2 mb-1">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">
            {{ isOpening(paper.provenance?.abstract) ? 'Opening' : 'Abstract' }}
          </h4>
          <span v-if="paper.abstract" class="text-[11px] text-base-content/45 text-right">{{ abstractLabel(paper.provenance?.abstract) }}</span>
        </div>
        <p v-if="paper.abstract" class="text-sm leading-relaxed whitespace-pre-line">{{ paper.abstract }}</p>
        <p v-else-if="paper.analyzed" class="text-sm text-base-content/55">No abstract, preface or introduction was found.</p>
        <div v-else class="text-sm text-base-content/60 flex items-center gap-2">
          Not analyzed yet.
          <button v-if="paper.can_write" class="btn btn-xs btn-outline" :disabled="busy.analyze" @click="analyze">
            <span v-if="busy.analyze" class="loading loading-spinner loading-xs" />Analyze
          </button>
        </div>
        <p v-if="busy.analyze" class="text-xs text-base-content/50 mt-1">Reading it in the background — a long book can take a minute or two.</p>
        <ul v-if="paper.analysis_notes?.length" class="mt-2 space-y-0.5">
          <li v-for="note in paper.analysis_notes" :key="note" class="text-xs text-base-content/50">{{ note }}</li>
        </ul>
        <details v-if="Object.keys(paper.provenance || {}).length" class="mt-1 text-xs text-base-content/50">
          <summary class="cursor-pointer">Where these details came from</summary>
          <button v-if="paper.can_write" class="btn btn-xs btn-ghost mt-1" :disabled="busy.analyze" @click="analyze">
            <span v-if="busy.analyze" class="loading loading-spinner loading-xs" />Read it again
          </button>
          <ul class="mt-1 space-y-0.5">
            <li v-for="(prov, field) in paper.provenance" :key="field">
              <span class="font-medium">{{ field.replace('_', ' ') }}</span>: {{ sourceLabel(prov) }}<span v-if="prov.detail"> — {{ prov.detail }}</span>
            </li>
          </ul>
        </details>
      </section>

      <!-- Worth my time? -->
      <section class="space-y-2" data-testid="paper-assess">
        <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Worth my time?</h4>
        <form class="flex gap-2" @submit.prevent="assess">
          <input
            v-model="questionDraft"
            class="input input-bordered input-sm flex-1"
            placeholder="What are you looking for?"
            aria-label="What are you looking for in this paper?"
          />
          <button class="btn btn-sm" :disabled="questionDraft.trim().length < 3 || busy.assess">
            <span v-if="busy.assess" class="loading loading-spinner loading-xs" />Check
          </button>
        </form>
        <p v-if="!providerId" class="text-xs text-base-content/50">
          No model set up: you will see where the source discusses it, without a verdict.
        </p>
        <div v-if="assessment" class="space-y-2">
          <div v-if="assessment.verdict" class="flex items-center gap-2">
            <span class="badge badge-sm" :class="VERDICTS[assessment.verdict]?.tone">{{ VERDICTS[assessment.verdict]?.label }}</span>
            <span v-if="assessment.summary" class="text-sm">{{ assessment.summary }}</span>
          </div>
          <ResearchEvidence
            v-if="assessment.evidence?.length"
            :items="assessment.evidence"
            :collection-id="collectionId"
            :document-id="paper.document_id"
          />
          <p v-if="assessment.missing?.length" class="text-xs text-base-content/55">
            Not addressed in the passages read: {{ assessment.missing.join('; ') }}
          </p>
          <p v-for="note in assessment.notes || []" :key="note" class="text-xs text-base-content/50">{{ note }}</p>
          <details v-if="assessment.retrieval?.length" :open="!assessment.verdict">
            <summary class="text-xs cursor-pointer text-base-content/60">Where the source discusses it ({{ assessment.retrieval.length }})</summary>
            <ul class="mt-1.5 space-y-2">
              <li v-for="hit in assessment.retrieval" :key="hit.passage" class="text-xs leading-relaxed">
                <a class="link link-hover text-base-content/55" :href="pageUrl(collectionId, paper.document_id, hit.page)" target="_blank" rel="noopener">p. {{ hit.page }}</a>
                <span class="text-base-content/75"> {{ truncate(hit.text, 280) }}</span>
              </li>
            </ul>
          </details>
        </div>
        <ul v-else-if="paper.assessments?.length" class="space-y-1">
          <li v-for="a in paper.assessments.slice(0, 3)" :key="a.id" class="text-xs flex items-center gap-2">
            <span class="badge badge-xs" :class="VERDICTS[a.verdict]?.tone">{{ VERDICTS[a.verdict]?.label }}</span>
            <button class="link link-hover truncate text-left" @click="showSaved(a)">{{ a.question }}</button>
          </li>
        </ul>
      </section>

      <!-- Reviews -->
      <section class="space-y-2" data-testid="paper-reviews">
        <div class="flex items-center justify-between gap-2">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Review</h4>
          <div v-if="paper.can_write && providerId" class="join">
            <button class="btn btn-xs join-item" :disabled="busy.brief" @click="prepare('brief')">Prepare brief</button>
            <button class="btn btn-xs join-item" :disabled="busy.brief" @click="prepare('thorough')">Thorough</button>
          </div>
        </div>
        <p v-if="queuedReview" class="text-xs text-base-content/60">
          Being prepared in the background — it will appear under Reviews when it is ready.
        </p>
        <template v-if="paper.brief_list?.length">
          <ResearchBrief :brief="paper.brief_list[0]" :collection-id="collectionId" />
          <p v-if="paper.brief_list.length > 1" class="text-xs text-base-content/50">
            {{ paper.brief_list.length - 1 }} earlier review{{ paper.brief_list.length > 2 ? 's' : '' }} under Reviews.
          </p>
        </template>
        <p v-else-if="!queuedReview" class="text-xs text-base-content/50">
          No review yet. A review quotes the source for every statement it makes.
        </p>
      </section>

      <!-- References -->
      <section v-if="paper.analyzed" class="space-y-2" data-testid="paper-references">
        <div class="flex items-center justify-between gap-2">
          <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">
            References <span class="font-normal tabular-nums">({{ paper.reference_list.length }})</span>
          </h4>
          <div v-if="paper.can_write && paper.reference_list.length" class="flex gap-1">
            <button
              v-if="paper.capabilities?.registry_lookups"
              class="btn btn-xs btn-ghost"
              :disabled="busy.resolve"
              title="Look up each reference in OpenAlex, Crossref and arXiv for an open copy"
              @click="resolveAll"
            ><span v-if="busy.resolve" class="loading loading-spinner loading-xs" />Find open copies</button>
            <button
              v-if="paper.capabilities?.link_download"
              class="btn btn-xs btn-outline"
              :disabled="busy.download"
              @click="downloadDialog = true"
            >Download…</button>
          </div>
        </div>
        <p v-if="!paper.reference_list.length" class="text-xs text-base-content/50">No reference list or bibliography was found.</p>
        <ol class="space-y-1.5">
          <li v-for="entry in visibleRefs" :key="entry.id" class="text-xs leading-relaxed flex gap-2">
            <span class="tabular-nums text-base-content/40 w-5 shrink-0 text-right">{{ entry.ordinal }}</span>
            <div class="min-w-0">
              <div class="text-base-content/80">{{ entry.raw_text }}</div>
              <div class="flex flex-wrap items-center gap-x-2 mt-0.5">
                <span class="badge badge-xs" :class="refStatus(entry).tone">{{ refStatus(entry).label }}</span>
                <button v-if="entry.matched_document_id" class="link link-hover text-left" @click="$emit('open', entry.matched_document_id)">{{ entry.matched_filename }}</button>
                <a v-else-if="entry.resolved_url" class="link link-hover truncate max-w-[16rem]" :href="entry.resolved_url" target="_blank" rel="noopener" :title="entry.resolved_via">{{ entry.resolved_url }}</a>
                <a v-else-if="entry.doi" class="link link-hover" :href="`https://doi.org/${entry.doi}`" target="_blank" rel="noopener">doi:{{ entry.doi }}</a>
              </div>
            </div>
          </li>
        </ol>
        <button v-if="paper.reference_list.length > refLimit" class="btn btn-xs btn-ghost" @click="refLimit = 1000">
          Show all {{ paper.reference_list.length }}
        </button>
      </section>

      <section v-if="paper.link_list?.length" class="space-y-1.5">
        <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Links in the text</h4>
        <ul class="space-y-1">
          <li v-for="link in paper.link_list" :key="link.id" class="text-xs flex items-center gap-2">
            <span class="text-base-content/40 tabular-nums">p. {{ link.page_number }}</span>
            <a class="link link-hover truncate" :href="link.url" target="_blank" rel="noopener">{{ link.url }}</a>
            <span v-if="link.status === 'queued'" class="badge badge-xs">Queued</span>
          </li>
        </ul>
      </section>

      <section v-if="paper.cited_by_list?.length" class="space-y-1.5">
        <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Cited by your sources</h4>
        <ul class="space-y-1">
          <li v-for="c in paper.cited_by_list" :key="c.document_id" class="text-xs">
            <button class="link link-hover text-left" @click="$emit('open', c.document_id)">{{ c.filename }}</button>
            <span class="text-base-content/45"> · {{ c.reason }}</span>
          </li>
        </ul>
      </section>
    </div>

    <!-- Download dialog -->
    <dialog class="modal" :open="downloadDialog" @close="downloadDialog = false">
      <div class="modal-box max-w-md space-y-3">
        <h3 class="font-semibold">Download references</h3>
        <p class="text-sm text-base-content/70">
          Fetches each reference into this collection. Open PDFs come from arXiv and the open-access copies
          OpenAlex knows about; paywalled papers are skipped unless you ask for their landing pages.
        </p>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="includeLinks" type="checkbox" class="checkbox checkbox-sm" />
          Also fetch the {{ paper?.link_list?.length || 0 }} links written in the text
        </label>
        <label class="flex items-center gap-2 text-sm">
          <input v-model="includePages" type="checkbox" class="checkbox checkbox-sm" />
          Fetch publisher pages when there is no open PDF
        </label>
        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="downloadDialog = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="busy.download" @click="download">
            <span v-if="busy.download" class="loading loading-spinner loading-xs" />Download
          </button>
        </div>
        <ul v-if="downloadResult?.skipped?.length" class="max-h-40 overflow-y-auto text-xs space-y-1 text-base-content/60">
          <li v-for="s in downloadResult.skipped" :key="s.reference_id">{{ truncate(s.text, 80) }} — {{ s.reason }}</li>
        </ul>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="downloadDialog = false">close</button></form>
    </dialog>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { FileText, X } from 'lucide-vue-next'
import ResearchEvidence from './ResearchEvidence.vue'
import ResearchBrief from './ResearchBrief.vue'
import { researchApi, VERDICTS, pageUrl, sourceLabel, abstractLabel, isOpening } from '../utils/researchApi.js'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  documentId: { type: String, required: true },
  collectionId: { type: String, required: true },
  providerId: { type: String, default: null },
  question: { type: String, default: '' },
})
const emit = defineEmits(['close', 'open', 'changed'])

const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()
const paper = ref(null)
const loading = ref(false)
const busy = reactive({ analyze: false, rename: false, assess: false, brief: false, resolve: false, download: false })
const editingName = ref(false)
const nameDraft = ref('')
const questionDraft = ref(props.question || '')
const assessment = ref(null)
const queuedReview = ref(false)
const refLimit = ref(25)
const downloadDialog = ref(false)
const includeLinks = ref(false)
const includePages = ref(false)
const downloadResult = ref(null)

const visibleRefs = computed(() => (paper.value?.reference_list || []).slice(0, refLimit.value))

async function load() {
  loading.value = true
  try {
    paper.value = await researchApi.paper(props.collectionId, props.documentId)
  } catch (err) {
    ui.toastError(err, 'Could not load the source')
  } finally {
    loading.value = false
  }
}

watch(() => props.documentId, () => {
  assessment.value = null
  queuedReview.value = false
  editingName.value = false
  refLimit.value = 25
  downloadResult.value = null
  load()
}, { immediate: true })
watch(() => props.question, (q) => { if (q) questionDraft.value = q })

function truncate(text, n) {
  const s = String(text || '')
  return s.length > n ? `${s.slice(0, n - 1)}…` : s
}

function refStatus(entry) {
  if (entry.status === 'in_library') return { label: 'In your library', tone: 'badge-success' }
  if (entry.status === 'queued') return { label: 'Queued', tone: 'badge-info' }
  if (entry.resolved_kind === 'pdf') return { label: 'Open PDF', tone: 'badge-outline' }
  if (entry.resolved_kind === 'page') return { label: 'Web page', tone: 'badge-ghost' }
  if (entry.status === 'unavailable') return { label: 'No open copy found', tone: 'badge-ghost' }
  return { label: entry.doi ? 'DOI' : 'As printed', tone: 'badge-ghost' }
}

// Analysis runs as a background job: a book is hundreds of pages, more than
// one request should wait for.
let analyzeTimer = null
onBeforeUnmount(() => clearTimeout(analyzeTimer))

async function analyze() {
  busy.analyze = true
  const documentId = props.documentId
  try {
    const out = await researchApi.analyzeMany(props.collectionId, { document_ids: [documentId], only_new: false })
    if (!out.job) { busy.analyze = false; return load() }
    const poll = async () => {
      try {
        const job = (await researchApi.jobs(props.collectionId)).jobs.find(j => j.job_id === out.job.job_id)
        if (job && ['queued', 'running'].includes(job.status)) { analyzeTimer = setTimeout(poll, 2000); return }
        busy.analyze = false
        if (job?.failed) ui.notify(job.errors?.[0]?.error || 'Analysis failed', 'error')
        if (props.documentId === documentId) await load()
        emit('changed')
      } catch (err) {
        busy.analyze = false
        ui.toastError(err, 'Analysis failed')
      }
    }
    analyzeTimer = setTimeout(poll, 1200)
  } catch (err) {
    busy.analyze = false
    ui.toastError(err, 'Analysis failed')
  }
}

function startEdit() {
  nameDraft.value = paper.value.filename
  editingName.value = true
}

async function rename(name = null) {
  busy.rename = true
  try {
    const out = await researchApi.rename(props.collectionId, props.documentId, name ? { filename: name } : {})
    ui.notify(`Renamed to ${out.filename}`, 'success')
    editingName.value = false
    await load()
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Rename failed')
  } finally {
    busy.rename = false
  }
}

async function restore() {
  try {
    await researchApi.restoreName(props.collectionId, props.documentId)
    await load()
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not restore the name')
  }
}

async function assess() {
  busy.assess = true
  try {
    assessment.value = await researchApi.assess(props.collectionId, props.documentId, questionDraft.value.trim(), props.providerId)
    if (assessment.value.saved_id) emit('changed')
  } catch (err) {
    ui.toastError(err, 'The check failed')
  } finally {
    busy.assess = false
  }
}

function showSaved(saved) {
  questionDraft.value = saved.question
  assessment.value = { ...saved, notes: [] }
}

async function prepare(depth) {
  busy.brief = true
  try {
    const out = await researchApi.startJob(props.collectionId, {
      kind: 'brief', depth, document_ids: [props.documentId],
      question: questionDraft.value.trim() || null, skip_reviewed: false,
    }, props.providerId)
    queuedReview.value = Boolean(out.job)
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not start the review')
  } finally {
    busy.brief = false
  }
}

async function resolveAll() {
  busy.resolve = true
  try {
    await researchApi.resolve(props.collectionId, { document_id: props.documentId })
    await load()
  } catch (err) {
    ui.toastError(err, 'Lookup failed')
  } finally {
    busy.resolve = false
  }
}

async function download() {
  busy.download = true
  try {
    const out = await researchApi.download(props.collectionId, {
      document_id: props.documentId, include_links: includeLinks.value, include_pages: includePages.value,
    })
    downloadResult.value = out
    if (out.job) jobsStore.addUploadJob(out.job)
    const n = out.queued?.length || 0
    ui.notify(n ? `Downloading ${n} item${n === 1 ? '' : 's'} into this collection` : 'Nothing new to download', n ? 'success' : 'info')
    if (n && !out.skipped?.length) downloadDialog.value = false
    await load()
  } catch (err) {
    ui.toastError(err, 'Download failed')
  } finally {
    busy.download = false
  }
}
</script>
