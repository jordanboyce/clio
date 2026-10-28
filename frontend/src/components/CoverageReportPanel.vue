<template>
  <div class="space-y-3" data-testid="coverage-panel">
    <div class="flex items-start justify-between gap-6">
      <div class="min-w-0">
        <div class="text-sm font-medium flex items-center gap-1.5">
          <Radar :size="14" aria-hidden="true" />
          Retrieval coverage
        </div>
        <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[60ch]">
          Searches this collection with questions drawn from its own content and checks whether the
          right source comes back. It measures how <em>findable</em> your sources are, not whether
          answers built from them are correct.
        </p>
      </div>
      <button
        v-if="canRun"
        class="btn btn-sm btn-outline flex-shrink-0"
        :disabled="running"
        data-testid="coverage-run"
        @click="startRun"
      >
        <span v-if="running" class="loading loading-spinner loading-xs" />
        {{ running ? progressLabel : report ? 'Run again' : 'Run report' }}
      </button>
    </div>

    <p v-if="error" class="text-xs text-error" role="alert" data-testid="coverage-error">{{ error }}</p>
    <p v-if="runState.state === 'failed'" class="text-xs text-error" role="alert">
      The last run failed: {{ runState.error }}
    </p>

    <p v-if="loaded && !report && !running" class="text-xs text-base-content/55" data-testid="coverage-empty">
      {{ canRun ? 'No report yet. Run one to see which sources are hard to find.' : 'No report yet. The collection’s owner can run one.' }}
    </p>

    <template v-if="report">
      <div class="flex flex-wrap items-baseline gap-x-6 gap-y-2" data-testid="coverage-score">
        <div>
          <div class="text-2xl font-semibold tabular-nums" data-testid="coverage-doc-hit">{{ pct(overall.document_hit_at_5) }}</div>
          <div class="text-xs text-base-content/55">sources found in top 5</div>
        </div>
        <div>
          <div class="text-sm tabular-nums">{{ pct(overall.chunk_hit_at_10) }}</div>
          <div class="text-xs text-base-content/55">passages in top 10</div>
        </div>
        <div :title="'Mean reciprocal rank: 1.0 means the right passage always comes back first; 0.5 means it is typically second.'">
          <div class="text-sm tabular-nums">{{ overall.mrr_chunk ?? '–' }}</div>
          <div class="text-xs text-base-content/55">rank quality</div>
        </div>
        <div class="text-xs text-base-content/55">
          {{ overall.probes }} test searches · {{ report.age || 'unknown age' }} old
        </div>
      </div>

      <div
        v-if="report.stale"
        class="alert alert-warning text-xs py-2 px-3"
        role="status"
        data-testid="coverage-stale"
      >
        <AlertTriangle :size="14" class="shrink-0" aria-hidden="true" />
        <span>
          Out of date: {{ staleText }} since this report ran.
          <template v-if="canRun">Run it again for current numbers.</template>
        </span>
      </div>

      <p v-for="note in extraNotes" :key="note" class="text-xs text-base-content/60" data-testid="coverage-note">{{ note }}</p>

      <div v-if="report.weakest_documents?.length" data-testid="coverage-weakest">
        <div class="text-xs font-medium mb-1">Hardest to find</div>
        <ul class="divide-y divide-base-300/50">
          <li v-for="d in report.weakest_documents" :key="d.document_id" class="py-2">
            <div class="flex items-baseline justify-between gap-3">
              <span class="text-sm truncate" :title="d.filename">{{ d.filename }}</span>
              <span class="text-xs tabular-nums text-base-content/60 shrink-0">
                {{ pct(d.document_hit_at_5) }} of {{ d.probes }} searches
              </span>
            </div>
            <ul v-if="d.failing_probes?.length" class="mt-1 space-y-0.5">
              <li
                v-for="f in d.failing_probes"
                :key="f.chunk_id"
                class="text-xs text-base-content/60 truncate"
                :title="f.preview"
                data-testid="coverage-failing-probe"
              >
                Missed: “{{ f.query }}”
              </li>
            </ul>
          </li>
        </ul>
      </div>
      <p v-else-if="report.documents?.length" class="text-xs text-success" data-testid="coverage-all-found">
        Every probed source came back in the top 5.
      </p>

      <p v-if="neverCount" class="text-xs text-base-content/60">
        {{ neverCount }} probed passage{{ neverCount === 1 ? '' : 's' }} never came back in the top 10.
      </p>
      <p v-if="unprobeable" class="text-xs text-base-content/60">
        {{ unprobeable }} passage{{ unprobeable === 1 ? ' has' : 's have' }} no searchable words (tables of numbers, for example) and were not probed.
      </p>

      <p class="text-xs text-base-content/45">
        Test searches: {{ methodText }} · embedding model {{ report.embedding_model || 'unknown' }} · {{ report.duration_seconds }}s
      </p>
    </template>

    <div class="pt-3 border-t border-base-300/50" data-testid="coverage-questions">
      <div class="text-xs font-medium">Your questions</div>
      <p class="text-xs text-base-content/55 mt-0.5 max-w-[60ch]">
        Questions you know people ask, with the source that should answer each. These are the most
        trustworthy part of the report and are scored separately.
      </p>

      <ul v-if="questions.length" class="mt-2 space-y-1">
        <li v-for="q in questions" :key="q.id" class="flex items-start justify-between gap-3 text-xs">
          <div class="min-w-0">
            <span class="break-words">{{ q.question }}</span>
            <span class="text-base-content/50"> → {{ q.expected_documents.map((d) => d.filename).join(', ') }}</span>
            <span
              v-if="itemStatus(q.id)"
              class="badge badge-xs ml-1.5"
              :class="itemStatus(q.id) === 'hit' ? 'badge-success' : 'badge-warning'"
              data-testid="coverage-question-status"
            >{{ itemStatus(q.id) === 'hit' ? 'found' : statusLabel(itemStatus(q.id)) }}</span>
          </div>
          <button
            v-if="canRun"
            class="btn btn-ghost btn-xs shrink-0"
            :aria-label="`Remove question: ${q.question}`"
            data-testid="coverage-question-delete"
            @click="removeQuestion(q.id)"
          >
            <Trash2 :size="12" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-else class="text-xs text-base-content/45 mt-2">None yet.</p>

      <form v-if="canRun" class="mt-3 space-y-2" data-testid="coverage-question-form" @submit.prevent="addQuestion">
        <input
          v-model="newQuestion"
          class="input input-bordered input-sm w-full"
          type="text"
          maxlength="1000"
          placeholder="How do I rotate an API key?"
          aria-label="Question"
        />
        <div class="flex gap-2">
          <input
            v-model="newExpected"
            class="input input-bordered input-sm flex-1"
            type="text"
            placeholder="Expected file name(s), comma separated"
            aria-label="Expected files"
          />
          <button
            class="btn btn-sm btn-outline"
            type="submit"
            :disabled="adding || !newQuestion.trim() || !expectedList.length"
          >
            <span v-if="adding" class="loading loading-spinner loading-xs" />
            Add
          </button>
        </div>
        <p v-if="questionError" class="text-xs text-error" role="alert" data-testid="coverage-question-error">{{ questionError }}</p>
      </form>
    </div>
  </div>
