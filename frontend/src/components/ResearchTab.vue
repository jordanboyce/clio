<template>
  <div class="max-w-6xl mx-auto py-4 space-y-4" data-testid="research-tab">
    <div class="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div class="min-w-0">
        <h2 class="text-lg font-semibold">Library</h2>
        <p class="hidden sm:block text-sm text-base-content/65">
          Decide what to read, and follow what it cites. Details come from each source's own text<span v-if="capabilities.registry_lookups"> or its catalogue record</span>.
        </p>
      </div>
      <div class="segmented max-w-full overflow-x-auto" role="tablist" aria-label="Library view">
        <button
          v-for="v in views"
          :key="v.id"
          class="segment"
          role="tab"
          :aria-selected="view === v.id"
          :class="view === v.id ? 'is-on text-base-content' : 'text-base-content/60 hover:text-base-content'"
          @click="view = v.id"
        >
          <component :is="v.icon" :size="14" aria-hidden="true" /><span class="sm:hidden">{{ v.short }}</span><span class="hidden sm:inline">{{ v.label }}</span>
          <span v-if="v.id === 'reviews' && stats.unread_reviews" class="badge badge-primary badge-sm tabular-nums" :aria-label="`${stats.unread_reviews} unread`">{{ stats.unread_reviews }}</span>
        </button>
      </div>
    </div>

    <!-- One strip for the tab's background work (R8b): Analyze, Screen and
         Reviews, with n of m, failures and Stop. Rebuilt from the server's
         job list when the tab opens, so a reload finds it again. -->
    <ul v-if="activeJobs.length" class="space-y-1" aria-label="Library work in progress" data-testid="library-job-strip">
      <li
        v-for="job in activeJobs"
        :key="job.job_id"
        class="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-box bg-base-200/70 px-3 py-1.5 text-sm"
        data-testid="library-job"
      >
        <span v-if="job.status === 'running'" class="loading loading-spinner loading-xs" aria-hidden="true" />
        <span class="font-medium">{{ jobLabel(job) }}</span>
        <span class="tabular-nums text-base-content/70" data-testid="library-job-count">{{ (job.done || 0) + (job.failed || 0) }} of {{ job.total || 0 }}</span>
        <span v-if="job.failed" class="text-warning tabular-nums">{{ job.failed }} failed</span>
        <span v-if="job.current" class="min-w-0 flex-1 truncate text-xs text-base-content/60">Now: {{ job.current }}</span>
        <span v-else class="flex-1" />
        <progress class="progress w-24" :value="(job.done || 0) + (job.failed || 0)" :max="job.total || 1" />
        <button v-if="canWrite" class="btn btn-xs btn-ghost" :disabled="stopping.has(job.job_id)" @click="stopJob(job)">
          {{ stopping.has(job.job_id) ? 'Stopping…' : 'Stop' }}
        </button>
      </li>
    </ul>

    <!-- A6: said once, here, rather than only in each source's provenance. -->
    <div
      v-if="stats.catalogue_unreachable && capabilities.registry_lookups"
      class="notice notice-warning flex flex-wrap items-center gap-x-3 gap-y-1 text-sm"
      role="status"
      data-testid="catalogues-unreachable"
    >
      <span class="flex-1 min-w-0">Catalogues could not be reached, so details come from each source's own text.</span>
      <button v-if="canWrite" class="btn btn-xs" :disabled="retrying || analyzing" @click="retryCatalogues">
        <span v-if="retrying" class="loading loading-spinner loading-xs" />Try again
      </button>
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
      <div class="library-main flex-1 min-w-0">
        <!-- Library -->
        <div v-if="view === 'library'" class="space-y-3">
          <div v-if="loaded && !papers.length" class="text-sm text-base-content/60 py-10 text-center">
            No papers or books in this collection yet. Add PDFs from the Sources panel, then come back here.
          </div>

          <template v-else>
            <!-- The order of work (R10): each step's state, what it needs and costs -->
            <ol class="library-steps" aria-label="Steps" data-testid="library-steps">
              <li v-for="(step, i) in steps" :key="step.id" class="library-step" :data-state="step.state" :data-testid="`step-${step.id}`">
                <span class="step-mark" aria-hidden="true">
                  <Check v-if="step.state === 'done'" :size="12" :stroke-width="3" /><template v-else>{{ i + 1 }}</template>
                </span>
                <span class="min-w-0">
                  <span class="block text-sm font-medium leading-snug">
                    <button v-if="step.id === 'review' || step.id === 'cite'" class="link link-hover" @click="view = step.id === 'review' ? 'reviews' : 'write'">{{ step.label }}</button>
                    <template v-else>{{ step.label }}</template>
                    <span v-if="step.done !== null" class="ml-1 font-normal text-base-content/65 tabular-nums">{{ step.done }} of {{ step.total }}</span>
                  </span>
                  <span class="block text-xs text-base-content/60 leading-snug">{{ step.hint }}</span>
                  <span v-if="step.id === 'analyze' && step.action" class="mt-1 inline-block" data-testid="research-analyze-banner">
                    <button class="btn btn-xs btn-primary text-xs" :disabled="analyzing" @click="analyzeNew()">
                      <span v-if="analyzing" class="loading loading-spinner loading-xs" />{{ step.action }}
                    </button>
                  </span>
                </span>
              </li>
            </ol>

            <!-- Screen by question -->
            <form class="flex flex-wrap gap-2" @submit.prevent="screen">
              <input
                v-model="question"
                class="input input-bordered input-sm flex-1 min-w-[16rem]"
                placeholder="What are you looking for? e.g. effect of root depth on drought survival"
                aria-label="Question to screen sources against"
                list="research-questions"
              />
              <datalist id="research-questions">
                <option v-for="q in pastQuestions" :key="q" :value="q" />
              </datalist>
              <button class="btn btn-sm" :disabled="!canScreen || screening" :title="screenHint">
                <span v-if="screening" class="loading loading-spinner loading-xs" />
                {{ screenLabel }}
              </button>
            </form>
            <p v-if="screenHint && question.trim()" class="text-xs text-base-content/65 -mt-1 flex flex-wrap items-center gap-2">
              {{ screenHint }}
              <button v-if="!providerId" class="btn btn-xs btn-outline" data-testid="connect-model" @click="ui.connectModel()">Connect a model</button>
            </p>

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
              <label v-if="checkCount || checkOnly" class="flex items-center gap-1 cursor-pointer" data-testid="needs-checking-filter">
                <input v-model="checkOnly" type="checkbox" class="checkbox checkbox-xs" />Needs checking
                <span class="tabular-nums text-base-content/55">({{ checkCount }})</span>
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
                <button
                  v-if="renameCandidates.length || !unanalyzed.length"
                  class="btn btn-xs btn-ghost"
                  :disabled="!renameCandidates.length"
                  @click="openRenamePreview"
                >Rename to descriptive names ({{ renameCandidates.length }})</button>
                <span v-else class="text-base-content/55" data-testid="rename-needs-analysis">Rename to descriptive names: analyze first</span>
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
                  :aria-label="`Select ${displayTitle(p)}`"
                  @change="selection.toggle(p.document_id)"
                />
                <button class="min-w-0 flex-1 text-left" @click="openPaper(p.document_id)">
                  <span class="block text-sm font-medium leading-snug">
                    {{ displayTitle(p) }}
                    <span v-if="p.kind === 'book'" class="badge badge-ghost badge-xs align-middle ml-1">book</span>
                    <span
                      v-if="needsChecking(p)"
                      class="badge badge-warning badge-outline badge-xs align-middle ml-1"
                      :title="checkReasons(p).join('\n')"
                      data-testid="needs-checking"
                    >needs checking</span>
                  </span>
                  <span class="block text-xs text-base-content/60 mt-0.5">
                    <template v-if="p.analyzed">
                      <span :class="doubtful(p, 'authors') ? 'italic' : ''">{{ authorsLine(p) || 'Authors unknown' }}</span><span v-if="p.year"> · {{ p.year }}<span v-if="doubtful(p, 'year')">?</span></span><span v-if="p.venue"> · {{ p.venue }}</span>
                    </template>
                    <template v-else>Not analyzed</template>
                    <span v-if="p.analyzed && p.missing?.length" class="text-warning" :title="`A citation of this source lacks: ${missingLabel(p.missing)}. Open it to add.`"> · cite: no {{ missingLabel(p.missing) }}</span>
                    <span v-if="p.title && !doubtful(p, 'title') && p.filename !== p.suggested_filename" class="text-base-content/50"> · {{ p.filename }}</span>
                  </span>
                  <span v-if="verdicts[p.document_id]?.evidence?.[0]" class="block text-xs text-base-content/70 mt-1 line-clamp-2">
                    “{{ verdicts[p.document_id].evidence[0].quote }}” <span class="text-base-content/55">p. {{ verdicts[p.document_id].evidence[0].page }}</span>
                  </span>
                </button>
                <div class="flex flex-col items-end gap-1 shrink-0 text-xs">
                  <span v-if="verdicts[p.document_id]" class="badge badge-sm" :class="VERDICTS[verdicts[p.document_id].verdict]?.tone">
                    {{ VERDICTS[verdicts[p.document_id].verdict]?.label }}
                  </span>
                  <span class="text-base-content/60 tabular-nums text-right">
                    <button v-if="p.unread_reviews" class="link link-hover text-primary" @click="view = 'reviews'">Unread review</button>
                    <span v-if="p.unread_reviews && (p.cited_by || p.references)"> · </span>
                    <span v-if="p.cited_by" :title="`Cited by ${p.cited_by} of your sources`">cited by {{ p.cited_by }}</span>
                    <span v-if="p.cited_by && p.references"> · </span>
                    <span v-if="p.references" :title="`${p.references} references`">{{ p.references }} ref{{ p.references === 1 ? '' : 's' }}</span>
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
          :details-id="isWide ? openId : null"
          @open="openPaper"
          @close-details="openId = null"
        >
          <!-- On wide screens the map shows the open source in its own side
               panel, so the details never float over the page or the graph. -->
          <template #details>
            <ResearchPaperPanel
              v-if="openId"
              :document-id="openId"
              :collection-id="collectionId"
              :provider-id="providerId"
              :question="question"
              @close="openId = null"
              @open="openPaper"
              @changed="onChanged"
            />
          </template>
        </ResearchMap>

        <ResearchWriting
          v-else-if="view === 'write'"
          :collection-id="collectionId"
          :papers="papers"
          :selected-ids="selectionCount ? scopeIds : []"
          :provider-id="providerId"
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

      <!-- Source panel: beside the list on wide screens, a full sheet on
           narrow ones. Over the map on wide screens it lives in the map's
           own side panel instead (above). -->
      <div
        v-if="openId && !(view === 'map' && isWide)"
        class="fixed inset-0 z-[65] bg-base-100 lg:static lg:z-auto lg:w-[26rem] lg:shrink-0 lg:border lg:border-base-300 lg:rounded-box lg:max-h-[calc(100vh-10rem)] lg:sticky lg:top-2 overflow-hidden"
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
    <dialog :ref="renameModal.dialogRef" class="modal" aria-labelledby="research-rename-title" @close="renameModal.onClosed">
      <div class="modal-box max-w-3xl space-y-3">
        <h3 id="research-rename-title" class="font-semibold">Rename to descriptive names</h3>
        <p class="text-sm text-base-content/70">
          Names are built from each source's title, authors and year as found in it (or in its catalogue record).
          Sources without a known title are left alone. Each keeps its original name, so this can be undone one at a time.
        </p>
        <label class="form-control">
          <span class="label-text text-xs mb-1">Pattern — {{ namingFields }}</span>
          <input v-model="pattern" class="input input-bordered input-sm font-mono" />
        </label>
        <p v-if="renameError" class="text-xs text-error" role="alert">{{ renameError }}</p>
        <div class="max-h-80 overflow-y-auto space-y-3">
          <ul class="divide-y divide-base-300/60 text-xs" data-testid="rename-ready">
            <li v-for="p in renameReady" :key="p.document_id" class="py-1.5 flex items-start gap-2">
              <input v-model="renameChecked" type="checkbox" class="checkbox checkbox-xs mt-0.5" :value="p.document_id" :aria-label="`Rename ${p.filename}`" />
              <div class="min-w-0">
                <div class="text-base-content/50 line-through truncate">{{ p.filename }}</div>
                <div class="truncate">{{ p.newName }}</div>
              </div>
            </li>
          </ul>
          <section v-if="renameDoubtful.length" data-testid="rename-check-first">
            <h4 class="text-xs font-semibold">Check these first</h4>
            <p class="text-xs text-base-content/60 mb-1">
              The title or authors these names are built from need checking. Open a source to correct its details, or tick it to rename anyway.
            </p>
            <ul class="divide-y divide-base-300/60 text-xs">
              <li v-for="p in renameDoubtful" :key="p.document_id" class="py-1.5 flex items-start gap-2">
                <input v-model="renameChecked" type="checkbox" class="checkbox checkbox-xs mt-0.5" :value="p.document_id" :aria-label="`Rename ${p.filename}`" />
                <div class="min-w-0 flex-1">
                  <div class="text-base-content/50 truncate">{{ p.filename }}</div>
                  <div class="truncate text-base-content/70">{{ p.newName }}</div>
                </div>
                <button class="btn btn-xs btn-ghost shrink-0" @click="openFromRename(p.document_id)">Open</button>
              </li>
            </ul>
          </section>
        </div>
        <div class="modal-action">
          <button class="btn btn-sm btn-ghost" @click="renameModal.close()">Cancel</button>
          <button class="btn btn-sm btn-primary" :disabled="!renameChecked.length || renaming" @click="renameSelected">
            <span v-if="renaming" class="loading loading-spinner loading-xs" />Rename {{ renameChecked.length }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
    </dialog>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Check, Library, ListChecks, Network, NotebookPen, Quote } from 'lucide-vue-next'
