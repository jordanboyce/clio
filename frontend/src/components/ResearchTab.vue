<template>
  <div class="max-w-6xl mx-auto py-4 space-y-4" data-testid="research-tab">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold">Papers</h2>
        <p class="text-sm text-base-content/60">
          Decide what to read, keep papers findable, and follow what they cite. Details come from each paper's own
          text<span v-if="capabilities.registry_lookups"> or the scholarly record for the identifier it prints</span>.
        </p>
      </div>
      <div class="segmented" role="tablist" aria-label="Papers view">
        <button
          v-for="v in views"
          :key="v.id"
          class="segment"
          role="tab"
          :aria-selected="view === v.id"
          :class="view === v.id ? 'is-on text-base-content' : 'text-base-content/60 hover:text-base-content'"
          @click="view = v.id"
        >
          <component :is="v.icon" :size="14" aria-hidden="true" />{{ v.label }}
          <span v-if="v.id === 'reviews' && stats.unread_briefs" class="badge badge-primary badge-xs tabular-nums">{{ stats.unread_briefs }}</span>
        </button>
      </div>
    </div>

    <div class="flex gap-4 min-h-0">
      <div class="flex-1 min-w-0">
        <!-- Library -->
        <div v-if="view === 'library'" class="space-y-3">
          <div v-if="loaded && !papers.length" class="text-sm text-base-content/60 py-10 text-center">
            No papers in this collection yet. Add PDFs from the Sources panel, then come back here.
          </div>

          <template v-else>
            <div v-if="canWrite && unanalyzed.length" class="alert py-2 px-3 text-sm" data-testid="research-analyze-banner">
              <span>{{ unanalyzed.length }} paper{{ unanalyzed.length === 1 ? ' has' : 's have' }} not been read for title, authors, abstract and references yet.</span>
              <button class="btn btn-xs btn-primary" :disabled="analyzing" @click="analyzeNew">
                <span v-if="analyzing" class="loading loading-spinner loading-xs" />Analyze {{ unanalyzed.length }}
              </button>
            </div>

            <!-- Screen by question -->
            <form class="flex flex-wrap gap-2" @submit.prevent="screen">
              <input
                v-model="question"
                class="input input-bordered input-sm flex-1 min-w-[16rem]"
                placeholder="What are you looking for? e.g. effect of root depth on drought survival"
                aria-label="Research question to screen papers against"
                list="research-questions"
              />
              <datalist id="research-questions">
                <option v-for="q in pastQuestions" :key="q" :value="q" />
              </datalist>
              <button class="btn btn-sm" :disabled="!canScreen || screening" :title="screenHint">
                <span v-if="screening" class="loading loading-spinner loading-xs" />
                Screen {{ screenTargets.length }} paper{{ screenTargets.length === 1 ? '' : 's' }}
              </button>
            </form>
            <p v-if="screenHint && question.trim()" class="text-xs text-base-content/55 -mt-1">{{ screenHint }}</p>

            <div class="flex flex-wrap items-center gap-2 text-xs">
              <input v-model="filter" class="input input-bordered input-xs w-48" placeholder="Filter by title, author, year" aria-label="Filter papers" />
              <select v-model="sort" class="select select-bordered select-xs" aria-label="Sort papers">
                <option value="verdict" :disabled="!hasVerdicts">Most relevant first</option>
                <option value="recent">Recently added</option>
                <option value="year">Newest publication</option>
                <option value="cited">Most cited in this collection</option>
                <option value="title">Title</option>
              </select>
              <span class="text-base-content/50 tabular-nums">{{ shown.length }} of {{ papers.length }}</span>
              <span class="flex-1" />
              <template v-if="canWrite">
                <span v-if="selected.size" class="text-base-content/60">{{ selected.size }} selected</span>
                <button class="btn btn-xs btn-ghost" :disabled="!renameCandidates.length" @click="openRenamePreview">
                  Rename to descriptive names ({{ renameCandidates.length }})
                </button>
              </template>
            </div>

            <ul class="divide-y divide-base-300/70 border-y border-base-300/70" data-testid="research-paper-list">
              <li
                v-for="p in shown"
                :key="p.document_id"
                class="flex items-start gap-3 py-2.5 px-1"
                :class="openId === p.document_id ? 'bg-base-200/60' : 'hover:bg-base-200/30'"
              >
                <input
                  v-if="canWrite"
                  type="checkbox"
                  class="checkbox checkbox-xs mt-1"
                  :checked="selected.has(p.document_id)"
                  :aria-label="`Select ${p.title || p.filename}`"
                  @change="toggleSelect(p.document_id)"
                />
                <button class="min-w-0 flex-1 text-left" @click="openPaper(p.document_id)">
                  <span class="block text-sm font-medium leading-snug">{{ p.title || p.filename }}</span>
                  <span class="block text-xs text-base-content/60 mt-0.5">
                    <template v-if="p.analyzed">
                      {{ authorsShort(p.authors) || 'Authors unknown' }}<span v-if="p.year"> · {{ p.year }}</span><span v-if="p.venue"> · {{ p.venue }}</span>
                    </template>
                    <template v-else>Not analyzed</template>
                    <span v-if="p.title && p.filename !== p.suggested_filename" class="text-base-content/40"> · {{ p.filename }}</span>
                  </span>
                  <span v-if="verdicts[p.document_id]?.evidence?.[0]" class="block text-xs text-base-content/70 mt-1 line-clamp-2">
                    “{{ verdicts[p.document_id].evidence[0].quote }}” <span class="text-base-content/45">p. {{ verdicts[p.document_id].evidence[0].page }}</span>
                  </span>
                </button>
                <div class="flex flex-col items-end gap-1 shrink-0 text-xs">
                  <span v-if="verdicts[p.document_id]" class="badge badge-sm" :class="VERDICTS[verdicts[p.document_id].verdict]?.tone">
                    {{ VERDICTS[verdicts[p.document_id].verdict]?.label }}
                  </span>
                  <span class="text-base-content/45 tabular-nums space-x-2">
                    <span v-if="p.unread_briefs" class="text-primary">● review</span>
                    <span v-if="p.cited_by" :title="`Cited by ${p.cited_by} of your papers`">↖ {{ p.cited_by }}</span>
                    <span v-if="p.references" :title="`${p.references} references`">≡ {{ p.references }}</span>
                  </span>
                </div>
              </li>
            </ul>
          </template>
        </div>

        <ResearchMap
          v-else-if="view === 'map'"
          :collection-id="collectionId"
          :can-download="canWrite && capabilities.link_download"
          :refresh-key="refreshKey"
          @open="openPaper"
        />

        <ResearchReviews
          v-else
          :collection-id="collectionId"
          :papers="papers"
          :selected-ids="[...selected]"
          :provider-id="providerId"
          :can-write="canWrite"
          :refresh-key="refreshKey"
          @open="openPaper"
          @changed="loadPapers"
        />
      </div>

      <!-- Paper panel: inline on wide screens, a sheet on narrow ones -->
      <div
        v-if="openId"
        class="fixed inset-0 z-[65] bg-base-100 lg:static lg:z-auto lg:w-[26rem] lg:shrink-0 lg:border lg:border-base-300 lg:rounded-box lg:max-h-[calc(100vh-10rem)] lg:sticky lg:top-2"
      >
        <ResearchPaperPanel
          :document-id="openId"
          :collection-id="collectionId"
          :provider-id="providerId"
          :question="question"
          @close="openId = null"
          @open="openPaper"
          @changed="onChanged"
        />
      </div>
    </div>

    <!-- Rename preview -->
    <dialog class="modal" :open="renameOpen" @close="renameOpen = false">
      <div class="modal-box max-w-3xl space-y-3">
        <h3 class="font-semibold">Rename to descriptive names</h3>
        <p class="text-sm text-base-content/70">
          Names are built from each paper's title, authors and year as found in the paper. Papers without a
          known title are left alone. Every paper keeps its original name, so this can be undone one at a time.
        </p>
        <label class="form-control">
          <span class="label-text text-xs mb-1">Pattern — {{ namingFields }}</span>
          <input v-model="pattern" class="input input-bordered input-sm font-mono" />
        </label>
        <p v-if="renameError" class="text-xs text-error" role="alert">{{ renameError }}</p>
        <ul class="max-h-80 overflow-y-auto divide-y divide-base-300/60 text-xs">
          <li v-for="p in renamePreview" :key="p.document_id" class="py-1.5 flex items-start gap-2">
            <input v-model="renameChecked" type="checkbox" class="checkbox checkbox-xs mt-0.5" :value="p.document_id" :aria-label="`Rename ${p.filename}`" />
            <div class="min-w-0">
              <div class="text-base-content/50 line-through truncate">{{ p.filename }}</div>
              <div class="truncate">{{ p.newName }}</div>
            </div>
          </li>
        </ul>
        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="renameOpen = false">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="!renameChecked.length || renaming" @click="renameSelected">
            <span v-if="renaming" class="loading loading-spinner loading-xs" />Rename {{ renameChecked.length }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="renameOpen = false">close</button></form>
    </dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Library, Network, NotebookPen } from 'lucide-vue-next'
