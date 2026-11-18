<template>
  <!-- One event (or gate) of the model: its data and everywhere it is used,
       as a sheet over the reader (full screen on a phone). -->
  <div class="fixed inset-0 z-[70]" data-testid="pra-event-panel">
    <div class="absolute inset-0 bg-base-content/20" aria-hidden="true" @click="$emit('close')" />
    <aside
      ref="panel"
      role="dialog"
      aria-modal="true"
      :aria-labelledby="titleId"
      tabindex="-1"
      class="absolute inset-y-0 right-0 w-full sm:w-[28rem] bg-base-100 border-l border-base-300 shadow-2xl flex flex-col focus:outline-none"
    >
      <header class="shrink-0 flex items-start gap-2 px-4 pt-3 pb-3 border-b border-base-300/70">
        <div class="min-w-0 flex-1">
          <p class="text-xs text-base-content/60">{{ kindLabel }}</p>
          <h3 :id="titleId" class="text-base font-semibold leading-snug [overflow-wrap:anywhere]">{{ name }}</h3>
          <p v-if="ev?.description" class="text-sm text-base-content/80 leading-snug">{{ ev.description }}</p>
        </div>
        <button class="btn btn-ghost btn-sm btn-square -mr-2" aria-label="Close" title="Close (Esc)" @click="$emit('close')">
          <X :size="16" aria-hidden="true" />
        </button>
      </header>

      <div class="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-5 text-sm">
        <p v-if="state === 'loading'" class="flex items-center gap-2 py-6 text-base-content/65" role="status">
          <span class="loading loading-spinner loading-sm" aria-hidden="true" />Looking it up…
        </p>
        <p v-else-if="state === 'error'" class="py-6" role="alert">{{ error }}</p>
        <template v-else-if="ev">
          <!-- Data -->
          <dl v-if="facts.length" class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            <template v-for="f in facts" :key="f.label">
              <dt class="text-base-content/60 text-xs pt-0.5">{{ f.label }}</dt>
              <dd class="tabular-nums [overflow-wrap:anywhere]">{{ f.value }}</dd>
            </template>
          </dl>

          <section v-if="ev.kind === 'gate' && ev.inputs?.length">
            <h4 class="panel-heading">Inputs</h4>
            <ul class="flex flex-wrap gap-x-3 gap-y-1">
              <li v-for="i in ev.inputs" :key="i">
                <button class="hover:underline" @click="$emit('open-event', i)">{{ i }}</button>
              </li>
            </ul>
            <button v-if="ev.tree" class="btn btn-xs btn-ghost mt-2 -ml-2" @click="$emit('open-tree', ev.tree, name)">Show it in {{ ev.tree }}</button>
          </section>
          <section v-if="ev.kind === 'fault_tree'">
            <button class="btn btn-xs btn-ghost -ml-2" @click="$emit('open-tree', name)">Open the fault tree</button>
          </section>

          <!-- Where it is used -->
          <section>
            <h4 class="panel-heading">Used in {{ plural(ev.used_in_gates_total, 'gate') }}</h4>
            <p v-if="!ev.used_in_gates_total" class="text-base-content/60">No gate takes it as an input.</p>
            <ul v-else class="divide-y divide-base-300/60">
              <li v-for="u in ev.used_in_gates" :key="`${u.tree}|${u.gate}`" class="py-1.5 flex items-baseline gap-2">
                <button class="font-medium hover:underline text-left [overflow-wrap:anywhere]" :title="u.tree ? `Show ${u.gate} in fault tree ${u.tree}` : ''" @click="u.tree ? $emit('open-tree', u.tree, u.gate) : $emit('open-event', u.gate)">
                  <span v-if="u.complemented" class="text-base-content/60">/</span>{{ u.gate }}
                </button>
                <span class="text-xs text-base-content/60">{{ (u.type || '').toUpperCase() }}<template v-if="u.tree"> in {{ u.tree }}</template></span>
              </li>
            </ul>
            <p v-if="ev.used_in_gates_total > ev.used_in_gates.length" class="text-xs text-base-content/60 mt-1">and {{ ev.used_in_gates_total - ev.used_in_gates.length }} more</p>
          </section>

          <section v-if="ev.event_trees?.length">
            <h4 class="panel-heading">Event trees</h4>
            <ul class="flex flex-wrap gap-x-3 gap-y-1">
              <li v-for="t in ev.event_trees" :key="t"><button class="hover:underline" @click="$emit('open-event-tree', t)">{{ t }}</button></li>
            </ul>
          </section>

          <section v-if="ev.sequences_total">
            <h4 class="panel-heading">In the logic of {{ plural(ev.sequences_total, 'sequence') }}</h4>
            <ul class="divide-y divide-base-300/60">
              <li v-for="s in ev.sequences" :key="`${s.event_tree}|${s.sequence}`" class="py-1.5 flex flex-wrap items-baseline gap-x-2">
                <button class="font-medium hover:underline" @click="$emit('open-event-tree', s.event_tree)">{{ s.event_tree }} / {{ s.sequence }}</button>
                <span class="text-xs text-base-content/60">{{ s.branch }} branch<template v-if="s.end_state"> · ends in {{ s.end_state }}</template></span>
              </li>
            </ul>
            <p v-if="ev.sequences_total > ev.sequences.length" class="text-xs text-base-content/60 mt-1">and {{ ev.sequences_total - ev.sequences.length }} more</p>
          </section>

          <section v-if="ev.stored_cut_sets">
            <h4 class="panel-heading">In {{ plural(dedup.total, 'stored cut set') }}</h4>
            <p v-if="!dedup.total" class="text-base-content/60">None of the model's stored cut sets contain it.</p>
            <ul v-else class="divide-y divide-base-300/60">
              <li v-for="c in contexts" :key="`${c.kind}|${c.event_tree || ''}|${c.context}`" class="py-1.5 flex items-baseline gap-2">
                <button class="font-medium hover:underline text-left [overflow-wrap:anywhere]" @click="$emit('open-cut-sets', { context: c.context, kind: c.kind, eventTree: c.event_tree })">
                  {{ c.event_tree ? `${c.event_tree} / ${c.context}` : c.context }}
                </button>
                <span class="text-xs text-base-content/60">{{ KIND_WORDS[c.kind] || c.kind }}</span>
                <span class="flex-1" />
                <span class="text-xs tabular-nums text-base-content/70">{{ c.cut_sets.toLocaleString('en-US') }}</span>
              </li>
            </ul>
          </section>
        </template>
      </div>
    </aside>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { praApi } from '../utils/praApi'