import ResearchPaperPanel from './ResearchPaperPanel.vue'
import ResearchReviews from './ResearchReviews.vue'
import ResearchWriting from './ResearchWriting.vue'
import { lazyView } from '../utils/lazyView'
import { librarySteps } from '../utils/libraryFlow.js'

// The map brings the canvas force graph; load it only when the map is opened.
// lazyView says what is loading, and offers Try again if the chunk fails (F19).
const ResearchMap = lazyView(() => import('./ResearchMap.vue'), { name: 'Citation map' })
import { researchApi, VERDICTS, authorsLine, checkReasons, displayTitle, doubtful } from '../utils/researchApi.js'
import { createJobPoller, finishNotice, isActive } from '../utils/researchJobs.js'
import { useModal } from '../composables/useModal'
import { missingLabel } from '../utils/citations.js'
import { useCollectionStore } from '../stores/collectionStore'
import { useProviderStore } from '../stores/providerStore'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useSelectionStore } from '../stores/selectionStore'

const views = [
  // The tab itself is the Library; its first view is the list of papers.
  { id: 'library', label: 'Papers', short: 'Papers', icon: Library },
  { id: 'map', label: 'Citation map', short: 'Map', icon: Network },
  { id: 'reviews', label: 'Reviews', short: 'Reviews', icon: NotebookPen },
  { id: 'write', label: 'Cite & check', short: 'Cite', icon: Quote },
]

