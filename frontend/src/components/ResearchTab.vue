<template>
  <div class="max-w-6xl mx-auto py-4 space-y-4" data-testid="research-tab">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-lg font-semibold">Research</h2>
        <p class="text-sm text-base-content/60">
          Decide what to read, keep papers and books findable, and follow what they cite. Details come from each
          source's own text<span v-if="capabilities.registry_lookups"> or the catalogue record for the identifier it prints</span>.
        </p>
      </div>
      <div class="segmented" role="tablist" aria-label="Research view">
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

    <!-- The app-wide source selection (the Sources panel's checkboxes) scopes everything here. -->
    <div v-if="selectionCount" class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-box bg-base-200/70 px-3 py-2 text-sm" data-testid="research-scope">
      <ListChecks :size="15" class="shrink-0 text-base-content/60" aria-hidden="true" />
      <span>
        Working on the <strong class="tabular-nums">{{ selectionCount }}</strong> selected source{{ selectionCount === 1 ? '' : 's' }}
        <span class="text-base-content/55">— the same selection Ask and Find use.</span>
      </span>
      <button class="btn btn-xs btn-ghost" @click="selection.clear()">Clear selection</button>
    </div>

    <div class="flex gap-4 min-h-0">
      <div class="flex-1 min-w-0">
        <!-- Library -->
        <div v-if="view === 'library'" class="space-y-3">
          <div v-if="loaded && !papers.length" class="text-sm text-base-content/60 py-10 text-center">
            No papers or books in this collection yet. Add PDFs from the Sources panel, then come back here.
          </div>

          <template v-else>
            <div v-if="canWrite && unanalyzed.length" class="alert py-2 px-3 text-sm" data-testid="research-analyze-banner">
              <span>
                {{ unanalyzed.length }} {{ selectionCount ? 'selected ' : '' }}source{{ unanalyzed.length === 1 ? ' has' : 's have' }}
                not been read for title, authors, abstract and references yet.
              </span>
              <button class="btn btn-xs btn-primary" :disabled="analyzing" @click="analyzeNew()">
                <span v-if="analyzing" class="loading loading-spinner loading-xs" />Analyze {{ unanalyzed.length }}
              </button>
            </div>

            <!-- Screen by question -->
            <form class="flex flex-wrap gap-2" @submit.prevent="screen">
              <input
                v-model="question"
                class="input input-bordered input-sm flex-1 min-w-[16rem]"
                placeholder="What are you looking for? e.g. effect of root depth on drought survival"
                aria-label="Research question to screen sources against"
                list="research-questions"
              />
              <datalist id="research-questions">
                <option v-for="q in pastQuestions" :key="q" :value="q" />
              </datalist>
              <button class="btn btn-sm" :disabled="!canScreen || screening" :title="screenHint">
                <span v-if="screening" class="loading loading-spinner loading-xs" />
                Screen {{ screenTargets.length }} {{ selectionCount ? 'selected' : '' }} source{{ screenTargets.length === 1 ? '' : 's' }}
              </button>
            </form>
            <p v-if="screenHint && question.trim()" class="text-xs text-base-content/55 -mt-1">{{ screenHint }}</p>

            <div class="flex flex-wrap items-center gap-2 text-xs">
              <input v-model="filter" class="input input-bordered input-xs w-48" placeholder="Filter by title, author, year" aria-label="Filter sources" />
              <select v-model="sort" class="select select-bordered select-xs" aria-label="Sort sources">
                <option value="verdict" :disabled="!hasVerdicts">Most relevant first</option>
                <option value="recent">Recently added</option>
                <option value="year">Newest publication</option>
                <option value="cited">Most cited in this collection</option>
                <option value="title">Title</option>
              </select>
              <label v-if="selectionCount" class="flex items-center gap-1 cursor-pointer">
                <input v-model="selectedOnly" type="checkbox" class="checkbox checkbox-xs" />Selected only
              </label>
              <span class="text-base-content/50 tabular-nums">{{ shown.length }} of {{ papers.length }}</span>
              <button class="btn btn-xs btn-ghost" :disabled="!shown.length" @click="selectShown">
                {{ allShownSelected ? 'Unselect shown' : 'Select shown' }}
              </button>
              <span class="flex-1" />
              <template v-if="canWrite">
                <button
                  v-if="!unanalyzed.length && scoped.length"
                  class="btn btn-xs btn-ghost"
                  :disabled="analyzing"
                  title="Read titles, authors, abstracts and references again (after an update, or a wrong result)"
                  @click="analyzeAgain"
                ><span v-if="analyzing" class="loading loading-spinner loading-xs" />Read {{ selectionCount ? 'selected' : 'all' }} again</button>
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
                  type="checkbox"
                  class="checkbox checkbox-xs mt-1"
                  :checked="selectedSet.has(p.document_id)"
                  :aria-label="`Select ${p.title || p.filename}`"
                  @change="selection.toggle(p.document_id)"
                />
                <button class="min-w-0 flex-1 text-left" @click="openPaper(p.document_id)">
                  <span class="block text-sm font-medium leading-snug">
                    {{ p.title || p.filename }}
                    <span v-if="p.kind === 'book'" class="badge badge-ghost badge-xs align-middle ml-1">book</span>
                  </span>
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
                    <span v-if="p.cited_by" :title="`Cited by ${p.cited_by} of your sources`">↖ {{ p.cited_by }}</span>
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
          :document-ids="selectionCount ? scopeIds : null"
          :can-download="canWrite && capabilities.link_download"
          :refresh-key="refreshKey"
          :question="question"
          @open="openPaper"
        />

        <ResearchReviews
          v-else
          :collection-id="collectionId"
          :papers="papers"
          :selected-ids="selectionCount ? scopeIds : []"
          :provider-id="providerId"
          :can-write="canWrite"
          :refresh-key="refreshKey"
          @open="openPaper"
          @changed="loadPapers"
        />
      </div>

      <!-- Source panel: beside the list on wide screens, a sheet on narrow
           ones, and a drawer over the map so the map keeps its width. -->
      <div
        v-if="openId"
        class="fixed inset-0 z-[65] bg-base-100"
        :class="view === 'map'
          ? 'lg:inset-auto lg:right-4 lg:top-16 lg:bottom-4 lg:w-[26rem] lg:border lg:border-base-300 lg:rounded-box lg:shadow-xl'
          : 'lg:static lg:z-auto lg:w-[26rem] lg:shrink-0 lg:border lg:border-base-300 lg:rounded-box lg:max-h-[calc(100vh-10rem)] lg:sticky lg:top-2'"
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
          Names are built from each source's title, authors and year as found in it (or in its catalogue record).
          Sources without a known title are left alone. Each keeps its original name, so this can be undone one at a time.
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
import { computed, defineAsyncComponent, onBeforeUnmount, ref, watch } from 'vue'
import { Library, ListChecks, Network, NotebookPen } from 'lucide-vue-next'
import ResearchPaperPanel from './ResearchPaperPanel.vue'
import ResearchReviews from './ResearchReviews.vue'

// The map brings the canvas force graph; load it only when the map is opened.
const ResearchMap = defineAsyncComponent(() => import('./ResearchMap.vue'))
import { researchApi, VERDICTS, authorsShort } from '../utils/researchApi.js'
import { useCollectionStore } from '../stores/collectionStore'
import { useProviderStore } from '../stores/providerStore'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useSelectionStore } from '../stores/selectionStore'

