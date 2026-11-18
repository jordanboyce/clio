<template>
  <div class="@container space-y-4" data-testid="research-reviews">
    <!-- Toolbar: which reviews, and the way to prepare more -->
    <div class="flex flex-wrap items-center gap-2">
      <div class="segmented" role="tablist" aria-label="Which reviews">
        <button
          class="segment"
          role="tab"
          :aria-selected="!unreadOnly"
          :class="!unreadOnly ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
          @click="unreadOnly = false"
        >All</button>
        <button
          class="segment"
          role="tab"
          :aria-selected="unreadOnly"
          data-testid="reviews-unread-only"
          :class="unreadOnly ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
          @click="unreadOnly = true"
        >Unread<span v-if="unread" class="tabular-nums text-base-content/60">{{ unread }}</span></button>
      </div>
      <span class="flex-1" />
      <button
        v-if="canWrite"
        class="btn btn-sm"
        :class="prepareOpen ? 'btn-active' : 'btn-ghost'"
        :aria-expanded="prepareOpen"
        data-testid="prepare-reviews-toggle"
        @click="prepareOpen = !prepareOpen"
      ><Plus :size="14" aria-hidden="true" />Prepare reviews</button>
    </div>

    <!-- Prepare: disclosed on request, open by itself when there is nothing to read yet -->
    <section v-if="canWrite && prepareOpen" class="rounded-box bg-base-200/60 p-4 space-y-3" data-testid="prepare-reviews">
      <p class="text-sm text-base-content/70 max-w-[65ch]">
        Clio reads each source and writes a review to skim later. Every statement quotes the source; anything it
        cannot quote is left out. It runs on the server, so you can close this tab.
      </p>
      <div class="grid gap-3 @lg:grid-cols-2">
        <label class="form-control">
          <span class="field-label">Sources</span>
          <select v-model="scope" class="select select-bordered select-sm w-full">
            <option value="unreviewed">Without a review yet ({{ unreviewedCount }})</option>
            <option v-if="selectedIds.length" value="selected">Selected sources ({{ selectedIds.length }})</option>
            <option value="all">All ({{ papers.length }})</option>
          </select>
        </label>
        <label class="form-control">
          <span class="field-label">Depth</span>
          <select v-model="depth" class="select select-bordered select-sm w-full">
            <option value="brief">Brief: key passages, one pass</option>
            <option value="thorough">Thorough: the whole text, part by part</option>
          </select>
        </label>
        <label class="form-control @lg:col-span-2">
          <span class="field-label">Your question (optional)</span>
          <input v-model="question" class="input input-bordered input-sm w-full" placeholder="Adds a “For your question” section to each review" />
        </label>
      </div>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
        <span class="field-label mb-0">Start</span>
        <label class="flex items-center gap-1.5"><input v-model="when" type="radio" value="now" class="radio radio-xs" />Now</label>
        <label class="flex items-center gap-1.5"><input v-model="when" type="radio" value="later" class="radio radio-xs" />At</label>
        <input v-model="atTime" type="time" class="input input-bordered input-sm w-28" :disabled="when !== 'later'" aria-label="Start time" />
        <span v-if="when === 'later'" class="text-xs text-base-content/60">{{ startLabel }}</span>
      </div>
      <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
        <button class="btn btn-sm btn-primary" :disabled="!providerId || starting || !targetIds.length" @click="start">
          <span v-if="starting" class="loading loading-spinner loading-xs" />
          {{ when === 'later' ? 'Schedule' : 'Start' }} {{ targetIds.length }} review{{ targetIds.length === 1 ? '' : 's' }}
        </button>
        <span v-if="!providerId" class="text-xs text-base-content/70">Reviews need a model. Connect one in Settings first.</span>
        <span v-else-if="targetIds.length" class="text-xs text-base-content/60">{{ providerName }} · {{ reviewCost(targetIds.length, depth) }}</span>
        <span v-else class="text-xs text-base-content/60">Every source in this scope already has a review.</span>
      </div>
    </section>

    <!-- Background work: running jobs stay in view; finished ones fold away -->
    <section v-if="jobs.length" class="space-y-1" data-testid="review-jobs">
      <ul v-if="activeJobs.length" class="divide-y divide-base-300/60">
        <li v-for="job in activeJobs" :key="job.job_id" class="py-2 text-sm flex flex-wrap items-center gap-x-3 gap-y-1" data-testid="job-row">
          <span class="loading loading-spinner loading-xs text-base-content/50" aria-hidden="true" />
          <div class="min-w-0 flex-1">
            <div class="truncate">
              <span class="font-medium">{{ jobStatus(job) }}</span> · {{ jobTitle(job) }}
              <span class="text-base-content/60 tabular-nums">· {{ job.done }} of {{ job.total }}<span v-if="job.failed">, {{ job.failed }} failed</span></span>
            </div>
            <div v-if="job.current" class="text-xs text-base-content/60 truncate">Now: {{ job.current }}</div>
          </div>
          <progress class="progress w-24" :value="job.done + job.failed" :max="job.total || 1" />
          <button v-if="canWrite" class="btn btn-xs btn-ghost" @click="cancel(job)">Stop</button>
        </li>
      </ul>
      <details v-if="finishedJobs.length" class="group text-sm" data-testid="finished-jobs">
        <summary class="flex cursor-pointer items-center gap-1.5 py-1 text-xs text-base-content/65 hover:text-base-content list-none">
          <ChevronRight :size="13" class="transition-transform group-open:rotate-90" aria-hidden="true" />
          Finished background work ({{ finishedJobs.length }})<span v-if="failedJobs" class="text-warning"> · {{ failedJobs }} with problems</span>
        </summary>
        <ul class="divide-y divide-base-300/60 pl-5">
          <li v-for="job in finishedJobs.slice(0, 8)" :key="job.job_id" class="py-1.5 flex items-start gap-3" data-testid="job-row">
            <div class="min-w-0 flex-1">
              <div class="truncate">
                {{ jobTitle(job) }}
                <span class="text-base-content/60 tabular-nums">· {{ jobStatus(job).toLowerCase() }} · {{ job.done }} of {{ job.total }}<span v-if="job.failed">, {{ job.failed }} failed</span></span>
              </div>
              <details v-if="job.errors?.length" class="text-xs text-base-content/65">
                <summary class="cursor-pointer">What failed</summary>
                <ul><li v-for="(e, i) in job.errors" :key="i">{{ e.filename || 'Job' }}: {{ e.error }}</li></ul>
              </details>
            </div>
            <button
              v-if="canWrite"
              class="btn btn-xs btn-ghost btn-square text-base-content/60"
              :aria-label="`Remove ${jobTitle(job)} from this list`"
              title="Remove from this list. Its reviews are kept."
              @click="removeJob(job)"
            ><X :size="13" /></button>
          </li>
        </ul>
        <div class="flex items-center gap-3 pl-5 pt-1">
          <span v-if="finishedJobs.length > 8" class="text-xs text-base-content/60">and {{ finishedJobs.length - 8 }} older</span>
          <button
            v-if="canWrite"
            class="btn btn-xs btn-ghost"
            :disabled="clearing"
            title="Removes finished jobs from this list. Their reviews are kept."
            data-testid="clear-finished-jobs"
            @click="clearFinished"
          >Clear finished</button>
        </div>
      </details>
    </section>

    <!-- States -->
    <p v-if="loadState === 'loading'" class="flex items-center gap-2 py-10 justify-center text-sm text-base-content/65" data-testid="reviews-loading">
      <span class="loading loading-spinner loading-sm" />Loading reviews…
    </p>
    <div v-else-if="loadState === 'error'" class="py-10 text-center space-y-2" role="alert" data-testid="reviews-error">
      <p class="text-sm">The reviews could not be loaded.</p>
      <p class="text-xs text-base-content/65">{{ loadError }}</p>
      <button class="btn btn-sm" @click="load">Try again</button>
    </div>
    <div v-else-if="!briefs.length" class="py-10 text-center space-y-2 text-sm text-base-content/70" data-testid="reviews-empty">
      <template v-if="unreadOnly">
        <p>Nothing unread.</p>
        <button class="btn btn-sm btn-ghost" @click="unreadOnly = false">Show all reviews</button>
      </template>
      <template v-else>
        <p class="text-base-content">No reviews yet.</p>
        <p v-if="!canWrite">Reviews appear here once someone with edit access to this collection prepares them.</p>
        <p v-else-if="!providerId">Reviews need a model. Connect one in Settings, then prepare reviews here.</p>
        <p v-else>Prepare reviews above. Each one quotes the source for every statement, with its page.</p>
      </template>
    </div>

    <!-- List and reader: side by side when there is room, one at a time when not -->
    <div v-else class="@3xl:grid @3xl:grid-cols-[minmax(15rem,19rem)_minmax(0,1fr)] @3xl:gap-8">
      <div :class="current ? 'hidden @3xl:block' : ''">
        <ul class="divide-y divide-base-300/70 border-y border-base-300/70" data-testid="review-list">
          <li
            v-for="b in queue"
            :key="b.id"
            class="group relative"
            :class="current?.id === b.id ? 'bg-base-200/80' : 'hover:bg-base-200/40'"
            data-testid="review-row"
          >
            <button
              class="flex w-full items-start gap-2.5 py-3 pl-2 pr-10 text-left"
              :aria-current="current?.id === b.id ? 'true' : undefined"
              @click="select(b)"
            >
              <span
                class="mt-1.5 h-2 w-2 shrink-0 rounded-full"
                :class="b.read_at ? 'bg-transparent' : 'bg-primary'"
                :aria-label="b.read_at ? 'Read' : 'Unread'"
                role="img"
              />
              <span class="min-w-0 flex-1">
                <span class="block text-sm leading-snug line-clamp-2" :class="b.read_at ? '' : 'font-semibold'">{{ b.title || b.filename }}</span>
                <span class="mt-0.5 block truncate text-xs text-base-content/60">
                  {{ [authorsShort(b.authors), b.year].filter(Boolean).join(' · ') || b.filename }}
                </span>
                <span v-if="confirmingId !== b.id" class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                  <span
                    v-if="verdictOf(b)"
                    class="badge badge-sm"
                    :class="VERDICTS[verdictOf(b).verdict]?.tone"
                    :title="`Screened for “${b.question}”`"
                  >{{ VERDICTS[verdictOf(b).verdict]?.label }}</span>
                  <span class="inline-flex items-center gap-1" :class="statusTone(b)">
                    <component :is="statusIcon(b)" :size="12" aria-hidden="true" />{{ reviewStatus(b).label }}
                  </span>
                </span>
              </span>
            </button>
            <div v-if="canWrite" class="absolute right-1 top-2">
              <button
                v-if="confirmingId !== b.id"
                class="btn btn-ghost btn-xs btn-square hover-reveal text-base-content/60 hover:text-error"
                :aria-label="`Delete the review of ${b.title || b.filename}`"
                title="Delete this review"
                @click="confirmingId = b.id"
              ><Trash2 :size="13" /></button>
            </div>
            <div v-if="canWrite && confirmingId === b.id" class="flex flex-wrap items-center gap-2 pb-3 pl-6 text-xs">
              <span class="text-base-content/70">Delete this review?</span>
              <button class="btn btn-xs btn-error" @click="remove(b)">Delete permanently</button>
              <button class="btn btn-xs btn-ghost" @click="confirmingId = null">Keep</button>
            </div>
          </li>
        </ul>
        <div v-if="canWrite && !unreadOnly" class="flex flex-wrap items-center gap-2 pt-3 text-xs">
          <template v-if="confirmingAll">
            <span class="text-base-content/70">Also removes screening verdicts.</span>
            <button class="btn btn-xs btn-error" :disabled="deletingAll" data-testid="delete-all-reviews-confirm" @click="removeAll">
              Delete all permanently
            </button>
            <button class="btn btn-xs btn-ghost" @click="confirmingAll = false">Keep</button>
          </template>
          <button v-else class="btn btn-xs btn-ghost text-base-content/60 hover:text-error" data-testid="delete-all-reviews" @click="confirmingAll = true">
            Delete all reviews…
          </button>
        </div>
      </div>

      <!-- Reader -->
      <article
        v-if="current"
        ref="reader"
        class="min-w-0 max-w-[72ch]"
        aria-labelledby="review-reader-title"
        data-testid="review-reader"
      >
        <button class="btn btn-ghost btn-sm -ml-2 mb-2 @3xl:hidden" @click="openId = null">
          <ArrowLeft :size="14" aria-hidden="true" />All reviews
        </button>
        <header class="space-y-2 border-b border-base-300/70 pb-4">
          <p class="text-xs text-base-content/60">
            {{ depthLabel(current.depth) }} · {{ when2(current.created_at) }}<span v-if="current.question"> · for “{{ current.question }}”</span>
          </p>
          <h3 id="review-reader-title" class="text-lg font-semibold leading-snug [overflow-wrap:anywhere]">{{ current.title || current.filename }}</h3>
          <p v-if="current.authors?.length || current.year" class="text-sm text-base-content/70">
            {{ [authorList(current.authors), current.year].filter(Boolean).join(' · ') }}
          </p>
          <p class="flex flex-wrap items-center gap-2 text-sm">
            <span v-if="verdictOf(current)" class="badge badge-sm" :class="VERDICTS[verdictOf(current).verdict]?.tone">
              {{ VERDICTS[verdictOf(current).verdict]?.label }}
            </span>
            <span class="inline-flex items-center gap-1" :class="statusTone(current)">
              <component :is="statusIcon(current)" :size="14" aria-hidden="true" />{{ reviewStatus(current).label }}
            </span>
          </p>
          <div class="flex flex-wrap items-center gap-2 pt-1">
            <button class="btn btn-sm btn-outline" @click="$emit('open', current.document_id)">
              <BookOpen :size="14" aria-hidden="true" />Source details
            </button>
            <a class="btn btn-sm btn-ghost" :href="pageUrl(collectionId, current.document_id)" target="_blank" rel="noopener">
              <FileText :size="14" aria-hidden="true" />Open the file
            </a>
            <button class="btn btn-sm btn-ghost" @click="markRead(current, !current.read_at)">
              {{ current.read_at ? 'Mark unread' : 'Mark read' }}
            </button>
            <button v-if="nextUnread" class="btn btn-sm btn-ghost" data-testid="next-unread" @click="select(nextUnread)">
              Next unread<ArrowRight :size="14" aria-hidden="true" />
            </button>
          </div>
        </header>
        <div class="pt-5">
          <ResearchBrief :brief="current" :collection-id="collectionId" size="md" />
          <div v-if="!verifiedCount(current) && canWrite && providerId && current.depth !== 'thorough'" class="pt-3">
            <button class="btn btn-sm" :disabled="starting" @click="reviewAgain(current)">
              <span v-if="starting" class="loading loading-spinner loading-xs" />Try a thorough review
            </button>
          </div>
        </div>
      </article>
      <div v-else class="hidden @3xl:block py-10 text-sm text-base-content/70" data-testid="review-reader-empty">
        <template v-if="firstUnread">
          <p>{{ unread }} unread review{{ unread === 1 ? '' : 's' }}.</p>
          <button class="btn btn-sm btn-ghost -ml-3 mt-1 max-w-full" @click="select(firstUnread)">
            <span class="truncate">Start with “{{ firstUnread.title || firstUnread.filename }}”</span><ArrowRight :size="14" aria-hidden="true" />
          </button>
        </template>
        <p v-else>Choose a review to read it.</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { AlertTriangle, ArrowLeft, ArrowRight, BookOpen, CheckCircle2, ChevronRight, FileText, Plus, Trash2, X } from 'lucide-vue-next'