</template>

<script setup>
// Owner-facing view of a collection's retrieval coverage report (see
// services/coverage_report.py). Anyone who can read the collection sees the
// report; only owners get the Run button and the question editor, matching
// the server's gates.
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { AlertTriangle, Radar, Trash2 } from 'lucide-vue-next'
import http from '../utils/http'

const props = defineProps({
  collectionId: { type: String, required: true },
  canRun: { type: Boolean, default: false },
  pollMs: { type: Number, default: 2000 },
})

const report = ref(null)
const runState = ref({ state: 'idle' })
const questions = ref([])
const loaded = ref(false)
const error = ref('')
const questionError = ref('')
const newQuestion = ref('')
const newExpected = ref('')
const adding = ref(false)
let timer = null

const overall = computed(() => report.value?.overall || {})
const running = computed(() => runState.value.state === 'running')
const progressLabel = computed(() =>
  runState.value.total ? `Running ${runState.value.done}/${runState.value.total}` : 'Running…')
const expectedList = computed(() =>
  newExpected.value.split(',').map((s) => s.trim()).filter(Boolean))
const neverCount = computed(() => report.value?.chunks_never_retrieved?.count || 0)
const unprobeable = computed(() => report.value?.unprobeable_chunks?.count || 0)
// The first note is the findability disclaimer, which the panel already states.
const extraNotes = computed(() => (report.value?.notes || []).slice(1))
const methodText = computed(() => {
  const by = report.value?.probe_method?.by_method || {}
  const parts = Object.entries(by).map(([m, v]) => `${m} ${v.probes}`)
  return parts.join(', ') || 'none'
})
const staleText = computed(() => {
  const labels = {
    corpus_changed: 'sources were added, removed or changed',
    embedding_model_changed: 'the embedding model changed',
  }
  return (report.value?.stale_reasons || []).map((r) => labels[r] || r).join(' and ')
})
const ownerItems = computed(() => report.value?.owner_questions?.items || [])

function pct(v) {
  return v == null ? '–' : `${Math.round(v * 100)}%`
}
function itemStatus(id) {
  return ownerItems.value.find((i) => i.id === id)?.status || null
}
function statusLabel(s) {
  return s === 'miss' ? 'not found' : s.replace(/_/g, ' ')
}

function stopPolling() {
  if (timer) { clearTimeout(timer); timer = null }
}

async function load() {
  if (!props.collectionId) return
  try {
    const { data } = await http.get(`/api/collections/${props.collectionId}/coverage`)
    report.value = data.report
    runState.value = data.run || { state: 'idle' }
    questions.value = data.questions || []
    error.value = ''
  } catch (e) {
    error.value = e?.message || 'Could not load the coverage report'
  } finally {
    loaded.value = true
  }
  stopPolling()
  if (running.value) timer = setTimeout(load, props.pollMs)
}

async function startRun() {
  error.value = ''
  try {
    await http.post(`/api/collections/${props.collectionId}/coverage`, {})
  } catch (e) {
    // 409: someone else's run is already going; just follow it.
    if (e?.status !== 409) {
      error.value = e?.message || 'Could not start the report'
      return
    }
  }
  await load()
}

async function addQuestion() {
  adding.value = true
  questionError.value = ''
  try {
    await http.post(`/api/collections/${props.collectionId}/coverage/questions`, {
      question: newQuestion.value.trim(),
      expected_documents: expectedList.value,
    })
    newQuestion.value = ''
    newExpected.value = ''
    await load()
  } catch (e) {
    questionError.value = e?.message || 'Could not add the question'
  } finally {
    adding.value = false
  }
}

async function removeQuestion(id) {
  questionError.value = ''
  try {
    await http.delete(`/api/collections/${props.collectionId}/coverage/questions/${id}`)
    await load()
  } catch (e) {
    questionError.value = e?.message || 'Could not remove the question'
  }
}

onMounted(load)
onBeforeUnmount(stopPolling)
watch(() => props.collectionId, () => {
  report.value = null
  runState.value = { state: 'idle' }
  questions.value = []
  loaded.value = false
  load()
})

defineExpose({ load })
</script>
