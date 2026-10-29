<template>
  <div class="space-y-6" data-testid="research-reviews">
    <!-- Schedule -->
    <section v-if="canWrite" class="rounded-box border border-base-300 p-4 space-y-3">
      <div>
        <h3 class="font-medium">Prepare reviews</h3>
        <p class="text-xs text-base-content/60 mt-0.5 max-w-[65ch]">
          Clio reads each paper and writes a review you can skim later. Every statement quotes the paper,
          and anything it cannot quote is left out. It runs on the server, so you can close this tab.
        </p>
      </div>
      <div class="grid gap-3 sm:grid-cols-2">
        <label class="form-control">
          <span class="label-text text-xs mb-1">Papers</span>
          <select v-model="scope" class="select select-bordered select-sm">
            <option value="unreviewed">Papers without a review ({{ unreviewedCount }})</option>
            <option v-if="selectedIds.length" value="selected">Selected papers ({{ selectedIds.length }})</option>
            <option value="all">All papers ({{ papers.length }})</option>
          </select>
        </label>
        <label class="form-control">
          <span class="label-text text-xs mb-1">Depth</span>
          <select v-model="depth" class="select select-bordered select-sm">
            <option value="brief">Brief — key passages, one pass per paper</option>
            <option value="thorough">Thorough — the whole paper, part by part</option>
          </select>
        </label>
        <label class="form-control sm:col-span-2">
          <span class="label-text text-xs mb-1">Your question (optional)</span>
          <input v-model="question" class="input input-bordered input-sm" placeholder="Adds a “For your question” section to each review" />
        </label>
        <div class="form-control sm:col-span-2">
          <span class="label-text text-xs mb-1">Start</span>
          <div class="flex flex-wrap items-center gap-3 text-sm">
            <label class="flex items-center gap-1.5"><input v-model="when" type="radio" value="now" class="radio radio-xs" />Now</label>
            <label class="flex items-center gap-1.5"><input v-model="when" type="radio" value="later" class="radio radio-xs" />At</label>
            <input v-model="atTime" type="time" class="input input-bordered input-xs w-28" :disabled="when !== 'later'" aria-label="Start time" />
            <span v-if="when === 'later'" class="text-xs text-base-content/55">{{ startLabel }}</span>
          </div>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <button class="btn btn-sm btn-primary" :disabled="!providerId || starting || !targetIds.length" @click="start">
          <span v-if="starting" class="loading loading-spinner loading-xs" />
          {{ when === 'later' ? 'Schedule' : 'Start' }} {{ targetIds.length }} review{{ targetIds.length === 1 ? '' : 's' }}
        </button>
        <span v-if="!providerId" class="text-xs text-warning">Set up a model in Settings first.</span>
        <span v-else class="text-xs text-base-content/50">Using {{ providerName }}</span>
      </div>
    </section>

    <!-- Jobs -->
    <section v-if="jobs.length" class="space-y-2">
      <h3 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">Background work</h3>
      <ul class="divide-y divide-base-300/60">
        <li v-for="job in jobs.slice(0, 8)" :key="job.job_id" class="py-2 text-sm flex items-center gap-3">
          <span class="badge badge-sm" :class="jobTone(job)">{{ jobStatus(job) }}</span>
          <div class="min-w-0 flex-1">
            <div class="truncate">
              {{ jobTitle(job) }}
              <span class="text-base-content/50 tabular-nums">· {{ job.done }}/{{ job.total }}<span v-if="job.failed">, {{ job.failed }} failed</span></span>
            </div>
            <div v-if="job.current" class="text-xs text-base-content/50 truncate">{{ job.current }}</div>
            <details v-if="job.errors?.length" class="text-xs text-base-content/55">
              <summary class="cursor-pointer">What failed</summary>
              <ul><li v-for="(e, i) in job.errors" :key="i">{{ e.filename || 'Job' }}: {{ e.error }}</li></ul>
            </details>
          </div>
          <progress v-if="isActive(job)" class="progress w-24" :value="job.done + job.failed" :max="job.total || 1" />
          <button v-if="isActive(job) && canWrite" class="btn btn-xs btn-ghost" @click="cancel(job)">Stop</button>
        </li>
      </ul>
    </section>

    <!-- Queue -->
    <section class="space-y-2">
      <div class="flex items-center justify-between gap-2">
        <h3 class="text-xs font-semibold uppercase tracking-wide text-base-content/55">
          Reviews <span v-if="unread" class="normal-case font-normal">· {{ unread }} unread</span>
        </h3>
        <label class="flex items-center gap-1.5 text-xs cursor-pointer">
          <input v-model="unreadOnly" type="checkbox" class="checkbox checkbox-xs" />Unread only
        </label>
      </div>
      <p v-if="!briefs.length" class="text-sm text-base-content/55">
        {{ unreadOnly ? 'Nothing unread.' : 'No reviews yet.' }}
      </p>
      <ul class="space-y-2">
        <li v-for="b in briefs" :key="b.id" class="rounded-box border border-base-300">
          <button class="w-full text-left px-3 py-2 flex items-start gap-2" :aria-expanded="openId === b.id" @click="toggle(b)">
            <span class="mt-1.5 w-2 h-2 rounded-full shrink-0" :class="b.read_at ? 'bg-transparent' : 'bg-primary'" :aria-label="b.read_at ? 'Read' : 'Unread'" />
            <span class="min-w-0 flex-1">
              <span class="block text-sm font-medium truncate">{{ b.title || b.filename }}</span>
              <span class="block text-xs text-base-content/55 truncate">
                {{ authorsShort(b.authors) }}<span v-if="b.year"> · {{ b.year }}</span>
                · {{ b.depth === 'thorough' ? 'Thorough' : 'Brief' }}<span v-if="b.question"> · “{{ b.question }}”</span>
                · {{ when2(b.created_at) }}
              </span>
              <span v-if="tldr(b) && openId !== b.id" class="block text-xs text-base-content/75 mt-1 line-clamp-2">{{ tldr(b) }}</span>
            </span>
          </button>
          <div v-if="openId === b.id" class="px-3 pb-3 space-y-3 border-t border-base-300/60 pt-3">
            <ResearchBrief :brief="b" :collection-id="collectionId" />
            <div class="flex gap-2">
              <button class="btn btn-xs btn-outline" @click="$emit('open', b.document_id)">Open paper</button>
              <button class="btn btn-xs btn-ghost" @click="markRead(b, !b.read_at)">{{ b.read_at ? 'Mark unread' : 'Mark read' }}</button>
              <button v-if="canWrite" class="btn btn-xs btn-ghost text-error" @click="remove(b)">Delete</button>
            </div>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import ResearchBrief from './ResearchBrief.vue'