const collectionStore = useCollectionStore()
const providerStore = useProviderStore()
const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()
const selection = useSelectionStore()

const collectionId = computed(() => collectionStore.currentCollectionId)
const providerId = computed(() => providerStore.activeProviderId || null)

// Tailwind's lg breakpoint: where panels sit beside content, not over it.
const wideQuery = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(min-width: 1024px)') : null
const isWide = ref(wideQuery ? wideQuery.matches : true)
const onWide = (e) => { isWide.value = e.matches }
wideQuery?.addEventListener?.('change', onWide)

// Shared with the ribbon, which can open the Library on a given view.
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
// The question the verdicts above answer: they lag a typed question by a request.
const verdictsFor = ref('')
const analyzing = ref(false)
const screening = ref(false)
const renameOpen = ref(false)
// A real modal (focus trap, inert page, Escape); closing by any route lands in onClose.
const renameModal = useModal({ onClose: () => { renameOpen.value = false } })
const renaming = ref(false)
const pattern = ref('')
const renameChecked = ref([])
let previewTimer = null
const checkOnly = ref(false)
// The server's job list for this collection, kept by every poll: the job
// strip reads it, so it shows the same thing however a job was started.
const jobs = ref([])
const activeJobs = computed(() => jobs.value.filter(isActive))
const stopping = ref(new Set())
// Analyze and Screen each follow their own jobs: one starting never stops
// the other's poll (whose spinner would then never clear).
const poller = createJobPoller(async (cid) => {
  const list = (await researchApi.jobs(cid)).jobs || []
  if (cid === collectionId.value) jobs.value = list
  return list
})
const ANALYZE_WORDS = { noun: 'Analysis', past: 'analyzed', failed: 'could not be read' }
const SCREEN_WORDS = { noun: 'Screening', past: 'screened', failed: 'could not be screened' }
const REVIEW_WORDS = { noun: 'Reviews', past: 'reviewed', failed: 'could not be reviewed' }
const RESOLVE_WORDS = { noun: 'The search for open copies', past: 'looked up', failed: 'could not be looked up' }
// The job kinds as stored (assess and brief predate R9's names).
const KIND = { analyze: 'analyze', assess: 'screen', screen: 'screen', brief: 'review', review: 'review', resolve: 'resolve' }
const retrying = ref(false)
const watched = new Set()