import { plural, ratio, sci } from '../utils/praFormat'
import { useOverlayFocus } from '../composables/useOverlayFocus'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
  name: { type: String, required: true },
})
const emit = defineEmits(['close', 'open-event', 'open-tree', 'open-event-tree', 'open-cut-sets'])

const KIND_LABELS = { basic_event: 'Basic event', house_event: 'House event', initiating_event: 'Initiating event', gate: 'Gate', fault_tree: 'Fault tree' }
const KIND_WORDS = { fault_tree: 'fault tree', sequence: 'sequence', end_state: 'end state' }

const panel = ref(null)
const open = ref(true)
useOverlayFocus(open, panel, { onEscape: () => emit('close') })
const titleId = `pra-event-${Math.random().toString(36).slice(2, 8)}`

const state = ref('loading')
const error = ref('')
const ev = ref(null)

async function load() {
  state.value = 'loading'
  try {
    ev.value = await praApi.event(props.collectionId, props.documentId, props.name)
    state.value = 'ready'
  } catch (err) {
    error.value = err?.message || 'Could not look this event up.'
    state.value = 'error'
  }
}
watch(() => props.name, load, { immediate: true })

const kindLabel = computed(() => KIND_LABELS[ev.value?.kind] || 'Event')
const facts = computed(() => {
  const e = ev.value
  if (!e) return []
  const out = []
  if (e.kind === 'basic_event') {
    out.push({ label: 'Probability', value: sci(e.probability) })
    if (e.failure_rate !== null && e.failure_rate !== undefined) out.push({ label: 'Failure rate', value: `${sci(e.failure_rate)} per hour` })
    if (e.mission_time !== null && e.mission_time !== undefined) out.push({ label: 'Mission time', value: `${ratio(e.mission_time)} h` })
    if (e.calc_type) out.push({ label: 'Calculation type', value: e.calc_type })
    if (e.uncertainty) out.push({ label: 'Uncertainty', value: e.uncertainty })
    for (const [k, v] of Object.entries(e.attributes || {}).slice(0, 12)) out.push({ label: k, value: String(v) })
  } else if (e.kind === 'house_event') {
    out.push({ label: 'State', value: e.state ? 'TRUE' : 'FALSE' })
  } else if (e.kind === 'initiating_event') {
    out.push({ label: 'Frequency', value: sci(e.frequency) })
  } else if (e.kind === 'gate') {
    out.push({ label: 'Type', value: e.type === 'atleast' ? `${e.k} of ${e.inputs?.length}` : String(e.type || '').toUpperCase() })
    if (e.tree) out.push({ label: 'Fault tree', value: e.tree })
  } else if (e.kind === 'fault_tree' && e.top) {
    out.push({ label: 'Top gate', value: e.top })
  }
  return out
})
// An export can carry one list twice (two end state files): show it, and
// count it, once.
const dedup = computed(() => {
  const seen = new Set()
  let repeated = 0
  const list = (ev.value?.stored_cut_sets?.by_context || []).filter((c) => {
    const key = `${c.kind}|${c.event_tree || ''}|${c.context}`
    if (seen.has(key)) { repeated += c.cut_sets; return false }
    seen.add(key)
    return true
  })
  return { list, total: (ev.value?.stored_cut_sets?.total || 0) - repeated }
})
const contexts = computed(() => dedup.value.list)
</script>

<style scoped>
.panel-heading { font-size: 0.75rem; font-weight: 600; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); margin-bottom: 0.25rem; }
</style>