import ResearchPaperPanel from './ResearchPaperPanel.vue'
import ResearchMap from './ResearchMap.vue'
import ResearchReviews from './ResearchReviews.vue'
import { researchApi, VERDICTS, authorsShort } from '../utils/researchApi.js'
import { useCollectionStore } from '../stores/collectionStore'
import { useProviderStore } from '../stores/providerStore'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const views = [
  { id: 'library', label: 'Library', icon: Library },
  { id: 'map', label: 'Citation map', icon: Network },
  { id: 'reviews', label: 'Reviews', icon: NotebookPen },
]

const collectionStore = useCollectionStore()
const providerStore = useProviderStore()
const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()

const collectionId = computed(() => collectionStore.currentCollectionId)
const providerId = computed(() => providerStore.activeProviderId || null)

const view = ref('library')
const papers = ref([])
const stats = ref({})
const capabilities = ref({})
const canWrite = ref(false)
const naming = ref({ default_pattern: '{year} - {authors} - {title}', fields: [] })
const loaded = ref(false)
const refreshKey = ref(0)
const openId = ref(null)
const selected = ref(new Set())
const filter = ref('')
const sort = ref('recent')
const question = ref('')
const pastQuestions = ref([])
const verdicts = ref({})
const analyzing = ref(false)
const screening = ref(false)
const renameOpen = ref(false)
const renaming = ref(false)
const pattern = ref('')
const renameChecked = ref([])
let pollTimer = null
let previewTimer = null