import ResearchBrief from './ResearchBrief.vue'
import { JOB_STATE_LABELS, isActive } from '../utils/researchJobs.js'
import { researchApi, authorsShort, nextLocalTime, pageUrl, VERDICTS } from '../utils/researchApi.js'
import { depthLabel, reviewQueue, reviewStatus, verifiedCount } from '../utils/reviewView.js'
import { reviewCost } from '../utils/libraryFlow.js'
import { getProviderDisplayName } from '../utils/aiProviders.js'
import { useUiStore } from '../stores/uiStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  papers: { type: Array, default: () => [] },
  selectedIds: { type: Array, default: () => [] },
  providerId: { type: String, default: null },
  canWrite: { type: Boolean, default: false },
  refreshKey: { type: Number, default: 0 },
})
const emit = defineEmits(['open', 'changed'])

const ui = useUiStore()
const jobs = ref([])
const briefs = ref([])
const loadState = ref('loading')
const loadError = ref('')
const confirmingId = ref(null)
const confirmingAll = ref(false)
const deletingAll = ref(false)
const unread = ref(0)
const unreadOnly = ref(false)
const openId = ref(null)
const prepareOpen = ref(false)
const reader = ref(null)
const verdicts = ref({})
const scope = ref('unreviewed')
// A selection made in the Sources panel is the obvious target.
watch(() => props.selectedIds.length, (n) => {
  if (n) scope.value = 'selected'
  else if (scope.value === 'selected') scope.value = 'unreviewed'
}, { immediate: true })
const depth = ref('brief')
const question = ref('')
const when = ref('now')
const atTime = ref('01:00')
const starting = ref(false)
let timer = null
let loadedOnce = false