const views = [
  { id: 'library', label: 'Library', icon: Library },
  { id: 'map', label: 'Citation map', icon: Network },
  { id: 'reviews', label: 'Reviews', icon: NotebookPen },
]

const collectionStore = useCollectionStore()
const providerStore = useProviderStore()
const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()
const selection = useSelectionStore()

const collectionId = computed(() => collectionStore.currentCollectionId)
const providerId = computed(() => providerStore.activeProviderId || null)

// Shared with the ribbon, which can open Research on a given view.
const view = computed({ get: () => ui.researchView, set: (v) => { ui.researchView = v } })
const papers = ref([])
const stats = ref({})
const capabilities = ref({})
const canWrite = ref(false)
const naming = ref({ default_pattern: '{year} - {authors} - {title}', fields: [] })
const loaded = ref(false)
const refreshKey = ref(0)
const openId = ref(null)
const selectedOnly = ref(false)
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
  } catch (err) {
    ui.toastError(err, 'Could not load sources')
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

// The selection can hold spreadsheets or code too; here it counts only the
// sources this view works on. With nothing selected, the scope is all of them.
const selectedSet = computed(() => new Set(selection.currentIds))
const selectedPapers = computed(() => papers.value.filter(p => selectedSet.value.has(p.document_id)))
const selectionCount = computed(() => selectedPapers.value.length)
const scoped = computed(() => selectionCount.value ? selectedPapers.value : papers.value)
const scopeIds = computed(() => scoped.value.map(p => p.document_id))
watch(selectionCount, (n) => { if (!n) selectedOnly.value = false })

const unanalyzed = computed(() => scoped.value.filter(p => !p.analyzed))
const hasVerdicts = computed(() => Object.keys(verdicts.value).length > 0)
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  let list = selectedOnly.value ? selectedPapers.value : papers.value
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

const screenTargets = computed(() => scopeIds.value)
const canScreen = computed(() => canWrite.value && providerId.value && question.value.trim().length >= 3 && screenTargets.value.length)
const screenHint = computed(() => {
  if (!providerId.value) return 'Screening needs a model (Settings). Open a source to see where it discusses your question without one.'
  if (!canWrite.value) return 'Open a source to check it; screening the collection needs edit access.'
  return ''
})

const allShownSelected = computed(() => shown.value.length > 0 && shown.value.every(p => selectedSet.value.has(p.document_id)))

function selectShown() {
  const ids = shown.value.map(p => p.document_id)
  if (allShownSelected.value) selection.remove(ids)
  else selection.set([...selection.currentIds, ...ids])
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

function analyzeAgain() {
  return analyzeNew({ onlyNew: false })
}

async function analyzeNew({ onlyNew = true } = {}) {
  analyzing.value = true
  try {
    const out = await researchApi.analyzeMany(collectionId.value, {
      only_new: onlyNew, document_ids: selectionCount.value ? scopeIds.value : null,
    })
    if (!out.job) { analyzing.value = false; return loadPapers() }
    pollJob(out.job.job_id, (job, finished) => {
      loadPapers()
      if (finished) {
        analyzing.value = false
        refreshKey.value++
        if (job?.failed) ui.notify(`${job.failed} source(s) could not be read`, 'warning')
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
        if (job?.failed) ui.notify(`${job.failed} source(s) could not be screened`, 'warning')
      }
    })
  } catch (err) {
    screening.value = false
    ui.toastError(err, 'Screening failed to start')
  }
}

// ── rename ──
const renameCandidates = computed(() => {
  return scoped.value.filter(p => p.suggested_filename && p.suggested_filename !== p.filename)
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
    ui.notify(`Renamed ${out.renamed.length} source${out.renamed.length === 1 ? '' : 's'}`
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