function jobLabel(job) {
  const kind = KIND[job.kind]
  if (job.status === 'queued' && job.params?.not_before) return 'Reviews scheduled'
  if (kind === 'analyze') {
    if (job.params?.parts > 1) return `Analyzing (part ${job.params.part} of ${job.params.parts})`
    return job.params?.automatic ? 'Analyzing new sources' : 'Analyzing'
  }
  if (kind === 'resolve') return job.params?.filename ? `Finding open copies for ${job.params.filename}` : 'Finding open copies'
  if (kind === 'screen') return job.params?.question ? `Screening for “${job.params.question}”` : 'Screening'
  if (kind === 'review') return job.params?.depth === 'thorough' ? 'Preparing thorough reviews' : 'Preparing reviews'
  return 'Working'
}

// Follow jobs to their end: spinners, the strip and the finish notice.
function follow(ids, kind) {
  const fresh = ids.filter(id => !watched.has(id))
  if (!fresh.length) return
  fresh.forEach(id => watched.add(id))
  if (kind === 'analyze') analyzing.value = true
  if (kind === 'screen') screening.value = true
  poller.watch(fresh, {
    context: collectionId.value,
    onProgress: (list) => {
      if (!list.some(j => j.done)) return
      if (kind === 'screen') loadVerdicts()
      else loadPapers()
    },
    onFinished: (list, extra) => {
      fresh.forEach(id => { watched.delete(id); stopping.value.delete(id) })
      stopping.value = new Set(stopping.value)
      if (kind === 'analyze') { analyzing.value = false; loadPapers(); refreshKey.value++ }
      if (kind === 'screen') { screening.value = false; loadVerdicts() }
      if (kind === 'review' || kind === 'resolve') { loadPapers(); refreshKey.value++ }
      reportFinish(list, { analyze: ANALYZE_WORDS, screen: SCREEN_WORDS, review: REVIEW_WORDS, resolve: RESOLVE_WORDS }[kind], extra)
    },
  })
}