const providerName = computed(() => props.providerId ? getProviderDisplayName(props.providerId) : '')
const reviewedIds = computed(() => new Set(props.papers.filter(p => p.review_count > 0).map(p => p.document_id)))
const unreviewedCount = computed(() => props.papers.filter(p => !reviewedIds.value.has(p.document_id)).length)
const targetIds = computed(() => {
  if (scope.value === 'selected') return props.selectedIds
  if (scope.value === 'all') return props.papers.map(p => p.document_id)
  return props.papers.filter(p => !reviewedIds.value.has(p.document_id)).map(p => p.document_id)
})
const startLabel = computed(() => {
  const iso = nextLocalTime(atTime.value)
  if (!iso) return ''
  const at = new Date(iso)
  const today = at.toDateString() === new Date().toDateString()
  return `${today ? 'today' : 'tomorrow'} at ${at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
})

const activeJobs = computed(() => jobs.value.filter(isActive))
const finishedJobs = computed(() => jobs.value.filter(j => !isActive(j)))
const failedJobs = computed(() => finishedJobs.value.filter(j => j.failed || ['failed', 'done_with_errors'].includes(j.status)).length)
const clearing = ref(false)

// The queue's order is fixed when the list loads, so a review does not jump
// down the list the moment it is opened and marked read.
const order = ref([])
const queue = computed(() => {
  const rank = new Map(order.value.map((id, i) => [id, i]))
  return [...briefs.value].sort((a, b) => (rank.get(a.id) ?? -1) - (rank.get(b.id) ?? -1))
})
const current = computed(() => briefs.value.find(b => b.id === openId.value) || null)
const firstUnread = computed(() => queue.value.find(b => !b.read_at) || null)
const nextUnread = computed(() => queue.value.find(b => !b.read_at && b.id !== openId.value) || null)

async function load() {
  if (!loadedOnce) loadState.value = 'loading'
  try {
    const [j, b] = await Promise.all([
      researchApi.jobs(props.collectionId),
      researchApi.reviews(props.collectionId, { unread: unreadOnly.value }),
    ])
    const wasActive = jobs.value.some(isActive)
    jobs.value = j.jobs || []
    briefs.value = b.reviews || []
    order.value = reviewQueue(briefs.value).map(r => r.id)
    unread.value = b.unread || 0
    if (!loadedOnce && !briefs.value.length && !unreadOnly.value && props.canWrite) prepareOpen.value = true
    loadedOnce = true
    loadState.value = 'ready'
    if (openId.value && !current.value) openId.value = null
    if (wasActive && !jobs.value.some(isActive)) emit('changed')
    loadVerdicts()
  } catch (err) {
    if (loadedOnce) ui.toastError(err, 'Could not load reviews')
    else {
      loadState.value = 'error'
      loadError.value = err?.message || ''
    }
  }
  schedule()
}

// The screening verdict for each review's own question, when there is one:
// the verdict is what a list of reviews should say at a glance.
async function loadVerdicts() {
  const questions = [...new Set(briefs.value.map(b => b.question).filter(Boolean))].slice(0, 4)
  if (!questions.length) { verdicts.value = {}; return }
  const out = {}
  await Promise.all(questions.map(async (q) => {
    try {
      const data = await researchApi.readingList(props.collectionId, q)
      for (const item of data?.items || []) out[`${q}|${item.document_id}`] = item
    } catch { /* verdicts are an extra */ }
  }))
  verdicts.value = out
}
const verdictOf = (b) => (b?.question ? verdicts.value[`${b.question}|${b.document_id}`] : null)

function schedule() {
  clearTimeout(timer)
  if (jobs.value.some(isActive)) timer = setTimeout(load, 5000)
}

watch(() => props.collectionId, () => { loadedOnce = false; openId.value = null })
watch(() => [props.collectionId, props.refreshKey, unreadOnly.value], load, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

async function start() {
  starting.value = true
  try {
    const out = await researchApi.startJob(props.collectionId, {
      kind: 'review',
      depth: depth.value,
      document_ids: targetIds.value,
      question: question.value.trim() || null,
      not_before: when.value === 'later' ? nextLocalTime(atTime.value) : null,
      skip_reviewed: scope.value === 'unreviewed',
    }, props.providerId)
    ui.notify(out.job ? (when.value === 'later' ? `Scheduled for ${startLabel.value}` : 'Reviews started') : (out.message || 'Nothing to do'),
      out.job ? 'success' : 'info')
    if (out.job) prepareOpen.value = false
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not start the reviews')
  } finally {
    starting.value = false
  }
}

// A review that verified nothing: the next step is to read more of the text.
async function reviewAgain(b) {
  starting.value = true
  try {
    await researchApi.startJob(props.collectionId, {
      kind: 'review', depth: 'thorough', document_ids: [b.document_id], question: b.question || null, skip_reviewed: false,
    }, props.providerId)
    ui.notify('A thorough review has started; it will appear here when it is ready', 'success')
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not start the review')
  } finally {
    starting.value = false
  }
}

async function cancel(job) {
  try {
    await researchApi.cancelJob(props.collectionId, job.job_id)
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not stop the job')
  }
}

// Removing a job only tidies this list: the reviews it wrote are rows of
// their own, so neither of these asks first.
async function removeJob(job) {
  try {
    await researchApi.removeJob(props.collectionId, job.job_id)
    jobs.value = jobs.value.filter(j => j.job_id !== job.job_id)
  } catch (err) {
    ui.toastError(err, 'Could not remove the job')
    await load()
  }
}

async function clearFinished() {
  clearing.value = true
  try {
    await researchApi.clearJobs(props.collectionId)
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not clear the finished jobs')
  } finally {
    clearing.value = false
  }
}

async function select(b) {
  openId.value = b.id
  confirmingId.value = null
  await nextTick()
  // One at a time (narrow): bring the reader's top into view.
  if (reader.value && reader.value.getBoundingClientRect?.().top < 0) reader.value.scrollIntoView?.({ block: 'start' })
  if (!b.read_at) await markRead(b, true)
}

async function markRead(b, read) {
  try {
    await researchApi.markReview(props.collectionId, b.id, read)
    b.read_at = read ? new Date().toISOString() : null
    unread.value = briefs.value.filter(x => !x.read_at).length
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not update the review')
  }
}

async function remove(b) {
  try {
    await researchApi.deleteReview(props.collectionId, b.id)
    briefs.value = briefs.value.filter(x => x.id !== b.id)
    unread.value = briefs.value.filter(x => !x.read_at).length
    if (openId.value === b.id) openId.value = null
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not delete the review')
  } finally {
    confirmingId.value = null
  }
}

async function removeAll() {
  deletingAll.value = true
  try {
    const deleted = await researchApi.deleteOutputs(props.collectionId, { reviews: true })
    briefs.value = []
    unread.value = 0
    openId.value = null
    emit('changed')
    ui.notify(`Deleted ${deleted.reviews.toLocaleString()} review${deleted.reviews === 1 ? '' : 's'}`, 'success')
  } catch (err) {
    ui.toastError(err, 'Could not delete the reviews')
  } finally {
    deletingAll.value = false
    confirmingAll.value = false
  }
}

function statusTone(b) {
  const tone = reviewStatus(b).tone
  return tone === 'warning' ? 'text-base-content/80 verify-warn' : 'text-base-content/65'
}
function statusIcon(b) {
  return reviewStatus(b).tone === 'warning' ? AlertTriangle : CheckCircle2
}
function authorList(authors = []) {
  if (authors.length <= 4) return authors.join(', ')
  return `${authors.slice(0, 3).join(', ')} and ${authors.length - 3} more`
}

// Job rows carry the stored kind, which kept its pre-R9 name: brief = review,
// assess = screening.
function jobTitle(job) {
  const kind = { brief: job.params?.depth === 'thorough' ? 'Thorough reviews' : 'Brief reviews', assess: 'Screening', analyze: 'Analysis' }[job.kind] || job.kind
  return job.params?.question ? `${kind} · “${job.params.question}”` : kind
}
function jobStatus(job) {
  if (job.status === 'queued' && job.params?.not_before) return 'Scheduled'
  return JOB_STATE_LABELS[job.status] || (isActive(job) ? job.status : 'Finished')
}
function when2(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
</script>

<style scoped>
/* Warning text in the warning colour is unreadable on light themes; the icon carries the colour. */
.verify-warn :deep(svg) { color: var(--color-warning); }
.field-label { display: block; margin-bottom: 0.25rem; font-size: 0.75rem; font-weight: 500; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
</style>