async function loadPapers() {
  if (!collectionId.value) return
  try {
    const data = await researchApi.papers(collectionId.value)
    papers.value = data.papers || []
    stats.value = data.stats || {}
    capabilities.value = data.capabilities || {}
    canWrite.value = Boolean(data.can_write)
    naming.value = data.naming || naming.value
    if (!pattern.value) pattern.value = naming.value.default_pattern
    const ids = new Set(papers.value.map(p => p.document_id))
    selected.value = new Set([...selected.value].filter(id => ids.has(id)))
  } catch (err) {
    ui.toastError(err, 'Could not load papers')
  } finally {
    loaded.value = true
  }
}

async function loadVerdicts() {
  const q = question.value.trim()
  try {
    const data = await researchApi.readingList(collectionId.value, q || null)
    pastQuestions.value = data.questions || []
    verdicts.value = q ? Object.fromEntries((data.items || []).map(i => [i.document_id, i])) : {}
    if (q && Object.keys(verdicts.value).length) sort.value = 'verdict'
  } catch { /* the reading list is an extra */ }
}

watch(collectionId, () => {
  openId.value = null
  selected.value = new Set()
  verdicts.value = {}
  loaded.value = false
  loadPapers()
  loadVerdicts()
}, { immediate: true })

let verdictTimer = null
watch(question, () => {
  clearTimeout(verdictTimer)
  verdictTimer = setTimeout(loadVerdicts, 400)
})

// New sources finished indexing (e.g. downloaded references): refresh.
watch(() => jobsStore.dataRefreshTick, () => {
  if (jobsStore.dataRefreshCollectionId === collectionId.value) loadPapers()
})

onBeforeUnmount(() => { clearTimeout(pollTimer); clearTimeout(verdictTimer); clearTimeout(previewTimer) })

const unanalyzed = computed(() => papers.value.filter(p => !p.analyzed))
const hasVerdicts = computed(() => Object.keys(verdicts.value).length > 0)
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  let list = papers.value
  if (f) {
    list = list.filter(p => [p.title, p.filename, (p.authors || []).join(' '), p.year, p.venue]
      .some(v => String(v || '').toLowerCase().includes(f)))
  }
  const rank = { relevant: 0, partial: 1, not_established: 2, not_relevant: 4 }
  const by = {
    verdict: (a, b) => (rank[verdicts.value[a.document_id]?.verdict] ?? 3) - (rank[verdicts.value[b.document_id]?.verdict] ?? 3),
    recent: (a, b) => String(b.uploaded || '').localeCompare(String(a.uploaded || '')),
    year: (a, b) => (b.year || 0) - (a.year || 0),
    cited: (a, b) => (b.cited_by || 0) - (a.cited_by || 0),
    title: (a, b) => String(a.title || a.filename).localeCompare(String(b.title || b.filename)),
  }
  return [...list].sort(by[sort.value] || by.recent)
})