// On opening the tab (or a reload): pick up whatever is still running.
async function attachRunningJobs() {
  const cid = collectionId.value
  if (!cid) return
  try {
    const list = (await researchApi.jobs(cid)).jobs || []
    if (cid !== collectionId.value) return
    jobs.value = list
    for (const kind of ['analyze', 'screen', 'review', 'resolve']) {
      const ids = list.filter(j => isActive(j) && KIND[j.kind] === kind).map(j => j.job_id)
      if (ids.length) follow(ids, kind)
    }
  } catch { /* the strip is an extra; the buttons still work */ }
}

async function retryCatalogues() {
  retrying.value = true
  try {
    const out = await researchApi.retryLookups(collectionId.value)
    if (out.job) {
      jobs.value = [...jobs.value.filter(j => j.job_id !== out.job.job_id), out.job]
      follow([out.job.job_id], 'analyze')
    } else {
      loadPapers()
    }
  } catch (err) {
    ui.toastError(err, 'Could not ask the catalogues again')
  } finally {
    retrying.value = false
  }
}

async function stopJob(job) {
  stopping.value = new Set([...stopping.value, job.job_id])
  try {
    await researchApi.cancelJob(collectionId.value, job.job_id)
  } catch (err) {
    stopping.value.delete(job.job_id)
    stopping.value = new Set(stopping.value)
    ui.toastError(err, 'Could not stop the job')
  }
}

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

// Runs on question edits (debounced) and on collection switches; only the
// newest reply may land, or a slow one would show another question's
// verdicts under this one.
let verdictsSeq = 0
async function loadVerdicts() {
  const q = question.value.trim()
  const mine = ++verdictsSeq
  try {
    const data = await researchApi.readingList(collectionId.value, q || null)
    if (mine !== verdictsSeq) return
    pastQuestions.value = data.questions || []
    verdicts.value = q ? Object.fromEntries((data.items || []).map(i => [i.document_id, i])) : {}
    verdictsFor.value = q
    if (q && Object.keys(verdicts.value).length) sort.value = 'verdict'
  } catch { /* the reading list is an extra */ }
}

