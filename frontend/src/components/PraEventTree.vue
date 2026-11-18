<template>
  <!-- An event tree as its sequences: one row each, the path as a mark per
       functional event (filled = failed, hollow = succeeded, blank = not
       asked on this path), then where it ends. Functional events open their
       fault tree; a transfer opens the next event tree; a sequence with
       stored cut sets opens them. -->
  <div class="pt-1 space-y-3" data-testid="pra-event-tree">
    <p v-if="state === 'loading'" class="flex items-center gap-2 py-8 text-sm text-base-content/65" role="status">
      <span class="loading loading-spinner loading-sm" aria-hidden="true" />Reading the sequences…
    </p>
    <div v-else-if="state === 'error'" class="py-8 space-y-2" role="alert">
      <p class="text-sm">{{ error }}</p>
      <button class="btn btn-sm" @click="load">Try again</button>
    </div>
    <template v-else-if="et">
      <p v-if="et.initiating_event" class="text-sm">
        Initiating event
        <button type="button" class="font-medium hover:underline" @click="$emit('open-event', et.initiating_event)">{{ et.initiating_event }}</button><span v-if="et.initiating_event_frequency !== null && et.initiating_event_frequency !== undefined" class="tabular-nums">, frequency {{ sci(et.initiating_event_frequency) }}</span>
      </p>

      <div v-if="endings.length > 1" class="flex flex-wrap items-center gap-2">
        <div class="segmented max-w-full overflow-x-auto" role="radiogroup" aria-label="Sequences ending in">
          <button
            v-for="e in endings"
            :key="e.id"
            class="segment"
            role="radio"
            :aria-checked="ending === e.id"
            :class="ending === e.id ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
            @click="ending = e.id"
          >{{ e.label }}<span class="tabular-nums text-base-content/55">{{ e.count }}</span></button>
        </div>
      </div>

      <div class="overflow-x-auto -mx-1 px-1">
        <table class="seq-table" data-testid="pra-sequence-table">
          <caption class="sr-only">Sequences of event tree {{ name }}: path across the functional events, then the end state</caption>
          <thead>
            <tr>
              <th scope="col" class="text-left align-bottom pr-2">Seq.</th>
              <th v-for="(fe, i) in et.functional_events" :key="fe.name" scope="col" class="fe-head" :title="fe.description || fe.name">
                <button
                  v-if="fe.fault_tree || faultTrees.has(fe.name)"
                  type="button"
                  class="fe-name hover:underline"
                  :aria-label="`Functional event ${i + 1}, ${fe.name}${fe.description ? `, ${fe.description}` : ''}: open its fault tree`"
                  @click="$emit('open-tree', fe.fault_tree || fe.name)"
                >{{ fe.name }}</button>
                <span v-else class="fe-name">{{ fe.name }}</span>
              </th>
              <th v-if="extraColumns" scope="col" class="text-left align-bottom px-2 text-base-content/60" title="Path steps on tops outside the tree's columns (node substitutions)">Also</th>
              <th scope="col" class="text-left align-bottom pl-2">Ends in</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in shownSequences" :key="s.name" :aria-label="`Sequence ${s.name}: ${sequencePathText(s.marks) || 'no path'}; ${outcomeText(s)}`">
              <th scope="row" class="text-left font-medium pr-2 whitespace-nowrap align-top">
                {{ s.name }}
                <span v-if="s.description" class="block text-xs font-normal text-base-content/60 max-w-[10rem] truncate" :title="s.description">{{ s.description }}</span>
              </th>
              <td v-for="m in s.columns" :key="m.name" class="mark-cell" :data-state="m.state" :title="`${m.name}: ${m.label}`">
                <span class="mark" aria-hidden="true">{{ m.state === 'other' ? m.label.slice(0, 3) : '' }}</span>
              </td>
              <td v-if="extraColumns" class="px-2 text-xs text-base-content/65 align-top">
                <span v-for="m in s.extras" :key="m.name" class="block whitespace-nowrap">{{ m.name }} {{ m.label }}</span>
              </td>
              <td class="pl-2 align-top whitespace-nowrap">
                <button v-if="s.outcome.kind === 'transfer'" type="button" class="inline-flex items-center gap-1 hover:underline" :title="`Continues in event tree ${s.outcome.label}`" @click="$emit('open-event-tree', s.outcome.label)">
                  <CornerDownRight :size="12" aria-hidden="true" />{{ s.outcome.label }}
                </button>
                <span v-else-if="s.outcome.kind === 'end_state'" :class="s.outcome.label === 'OK' ? 'text-base-content/60' : 'font-medium'">{{ s.outcome.label }}</span>
                <span v-else class="text-base-content/50">—</span>
                <span v-if="s.frequency !== null && s.frequency !== undefined" class="block text-xs text-base-content/65 tabular-nums">{{ sci(s.frequency) }}</span>
                <button v-if="s.has_cut_sets" type="button" class="block text-xs link link-hover" :aria-label="`Cut sets of sequence ${s.name}`" @click="$emit('open-sequence', s.name)">Cut sets</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-xs text-base-content/60 flex flex-wrap items-center gap-x-3 gap-y-1" aria-hidden="true">
        <span class="inline-flex items-center gap-1"><span class="legend-mark" data-state="failure" />failed</span>
        <span class="inline-flex items-center gap-1"><span class="legend-mark" data-state="success" />succeeded</span>
        <span class="inline-flex items-center gap-1"><span class="legend-mark" data-state="skip" />not asked</span>
        <span v-if="withCutSets">{{ plural(withCutSets, 'sequence') }} with stored cut sets</span>
      </p>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { CornerDownRight } from 'lucide-vue-next'