const screenTargets = computed(() => selected.value.size ? [...selected.value] : papers.value.map(p => p.document_id))
const canScreen = computed(() => canWrite.value && providerId.value && question.value.trim().length >= 3 && screenTargets.value.length)
const screenHint = computed(() => {
  if (!providerId.value) return 'Screening needs a model (Settings). Open a paper to see where it discusses your question without one.'
  if (!canWrite.value) return 'Open a paper to check it; screening the whole collection needs edit access.'
  return ''
})

function toggleSelect(id) {
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}

function openPaper(id) {
  openId.value = id
}

function onChanged() {
  loadPapers()
  loadVerdicts()
  refreshKey.value++
}

function pollJob(jobId, done) {
  clearTimeout(pollTimer)
  pollTimer = setTimeout(async () => {
    try {
      const { jobs } = await researchApi.jobs(collectionId.value)
      const job = jobs.find(j => j.job_id === jobId)
      if (job && ['queued', 'running'].includes(job.status)) {
        if (job.done) done(job, false)
        return pollJob(jobId, done)
      }
      done(job, true)
    } catch {
      pollJob(jobId, done)
    }
  }, 2500)
}

async function analyzeNew() {
  analyzing.value = true
  try {
    const out = await researchApi.analyzeMany(collectionId.value, { only_new: true })
    if (!out.job) { analyzing.value = false; return loadPapers() }
    pollJob(out.job.job_id, (job, finished) => {
      loadPapers()
      if (finished) {
        analyzing.value = false
        refreshKey.value++
        if (job?.failed) ui.notify(`${job.failed} paper(s) could not be read`, 'warning')
      }
    })
  } catch (err) {
    analyzing.value = false
    ui.toastError(err, 'Analysis failed to start')
  }
}

async function screen() {
  if (!canScreen.value) return
  screening.value = true
  try {
    const out = await researchApi.startJob(collectionId.value, {
      kind: 'assess', question: question.value.trim(), document_ids: screenTargets.value,
    }, providerId.value)
    sort.value = 'verdict'
    pollJob(out.job.job_id, (job, finished) => {
      loadVerdicts()
      if (finished) {
        screening.value = false
        if (job?.failed) ui.notify(`${job.failed} paper(s) could not be screened`, 'warning')
      }
    })
  } catch (err) {
    screening.value = false
    ui.toastError(err, 'Screening failed to start')
  }
}

// ── rename ──
const renameCandidates = computed(() => {
  const pool = selected.value.size ? papers.value.filter(p => selected.value.has(p.document_id)) : papers.value
  return pool.filter(p => p.suggested_filename && p.suggested_filename !== p.filename)
})
const namingFields = computed(() => (naming.value.fields || []).map(f => `{${f}}`).join(' '))
const renamePreview = ref([])
const renameError = ref('')

// The server builds the names (one implementation of the rules); the
// dialog asks it for a dry run whenever the pattern changes.
async function refreshPreview({ checkAll = false } = {}) {
  const ids = renameCandidates.value.map(p => p.document_id)
  if (!ids.length) { renamePreview.value = []; return }
  try {
    const out = await researchApi.renameMany(collectionId.value, ids, pattern.value, true)
    renameError.value = ''
    renamePreview.value = out.renamed.map(r => ({ document_id: r.document_id, filename: r.previous_filename, newName: r.filename }))
    const valid = new Set(renamePreview.value.map(p => p.document_id))
    renameChecked.value = checkAll ? [...valid] : renameChecked.value.filter(id => valid.has(id))
  } catch (err) {
    renameError.value = err?.message || 'That pattern cannot be used'
    renamePreview.value = []
  }
}
watch(pattern, () => {
  if (!renameOpen.value) return
  clearTimeout(previewTimer)
  previewTimer = setTimeout(() => refreshPreview({ checkAll: true }), 300)
})

async function openRenamePreview() {
  pattern.value = pattern.value || naming.value.default_pattern
  renameOpen.value = true
  await refreshPreview({ checkAll: true })
}

async function renameSelected() {
  renaming.value = true
  try {
    const out = await researchApi.renameMany(collectionId.value, renameChecked.value, pattern.value)
    ui.notify(`Renamed ${out.renamed.length} paper${out.renamed.length === 1 ? '' : 's'}`
      + (out.skipped.length ? ` · ${out.skipped.length} left as they were` : ''), 'success')
    renameOpen.value = false
    ui.sourcesChanged()
    onChanged()
  } catch (err) {
    ui.toastError(err, 'Rename failed')
  } finally {
    renaming.value = false
  }
}
</script>