import { researchApi, authorsShort, nextLocalTime } from '../utils/researchApi.js'
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
const unread = ref(0)
const unreadOnly = ref(false)
const openId = ref(null)
const scope = ref('unreviewed')
const depth = ref('brief')
const question = ref('')
const when = ref('now')
const atTime = ref('01:00')
const starting = ref(false)
let timer = null

const providerName = computed(() => props.providerId ? getProviderDisplayName(props.providerId) : '')
const reviewedIds = computed(() => new Set(props.papers.filter(p => p.briefs > 0).map(p => p.document_id)))
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

const isActive = (job) => ['queued', 'running'].includes(job.status)

async function load() {
  try {
    const [j, b] = await Promise.all([
      researchApi.jobs(props.collectionId),
      researchApi.briefs(props.collectionId, { unread: unreadOnly.value }),
    ])
    const wasActive = jobs.value.some(isActive)
    jobs.value = j.jobs || []
    briefs.value = b.briefs || []
    unread.value = b.unread || 0
    if (wasActive && !jobs.value.some(isActive)) emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not load reviews')
  }
  schedule()
}

function schedule() {
  clearTimeout(timer)
  if (jobs.value.some(isActive)) timer = setTimeout(load, 5000)
}

watch(() => [props.collectionId, props.refreshKey, unreadOnly.value], load, { immediate: true })
onBeforeUnmount(() => clearTimeout(timer))

async function start() {
  starting.value = true
  try {
    const out = await researchApi.startJob(props.collectionId, {
      kind: 'brief',
      depth: depth.value,
      document_ids: targetIds.value,
      question: question.value.trim() || null,
      not_before: when.value === 'later' ? nextLocalTime(atTime.value) : null,
      skip_reviewed: scope.value === 'unreviewed',
    }, props.providerId)
    ui.notify(out.job ? (when.value === 'later' ? `Scheduled for ${startLabel.value}` : 'Reviews started') : (out.message || 'Nothing to do'),
      out.job ? 'success' : 'info')
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not start the reviews')
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

async function toggle(b) {
  openId.value = openId.value === b.id ? null : b.id
  if (openId.value && !b.read_at) await markRead(b, true)
}

async function markRead(b, read) {
  try {
    await researchApi.markBrief(props.collectionId, b.id, read)
    b.read_at = read ? new Date().toISOString() : null
    unread.value = briefs.value.filter(x => !x.read_at).length
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not update the review')
  }
}

async function remove(b) {
  try {
    await researchApi.deleteBrief(props.collectionId, b.id)
    briefs.value = briefs.value.filter(x => x.id !== b.id)
    emit('changed')
  } catch (err) {
    ui.toastError(err, 'Could not delete the review')
  }
}

function tldr(b) {
  return b.sections?.find(s => s.key === 'tldr')?.items?.[0]?.point || ''
}

function jobTitle(job) {
  const kind = { brief: job.params?.depth === 'thorough' ? 'Thorough reviews' : 'Brief reviews', assess: 'Screening', analyze: 'Analysis' }[job.kind] || job.kind
  return job.params?.question ? `${kind} · “${job.params.question}”` : kind
}
function jobStatus(job) {
  if (job.status === 'queued' && job.params?.not_before) return 'Scheduled'
  return { queued: 'Waiting', running: 'Running', done: 'Done', failed: 'Stopped', cancelled: 'Cancelled' }[job.status] || job.status
}
function jobTone(job) {
  return { running: 'badge-info', done: 'badge-success', failed: 'badge-error', cancelled: 'badge-ghost' }[job.status] || 'badge-ghost'
}
function when2(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`)
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
</script>