watch(collectionId, () => {
  openId.value = null
  verdicts.value = {}
  loaded.value = false
  // Jobs belong to a collection: the previous one's watches end here.
  poller.stopAll()
  watched.clear()
  jobs.value = []
  analyzing.value = false
  screening.value = false
  loadPapers()
  loadVerdicts()
  attachRunningJobs()
}, { immediate: true })

// Reviews started from the Reviews view or a source panel join the strip.
watch(refreshKey, () => attachRunningJobs())

let verdictTimer = null
watch(question, () => {
  clearTimeout(verdictTimer)
  verdictTimer = setTimeout(loadVerdicts, 400)
})

// New sources finished indexing (e.g. downloaded references): refresh.
// A4: new papers are analysed on their own after indexing; the strip
// picks that job up here, and the records arrive without a click.
watch(() => jobsStore.dataRefreshTick, () => {
  if (jobsStore.dataRefreshCollectionId !== collectionId.value) return
  loadPapers()
  attachRunningJobs()
})

onBeforeUnmount(() => {
  poller.stopAll(); clearTimeout(verdictTimer); clearTimeout(previewTimer)
  wideQuery?.removeEventListener?.('change', onWide)
})

// The selection can hold spreadsheets or code too; here it counts only the
// sources this view works on. With nothing selected, the scope is all of them.
const selectedSet = computed(() => new Set(selection.currentIds))
const selectedPapers = computed(() => papers.value.filter(p => selectedSet.value.has(p.document_id)))
const selectionCount = computed(() => selectedPapers.value.length)
const scoped = computed(() => selectionCount.value ? selectedPapers.value : papers.value)
const scopeIds = computed(() => scoped.value.map(p => p.document_id))
watch(selectionCount, (n) => { if (!n) selectedOnly.value = false })

const unanalyzed = computed(() => scoped.value.filter(p => !p.analyzed))
// R3: records with a title, authors or year that are not known to be right.
const needsChecking = (p) => Object.keys(p.needs_checking || {}).length > 0
const checkCount = computed(() => scoped.value.filter(needsChecking).length)
watch(checkCount, (n) => { if (!n) checkOnly.value = false })
const hasVerdicts = computed(() => Object.keys(verdicts.value).length > 0)
const shown = computed(() => {
  const f = filter.value.trim().toLowerCase()
  let list = selectedOnly.value ? selectedPapers.value : papers.value
  if (checkOnly.value) list = list.filter(needsChecking)
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
    title: (a, b) => displayTitle(a).localeCompare(displayTitle(b)),
  }
  return [...list].sort(by[sort.value] || by.recent)
})