import { praApi } from '../utils/praApi'
import { plural, sci, sequenceMarks, sequenceOutcome, sequencePathText } from '../utils/praFormat'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
  name: { type: String, required: true },
  faultTrees: { type: Set, default: () => new Set() },
})
defineEmits(['open-tree', 'open-event-tree', 'open-sequence', 'open-event'])

const state = ref('loading')
const error = ref('')
const et = ref(null)
const ending = ref('all')

async function load() {
  state.value = 'loading'
  try {
    et.value = await praApi.eventTree(props.collectionId, props.documentId, props.name)
    state.value = 'ready'
  } catch (err) {
    error.value = err?.message || 'The event tree could not be loaded.'
    state.value = 'error'
  }
}
onMounted(load)

const sequences = computed(() => (et.value?.sequences || []).map(s => {
  const marks = sequenceMarks(et.value.functional_events, s.path)
  return { ...s, marks, columns: marks.filter(m => !m.extra), extras: marks.filter(m => m.extra), outcome: sequenceOutcome(s) }
}))
const extraColumns = computed(() => sequences.value.some(s => s.extras.length))
const withCutSets = computed(() => sequences.value.filter(s => s.has_cut_sets).length)

const endingOf = (s) => (s.outcome.kind === 'transfer' ? '→transfer' : s.outcome.label || '—')
const endings = computed(() => {
  const counts = new Map()
  for (const s of sequences.value) counts.set(endingOf(s), (counts.get(endingOf(s)) || 0) + 1)
  const list = [...counts].map(([id, count]) => ({ id, count, label: id === '→transfer' ? 'Transfers' : id === '—' ? 'No end state' : id }))
  list.sort((a, b) => b.count - a.count)
  return [{ id: 'all', label: 'All', count: sequences.value.length }, ...list]
})
const shownSequences = computed(() => (ending.value === 'all' ? sequences.value : sequences.value.filter(s => endingOf(s) === ending.value)))

function outcomeText(s) {
  if (s.outcome.kind === 'transfer') return `transfers to event tree ${s.outcome.label}`
  if (s.outcome.kind === 'end_state') return `ends in ${s.outcome.label}`
  return 'no end state'
}
</script>

<style scoped>
.seq-table { font-size: 0.875rem; border-collapse: collapse; }
.seq-table tbody tr { border-top: 1px solid color-mix(in oklch, var(--color-base-content) 8%, transparent); }
.seq-table tbody tr:hover { background-color: color-mix(in oklch, var(--color-base-content) 4%, transparent); }
.seq-table td, .seq-table tbody th { padding-top: 0.3rem; padding-bottom: 0.3rem; }
.seq-table thead th { font-size: 0.75rem; font-weight: 500; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
/* Functional event names read bottom to top over their column, as event tree
   drawings label their headings, so eleven of them fit a phone. */
.fe-head { vertical-align: bottom; padding: 0 0 0.25rem; width: 1.25rem; }
.fe-name {
  writing-mode: vertical-rl;
  transform: rotate(180deg);
  white-space: nowrap;
  max-height: 9rem;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 0.75rem;
  line-height: 1.25rem;
}
.mark-cell { width: 1.25rem; text-align: center; vertical-align: middle; position: relative; }
/* Not asked: the path runs through, a thin line. */
.mark-cell[data-state='skip']::before {
  content: '';
  position: absolute;
  left: 0; right: 0; top: 50%;
  border-top: 1px solid color-mix(in oklch, var(--color-base-content) 18%, transparent);
}
.mark { display: inline-grid; place-items: center; width: 0.625rem; height: 0.625rem; border-radius: 9999px; font-size: 0.75rem; }
.mark-cell[data-state='failure'] .mark { background: currentColor; }
.mark-cell[data-state='success'] .mark { border: 1.5px solid color-mix(in oklch, var(--color-base-content) 45%, transparent); }
.mark-cell[data-state='other'] .mark { width: auto; height: auto; border-radius: 0; color: color-mix(in oklch, var(--color-base-content) 75%, transparent); }
.legend-mark { display: inline-block; width: 0.625rem; height: 0.625rem; border-radius: 9999px; }
.legend-mark[data-state='failure'] { background: currentColor; }
.legend-mark[data-state='success'] { border: 1.5px solid color-mix(in oklch, var(--color-base-content) 45%, transparent); }
.legend-mark[data-state='skip'] { width: 0.875rem; height: 0; border-radius: 0; border-top: 1px solid color-mix(in oklch, var(--color-base-content) 30%, transparent); }
</style>
