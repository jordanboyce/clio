<template>
  <!-- Cut sets or importance for one context: a fault tree or gate, a
       sequence of an event tree, or an end state. Stored lists come from the
       model; a fault tree without one can be computed here, within the
       server's time budget, and the pane says how complete the result is.
       The screening notice is said once, at the foot. -->
  <div class="pt-1 space-y-3" :data-testid="`pra-${mode}`">
    <p v-if="state === 'loading'" class="flex items-center gap-2 py-8 text-sm text-base-content/65" role="status">
      <span class="loading loading-spinner loading-sm" aria-hidden="true" />{{ mode === 'importance' ? 'Ranking the events…' : 'Reading the cut sets…' }}
    </p>

    <!-- Nothing stored: offer to compute (fault trees and gates), or say why not -->
    <section v-else-if="state === 'absent'" class="max-w-[68ch] space-y-3 py-2" data-testid="pra-compute">
      <template v-if="canCompute">
        <p class="text-sm">
          The model stores no cut sets for {{ kindWord }} {{ context }}. Clio can search its logic for the minimal
          cut sets (top-down, with truncation), for up to {{ budget }} seconds.
        </p>
        <details class="text-sm" :open="optionsOpen" @toggle="optionsOpen = $event.target.open">
          <summary class="cursor-pointer text-xs text-base-content/70 hover:text-base-content">Search settings</summary>
          <div class="mt-2 flex flex-wrap gap-x-4 gap-y-2">
            <label class="flex flex-col gap-1">
              <span class="text-xs text-base-content/70">Truncation (cutoff)</span>
              <select v-model.number="truncation" class="select select-bordered select-sm" data-testid="pra-truncation">
                <option v-for="t in TRUNCATIONS" :key="t" :value="t">{{ cutoff(t) }}</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-base-content/70">Largest order</span>
              <select v-model.number="maxOrder" class="select select-bordered select-sm">
                <option :value="0">Any</option>
                <option v-for="n in [1, 2, 3, 4, 5, 6, 8, 10]" :key="n" :value="n">{{ n }}</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-xs text-base-content/70">Success terms (/X)</span>
              <select v-model="successTerms" class="select select-bordered select-sm">
                <option value="drop">Drop (coherent, upper bound)</option>
                <option value="keep">Keep at 1 − p</option>
              </select>
            </label>
          </div>
          <p class="mt-2 text-xs text-base-content/65">
            The search runs at cutoffs 1E-3, 1E-5, … down to the one chosen, each pass exact for its cutoff. If the
            time runs out, the deepest finished pass is kept and its cutoff is shown.
          </p>
        </details>
        <button class="btn btn-sm btn-primary" data-testid="pra-compute-button" @click="compute">Compute cut sets</button>
      </template>
      <p v-else class="text-sm text-base-content/75">{{ absentMessage }}</p>
    </section>

    <!-- Computing: elapsed time against the budget, which is what is known -->
    <section v-else-if="state === 'computing'" class="max-w-[60ch] space-y-2 py-2" role="status" aria-live="polite" data-testid="pra-computing">
      <p class="text-sm">Searching for minimal cut sets of {{ context }}…</p>
      <progress class="progress w-full max-w-sm" :value="Math.min(elapsed, budget)" :max="budget" :aria-label="`${Math.floor(elapsed)} of at most ${budget} seconds`" />
      <p class="text-xs text-base-content/65 tabular-nums">
        {{ Math.floor(elapsed) }} s of at most {{ budget }} s{{ elapsed > budget ? ' · quantifying what was found' : '' }}.
        The bar is time, not work: a search can finish early.
      </p>
      <button class="btn btn-xs btn-ghost" @click="stopWaiting">Stop waiting</button>
    </section>

    <div v-else-if="state === 'error'" class="py-4 space-y-2 max-w-[68ch]" role="alert" data-testid="pra-analysis-error">
      <p class="text-sm">{{ error.message }}</p>
      <div class="flex gap-2">
        <button class="btn btn-sm" @click="retry">Try again</button>
        <button v-if="error.kind === 'stopped' && canCompute" class="btn btn-sm btn-ghost" @click="state = 'absent'; optionsOpen = true">Change the settings</button>
      </div>
    </div>

    <template v-else-if="data">
      <!-- Where the list came from, how complete it is, and its totals -->
      <div class="space-y-1" data-testid="pra-provenance">
        <p class="text-sm" :class="provenance.lowerBound ? 'font-medium' : ''">
          <AlertTriangle v-if="provenance.lowerBound" :size="13" class="inline -mt-0.5 mr-1 text-warning" aria-hidden="true" />{{ provenance.headline }}
        </p>
        <p class="text-xs text-base-content/70 tabular-nums">
          <template v-if="mode === 'cut-sets'">
            {{ plural(data.cut_set_count, 'cut set') }}
            · rare-event total {{ sci(data.totals?.rare_event) }}
            <template v-if="data.totals?.mcub !== null && data.totals?.mcub !== undefined"> · MCUB {{ sci(data.totals.mcub) }}</template>
          </template>
          <template v-else>
            {{ plural(data.events_total, 'event') }} ranked · F = {{ sci(data.total) }}
            ({{ data.basis === 'mcub' ? 'minimal cut set upper bound' : 'rare-event sum: the cut sets carry a frequency' }})
          </template>
          <template v-if="c"> · {{ c.elapsed_seconds?.toFixed?.(1) }} s, {{ Number(c.rows_explored || 0).toLocaleString('en-US') }} rows searched</template>
        </p>
        <ul v-if="provenance.notes.length" class="space-y-0.5">
          <li v-for="(n, i) in provenance.notes" :key="i" class="text-xs text-base-content/70 flex gap-1.5">
            <Info :size="12" class="mt-0.5 shrink-0" aria-hidden="true" /><span>{{ n }}</span>
          </li>
        </ul>
      </div>

      <!-- Cut sets -->
      <template v-if="mode === 'cut-sets'">
        <label class="inline-flex items-center gap-1.5 text-xs text-base-content/70 cursor-pointer">
          <input v-model="descriptions" type="checkbox" class="checkbox checkbox-xs" />Show event descriptions
        </label>
        <table class="pra-table w-full" data-testid="pra-cut-set-table">
          <caption class="sr-only">Cut sets of {{ context }}, most probable first</caption>
          <thead>
            <tr>
              <th scope="col" class="w-8 text-right">#</th>
              <th scope="col">Cut set</th>
              <th scope="col" class="text-right" :title="data.definitions?.probability">Probability</th>
              <th scope="col" class="text-right w-14" :title="data.definitions?.share">Share</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in data.cut_sets" :key="row.rank">
              <td class="text-right text-base-content/55 tabular-nums">{{ row.rank }}</td>
              <td>
                <span class="cut-set" :class="descriptions ? 'is-described' : ''">
                  <span v-for="(e, i) in row.events" :key="i" class="cut-event">
                    <button type="button" class="event-link" :title="eventTitle(e)" @click="$emit('open-event', bare(e))">{{ e }}</button>
                    <span v-if="descriptions && describe(e)" class="block text-xs text-base-content/65 leading-snug">{{ describe(e) }}</span>
                  </span>
                </span>
                <span v-if="row.missing?.length" class="block text-xs text-warning">No data for {{ row.missing.join(', ') }}</span>
              </td>
              <td class="text-right tabular-nums whitespace-nowrap">{{ sci(row.probability) }}</td>
              <td class="text-right tabular-nums whitespace-nowrap text-base-content/75">{{ percent(row.share) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="data.cut_set_count > data.returned" class="flex flex-wrap items-center gap-2 text-xs text-base-content/65">
          <span class="tabular-nums">Showing {{ data.returned.toLocaleString('en-US') }} of {{ data.cut_set_count.toLocaleString('en-US') }}</span>
          <button v-if="limit < MAX_LIMIT" class="btn btn-xs btn-ghost" :disabled="refreshing" data-testid="pra-more-cut-sets" @click="more">
            <span v-if="refreshing" class="loading loading-spinner loading-xs" />Show {{ Math.min(STEP, data.cut_set_count - data.returned) }} more
          </button>
          <span v-else>The rest are in the source's cut_sets table (Connect → query_table).</span>
        </div>
      </template>

      <!-- Importance -->
      <template v-else>
        <div class="segmented" role="radiogroup" aria-label="Rank by">
          <button
            v-for="col in IMPORTANCE_COLUMNS"
            :key="col.id"
            class="segment"
            role="radio"
            :aria-checked="sort === col.id"
            :title="`${col.name}: ${col.hint}`"
            :class="sort === col.id ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
            :data-testid="`pra-sort-${col.id}`"
            @click="setSort(col.id)"
          >{{ col.label }}</button>
        </div>
        <p class="text-xs text-base-content/65">{{ currentColumn.name }}: {{ currentColumn.hint }}</p>
        <div class="@container">
          <table class="pra-table w-full" data-testid="pra-importance-table">
            <caption class="sr-only">Importance of the events in the cut sets of {{ context }}, by {{ currentColumn.name }}</caption>
            <thead>
              <tr>
                <th scope="col" class="w-8 text-right">#</th>
                <th scope="col">Event</th>
                <th scope="col" class="text-right" title="The event's point probability (or frequency)">P</th>
                <th
                  v-for="col in IMPORTANCE_COLUMNS"
                  :key="col.id"
                  scope="col"
                  class="text-right whitespace-nowrap"
                  :class="col.id === sort ? '' : 'hidden @xl:table-cell'"
                  :aria-sort="col.id === sort ? 'descending' : 'none'"
                >
                  <button type="button" class="hover:underline" :class="col.id === sort ? 'font-semibold text-base-content' : ''" :title="`${col.name}: ${col.hint}`" @click="setSort(col.id)">{{ col.label }}</button>
                </th>
                <th scope="col" class="text-right hidden @2xl:table-cell" title="How many of the cut sets contain the event">In</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in data.events" :key="row.event">
                <td class="text-right text-base-content/55 tabular-nums">{{ row.rank }}</td>
                <td>
                  <button type="button" class="event-link" @click="$emit('open-event', row.event)">{{ row.event }}</button>
                  <span v-if="row.description" class="block text-xs text-base-content/65 leading-snug">{{ row.description }}</span>
                </td>
                <td class="text-right tabular-nums whitespace-nowrap">{{ sci(row.probability) }}</td>
                <td
                  v-for="col in IMPORTANCE_COLUMNS"
                  :key="col.id"
                  class="text-right tabular-nums whitespace-nowrap"
                  :class="col.id === sort ? 'font-medium' : 'hidden @xl:table-cell text-base-content/75'"
                >{{ col.id === 'rrw' ? rrwText(row) : col.format(row[col.field]) }}</td>
                <td class="text-right tabular-nums text-base-content/65 hidden @2xl:table-cell">{{ row.cut_sets?.toLocaleString?.('en-US') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="data.events_total > data.returned" class="flex flex-wrap items-center gap-2 text-xs text-base-content/65">
          <span class="tabular-nums">Showing {{ data.returned }} of {{ data.events_total.toLocaleString('en-US') }} events</span>
          <button v-if="limit < MAX_LIMIT" class="btn btn-xs btn-ghost" :disabled="refreshing" @click="more">
            <span v-if="refreshing" class="loading loading-spinner loading-xs" />Show {{ Math.min(STEP, data.events_total - data.returned) }} more
          </button>
        </div>
      </template>

      <!-- Once per view, quietly -->
      <p class="pt-2 text-xs text-base-content/55 max-w-[72ch]" data-testid="pra-notice">
        Screening estimate from point values (rare-event sum and minimal cut set upper bound), without uncertainty,
        recovery or the tool's own rules. {{ toolName }}'s own quantification governs.
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { AlertTriangle, Info } from 'lucide-vue-next'
import { analysisError, praApi } from '../utils/praApi'
import { IMPORTANCE_COLUMNS, cutSetProvenance, cutoff, percent, plural, rrwText, sci } from '../utils/praFormat'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
  context: { type: String, required: true },
  eventTree: { type: String, default: null },
  kind: { type: String, default: 'fault_tree' }, // fault_tree | gate | sequence | end_state
  mode: { type: String, default: 'cut-sets' }, // cut-sets | importance
  tool: { type: String, default: 'the PRA tool' },
  // { truncation, maxOrder, successTerms } when this context was computed in this visit.
  computeOptions: { type: Object, default: null },
  // Whether the model stores a list for it, when the summary says (null: ask).
  stored: { type: Boolean, default: null },
})
const emit = defineEmits(['open-event', 'computed'])

const STEP = 50
const MAX_LIMIT = 1000
const TRUNCATIONS = [1e-9, 1e-10, 1e-11, 1e-12, 1e-13, 1e-14, 1e-15]
const budget = 20

const state = ref('loading')
const data = ref(null)
const error = ref({ kind: '', message: '' })
const absentMessage = ref('')
const limit = ref(STEP)
const sort = ref('fv')
const descriptions = ref(false)
const refreshing = ref(false)
const optionsOpen = ref(false)
const truncation = ref(props.computeOptions?.truncation ?? 1e-15)
const maxOrder = ref(props.computeOptions?.maxOrder ?? 0)
const successTerms = ref(props.computeOptions?.successTerms ?? 'drop')
const elapsed = ref(0)
let timer = null
let controller = null

const canCompute = computed(() => props.kind === 'fault_tree' || props.kind === 'gate')
const kindWord = computed(() => ({ fault_tree: 'fault tree', gate: 'gate', sequence: 'sequence', end_state: 'end state' }[props.kind] || ''))
const toolName = computed(() => (props.tool === 'the PRA tool' ? 'The PRA tool' : props.tool))
const c = computed(() => (data.value?.source === 'computed' ? data.value.computation : null))
const provenance = computed(() => cutSetProvenance(data.value))
const currentColumn = computed(() => IMPORTANCE_COLUMNS.find(col => col.id === sort.value))

const bare = (e) => String(e).replace(/^\//, '')
const describe = (e) => data.value?.events?.[bare(e)]?.description || ''
function eventTitle(e) {
  const info = data.value?.events?.[bare(e)]
  if (!info) return ''
  const value = info.house_event !== undefined ? (info.house_event ? 'TRUE' : 'FALSE') : sci(info.probability)
  return [info.description, `p = ${value}`, e.startsWith('/') ? 'complemented: counts 1 − p' : ''].filter(Boolean).join('\n')
}

function opts(compute) {
  const o = { limit: limit.value, eventTree: props.eventTree || undefined }
  if (props.mode === 'importance') o.sort = sort.value
  if (compute) Object.assign(o, { compute: true, truncation: truncation.value, maxOrder: maxOrder.value || undefined })
  return o
}

function request(compute) {
  controller = new AbortController()
  const fn = props.mode === 'importance' ? praApi.importance : praApi.cutSets
  const o = opts(compute)
  if (compute && successTerms.value !== 'drop') o.successTerms = successTerms.value
  return fn(props.collectionId, props.documentId, props.context, o, { signal: controller.signal })
}

async function fetchStored() {
  // The summary already said there is no stored list: offer to compute
  // without asking the server for one it does not have.
  if (props.stored === false && canCompute.value) {
    if (props.computeOptions) return compute()
    state.value = 'absent'
    return
  }
  state.value = 'loading'
  try {
    data.value = await request(false)
    state.value = 'ready'
  } catch (err) {
    if (err?.code === 'ERR_CANCELED') return
    const e = analysisError(err)
    if (e.kind === 'missing' && /compute=true|stores no cut sets/.test(e.message)) {
      if (canCompute.value && props.computeOptions) return compute()
      absentMessage.value = canCompute.value ? '' : `${e.message.replace(/, and Clio generates.*$/, '')}. Clio generates cut sets for fault trees and gates only.`
      state.value = 'absent'
      return
    }
    error.value = e
    state.value = 'error'
  }
}

async function compute() {
  state.value = 'computing'
  const started = performance.now()
  elapsed.value = 0
  clearInterval(timer)
  timer = setInterval(() => { elapsed.value = (performance.now() - started) / 1000 }, 250)
  try {
    data.value = await request(true)
    state.value = 'ready'
    emit('computed', { truncation: truncation.value, maxOrder: maxOrder.value, successTerms: successTerms.value })
  } catch (err) {
    if (err?.code === 'ERR_CANCELED') return
    error.value = analysisError(err)
    state.value = 'error'
  } finally {
    clearInterval(timer)
  }
}

function stopWaiting() {
  controller?.abort()
  clearInterval(timer)
  state.value = 'absent'
  absentMessage.value = ''
}

function retry() {
  if (error.value.kind === 'stopped' || error.value.kind === 'busy' || props.computeOptions || data.value?.source === 'computed') {
    return canCompute.value ? compute() : fetchStored()
  }
  return fetchStored()
}

async function refresh() {
  refreshing.value = true
  try {
    data.value = await request(data.value?.source === 'computed')
  } catch (err) {
    if (err?.code !== 'ERR_CANCELED') { error.value = analysisError(err); state.value = 'error' }
  } finally {
    refreshing.value = false
  }
}
function more() {
  limit.value = Math.min(MAX_LIMIT, limit.value + STEP)
  refresh()
}
function setSort(id) {
  if (sort.value === id) return
  sort.value = id
  refresh()
}

onMounted(fetchStored)
onBeforeUnmount(() => { controller?.abort(); clearInterval(timer) })
</script>

<style scoped>
.pra-table { font-size: 0.875rem; border-collapse: collapse; }
.pra-table th {
  font-size: 0.75rem;
  font-weight: 500;
  text-align: left;
  color: color-mix(in oklch, var(--color-base-content) 65%, transparent);
  padding: 0.25rem 0.375rem;
  border-bottom: 1px solid color-mix(in oklch, var(--color-base-content) 15%, transparent);
}
.pra-table th.text-right { text-align: right; }
.pra-table td {
  padding: 0.375rem;
  vertical-align: top;
  border-bottom: 1px solid color-mix(in oklch, var(--color-base-content) 7%, transparent);
}
.cut-set { display: flex; flex-wrap: wrap; gap: 0 0.75rem; }
.cut-set.is-described { flex-direction: column; gap: 0.25rem; }
.cut-event { min-width: 0; overflow-wrap: anywhere; }
.cut-set:not(.is-described) .cut-event { display: inline-flex; align-items: baseline; }
/* The separator trails its event, so a wrapped line starts with an event. */
.cut-set:not(.is-described) .cut-event:not(:last-child)::after {
  content: '·';
  margin-left: 0.375rem;
  margin-right: -0.375rem;
  color: color-mix(in oklch, var(--color-base-content) 40%, transparent);
}
.event-link { text-align: left; font-weight: 500; overflow-wrap: anywhere; }
.event-link:hover { text-decoration: underline; text-underline-offset: 2px; }
</style>