// Screening skips sources already screened for this question (R10); when
// every one has been, Screen asks them all again.
const currentVerdicts = computed(() => (question.value.trim() && verdictsFor.value === question.value.trim() ? verdicts.value : {}))
const alreadyScreened = computed(() => scopeIds.value.filter(id => currentVerdicts.value[id]))
const screenTargets = computed(() => {
  if (!question.value.trim()) return scopeIds.value
  const fresh = scopeIds.value.filter(id => !currentVerdicts.value[id])
  return fresh.length ? fresh : scopeIds.value
})
const screenLabel = computed(() => {
  const n = screenTargets.value.length
  const noun = `source${n === 1 ? '' : 's'}`
  if (question.value.trim() && alreadyScreened.value.length) {
    return alreadyScreened.value.length === scopeIds.value.length ? `Screen all ${n} again` : `Screen ${n} more ${noun}`
  }
  return `Screen ${n} ${selectionCount.value ? 'selected ' : ''}${noun}`
})
const steps = computed(() => librarySteps({
  papers: scoped.value, question: question.value, screened: Object.keys(currentVerdicts.value),
  hasModel: Boolean(providerId.value), canWrite: canWrite.value,
}))
const canScreen = computed(() => canWrite.value && providerId.value && question.value.trim().length >= 3 && screenTargets.value.length)
const screenHint = computed(() => {
  if (!providerId.value) return 'Screening needs a model. Without one, open a source to see where it discusses your question.'
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

// Any finish that is not clean (failures, a budget stop, a cancel) says so.
function reportFinish(jobs, words, extra) {
  const notice = finishNotice(jobs, words, extra)
  if (notice) ui.notify(notice.message, notice.type, { duration: notice.type === 'error' ? 8000 : 6000 })
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
    // A big selection comes back as several jobs (one after another).
    const started = out.jobs?.length ? out.jobs : [out.job]
    jobs.value = [...jobs.value.filter(j => !started.some(s => s.job_id === j.job_id)), ...started]
    follow(started.map(j => j.job_id), 'analyze')
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
      kind: 'screen', question: question.value.trim(), document_ids: screenTargets.value,
    }, providerId.value)
    sort.value = 'verdict'
    jobs.value = [...jobs.value.filter(j => j.job_id !== out.job.job_id), out.job]
    follow([out.job.job_id], 'screen')
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
let previewSeq = 0
async function refreshPreview({ checkAll = false } = {}) {
  const ids = renameCandidates.value.map(p => p.document_id)
  if (!ids.length) { renamePreview.value = []; return }
  const mine = ++previewSeq
  try {
    const out = await researchApi.renameMany(collectionId.value, ids, pattern.value, true)
    if (mine !== previewSeq) return
    renameError.value = ''
    const ready = new Set(renameCandidates.value.filter(p => p.rename_ready).map(p => p.document_id))
    renamePreview.value = out.renamed.map(r => ({
      document_id: r.document_id, filename: r.previous_filename, newName: r.filename, ready: ready.has(r.document_id),
    }))
    const valid = new Set(renamePreview.value.map(p => p.document_id))
    // A3: only names built from a title and authors known to be right are
    // ticked to begin with; the rest wait under "Check these first".
    renameChecked.value = checkAll
      ? renamePreview.value.filter(p => p.ready).map(p => p.document_id)
      : renameChecked.value.filter(id => valid.has(id))
  } catch (err) {
    if (mine !== previewSeq) return
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
  renameModal.open()
  await refreshPreview({ checkAll: true })
}

const renameReady = computed(() => renamePreview.value.filter(p => p.ready))
const renameDoubtful = computed(() => renamePreview.value.filter(p => !p.ready))

function openFromRename(id) {
  renameModal.close()
  openPaper(id)
}

// Every source renamed in one go gets its arrival name back (each kept it).
async function undoRename(ids) {
  const cid = collectionId.value
  const results = await Promise.allSettled(ids.map(id => researchApi.restoreName(cid, id)))
  const failed = results.filter(r => r.status === 'rejected').length
  ui.notify(failed ? `Restored ${ids.length - failed} names; ${failed} could not be restored` : `Restored ${ids.length} original names`,
    failed ? 'warning' : 'success')
  ui.sourcesChanged()
  onChanged()
}

async function renameSelected() {
  renaming.value = true
  try {
    const out = await researchApi.renameMany(collectionId.value, renameChecked.value, pattern.value)
    const ids = out.renamed.map(r => r.document_id)
    ui.notify(`Renamed ${out.renamed.length} source${out.renamed.length === 1 ? '' : 's'}`
      + (out.skipped.length ? ` · ${out.skipped.length} left as they were` : ''), 'success',
    ids.length > 1 ? { duration: 12000, action: { label: 'Undo all', run: () => undoRename(ids) } } : {})
    renameModal.close()
    ui.sourcesChanged()
    onChanged()
  } catch (err) {
    ui.toastError(err, 'Rename failed')
  } finally {
    renaming.value = false
  }
}
</script>

<style scoped>
/* The four steps read as one line of progress, not as cards: a numbered
   mark, the step and its count, and what it needs. */
/* A7: the column the list sits in decides, not the window: with the Sources
   panel and a source open, a 1280px window leaves the list about 30rem. */
.library-main { container-type: inline-size; }
.library-steps { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0.75rem 1rem; padding-block: 0.25rem; }
@container (min-width: 46rem) { .library-steps { grid-template-columns: repeat(4, minmax(0, 1fr)); } }
.library-step { display: flex; align-items: flex-start; gap: 0.5rem; min-width: 0; }
.step-mark { display: inline-grid; place-items: center; flex-shrink: 0; width: 1.25rem; height: 1.25rem; margin-top: 0.0625rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; border: 1.5px solid color-mix(in oklch, var(--color-base-content) 30%, transparent); color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
.library-step[data-state='partial'] .step-mark { border-color: var(--color-primary); color: var(--color-primary); }
.library-step[data-state='done'] .step-mark { border-color: var(--color-primary); background: var(--color-primary); color: var(--color-primary-content); }
</style>
