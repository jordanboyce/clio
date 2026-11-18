<template>
  <!-- A fault tree's logic as an outline: gates with their type and
       description, basic events with their probability, house events with
       their state, transfers that jump to the other tree. One flat list of
       rows with tree semantics (WAI-ARIA tree: arrows move, Right/Left open
       and close, * opens the siblings, Enter follows), so hundreds of gates
       stay one list. Deeper levels load when a gate is opened. -->
  <div class="pt-1" data-testid="pra-outline">
    <p v-if="state === 'loading'" class="flex items-center gap-2 py-8 text-sm text-base-content/65" role="status">
      <span class="loading loading-spinner loading-sm" aria-hidden="true" />Reading the logic…
    </p>
    <div v-else-if="state === 'error'" class="py-8 space-y-2" role="alert">
      <p class="text-sm">{{ error }}</p>
      <button class="btn btn-sm" @click="load">Try again</button>
    </div>
    <template v-else-if="root">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1 pb-2 text-xs text-base-content/60">
        <span class="tabular-nums">{{ shownLabel }}</span>
        <span class="flex-1" />
        <button class="btn btn-ghost btn-xs" data-testid="pra-outline-open-all" @click="openAll">Open all loaded</button>
        <button class="btn btn-ghost btn-xs" @click="closeAll">Close all</button>
      </div>
      <p v-if="view.truncated" class="text-xs text-base-content/65 pb-2">
        A large tree: the first {{ maxNodes.toLocaleString('en-US') }} nodes came at once; open a gate marked “more” to read further.
      </p>
      <ul
        ref="list"
        role="tree"
        :aria-label="`Logic of ${tree}`"
        class="outline-rows"
        data-testid="pra-outline-tree"
        @keydown="onKeydown"
      >
        <li
          v-for="row in rows"
          :key="row.key"
          :ref="(el) => setRowEl(row.key, el)"
          role="treeitem"
          :aria-level="row.level"
          :aria-setsize="row.setsize"
          :aria-posinset="row.posinset"
          :aria-expanded="row.expandable ? row.expanded : undefined"
          :aria-selected="focusKey === row.key"
          :aria-busy="loading.has(row.key) || undefined"
          :aria-label="rowLabel(row)"
          :tabindex="focusKey === row.key ? 0 : -1"
          class="outline-row group"
          :class="[focusKey === row.key ? 'is-focus' : '', revealKey === row.key ? 'is-reveal' : '']"
          :style="{ '--level': row.level - 1 }"
          @click="onRowClick(row, $event)"
          @focus="focusKey = row.key"
        >
          <span class="outline-indent" aria-hidden="true" />
          <span class="outline-toggle" aria-hidden="true">
            <span v-if="loading.has(row.key)" class="loading loading-spinner loading-xs" />
            <ChevronRight v-else-if="row.expandable" :size="13" class="transition-transform" :class="row.expanded ? 'rotate-90' : ''" />
          </span>
          <span class="min-w-0 flex-1 flex flex-wrap items-baseline gap-x-2 gap-y-0 py-1">
            <!-- the kind of node -->
            <span v-if="row.node.kind === 'gate'" class="gate-type" :title="gateMeaning(row.node)">{{ gateLabel(row.node) }}</span>
            <span v-else-if="row.node.kind === 'basic_event'" class="node-mark" title="Basic event"><Circle :size="7" fill="currentColor" /></span>
            <span v-else-if="row.node.kind === 'house_event'" class="node-mark" title="House event"><House :size="12" /></span>
            <span v-else-if="row.node.kind === 'transfer'" class="node-mark" title="Transfer to another fault tree"><CornerDownRight :size="12" /></span>
            <span v-else class="node-mark" title="Not defined in the model"><Circle :size="8" /></span>

            <button
              type="button"
              tabindex="-1"
              class="node-name"
              :class="row.node.kind === 'gate' ? 'font-semibold' : 'font-medium'"
              :title="row.node.kind === 'transfer' ? `Open fault tree ${row.node.tree}` : 'Where it is used'"
              @click.stop="activate(row, 'name')"
            ><span v-if="row.node.complemented" class="text-base-content/60" title="Complemented: the success of this input">/</span>{{ row.node.name }}</button>

            <span v-if="row.node.kind === 'transfer'" class="text-xs text-base-content/65">
              transfer to <button type="button" tabindex="-1" class="link link-hover" @click.stop="$emit('open-tree', row.node.tree)">{{ row.node.tree }}</button>
            </span>
            <span v-else-if="row.node.kind === 'undefined'" class="text-xs text-warning">not defined in the model</span>
            <span v-if="row.node.description" class="node-desc">{{ row.node.description }}</span>
            <span v-if="row.cycle" class="text-xs text-base-content/60">(repeats a gate above; not opened again)</span>
            <button
              v-else-if="row.node.more && !row.node.children && !row.expanded"
              type="button"
              tabindex="-1"
              class="text-xs link link-hover text-base-content/60"
              @click.stop="expand(row.key)"
            >{{ plural(row.node.input_count, 'input') }} more</button>
            <span v-if="loadErrors[row.key]" class="text-xs text-error basis-full">{{ loadErrors[row.key] }}</span>
          </span>
          <!-- the value, right-aligned -->
          <span v-if="row.node.kind === 'basic_event' || row.node.kind === 'initiating_event'" class="node-value" :title="row.node.complemented ? `p = ${sci(row.node.probability ?? row.node.frequency)}; its success counts 1 − p` : undefined">
            {{ sci(row.node.probability ?? row.node.frequency) }}
          </span>
          <span v-else-if="row.node.kind === 'house_event'" class="node-value">{{ row.node.state ? 'TRUE' : 'FALSE' }}</span>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, shallowRef } from 'vue'
import { ChevronRight, Circle, CornerDownRight, House } from 'lucide-vue-next'
import { praApi } from '../utils/praApi'
import { gateLabel, gateMeaning, plural, sci } from '../utils/praFormat'
import {
  ROOT_KEY, expandAllLoaded, findInOutline, flattenOutline, graft, initialExpansion, nodeAt, outlineKeyAction,
} from '../utils/praOutline'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
  tree: { type: String, required: true },
  // A gate or event to show and focus once the outline is loaded.
  reveal: { type: String, default: '' },
  // tree -> { view, expanded, focusKey }: kept by the viewer across trees.
  cache: { type: Map, default: null },
})
const emit = defineEmits(['open-tree', 'open-event'])

const FIRST_DEPTH = 4
const MORE_DEPTH = 3
const maxNodes = 2000

const state = ref('loading')
const error = ref('')
const view = shallowRef(null)
const expanded = ref(new Set())
const version = ref(0)
const focusKey = ref(ROOT_KEY)
const revealKey = ref('')
const loading = ref(new Set())
const loadErrors = ref({})
const list = ref(null)
const rowEls = new Map()

const root = computed(() => view.value?.structure || null)
const rows = computed(() => {
  void version.value
  return flattenOutline(root.value, expanded.value)
})
const shownLabel = computed(() => {
  const gates = rows.value.filter(r => r.node.kind === 'gate').length
  return `${plural(rows.value.length, 'row')} open · ${plural(gates, 'gate')}`
})

function setRowEl(key, el) {
  if (el) rowEls.set(key, el)
  else rowEls.delete(key)
}

function remember() {
  props.cache?.set(props.tree, { view: view.value, expanded: expanded.value, focusKey: focusKey.value })
}

async function load() {
  const cached = props.cache?.get(props.tree)
  if (cached) {
    view.value = cached.view
    expanded.value = cached.expanded
    focusKey.value = cached.focusKey
    state.value = 'ready'
    afterLoad()
    return
  }
  state.value = 'loading'
  try {
    view.value = await praApi.faultTree(props.collectionId, props.documentId, props.tree, FIRST_DEPTH)
    expanded.value = initialExpansion(view.value.structure)
    focusKey.value = ROOT_KEY
    state.value = view.value.structure ? 'ready' : 'empty'
    if (!view.value.structure) {
      error.value = 'This fault tree has no logic in the files that were read.'
      state.value = 'error'
    }
    remember()
    afterLoad()
  } catch (err) {
    error.value = err?.message || 'The logic could not be loaded.'
    state.value = 'error'
  }
}
onMounted(load)

function afterLoad() {
  if (!props.reveal || !root.value) return
  const hit = findInOutline(root.value, props.reveal)
  if (!hit) return
  expanded.value = new Set([...expanded.value, ...hit.open])
  focusKey.value = hit.key
  revealKey.value = hit.key
  nextTick(() => focusRow(hit.key, { scroll: true }))
}

async function expand(key) {
  const node = nodeAt(root.value, key)
  if (!node) return
  expanded.value = new Set(expanded.value).add(key)
  if (node.more && !node.children) {
    loading.value = new Set(loading.value).add(key)
    const { [key]: _drop, ...rest } = loadErrors.value
    loadErrors.value = rest
    try {
      const sub = await praApi.faultTree(props.collectionId, props.documentId, node.name, MORE_DEPTH)
      graft(node, sub.structure)
      version.value++
    } catch (err) {
      loadErrors.value = { ...loadErrors.value, [key]: err?.message || 'Could not load this gate' }
      const next = new Set(expanded.value)
      next.delete(key)
      expanded.value = next
    } finally {
      const next = new Set(loading.value)
      next.delete(key)
      loading.value = next
    }
  }
  remember()
}
function collapse(key) {
  const next = new Set(expanded.value)
  next.delete(key)
  expanded.value = next
  remember()
}
function toggle(row) {
  if (!row.expandable) return
  if (row.expanded) collapse(row.key)
  else expand(row.key)
}
function openAll() {
  expanded.value = expandAllLoaded(root.value, expanded.value)
  remember()
}
function closeAll() {
  expanded.value = new Set([ROOT_KEY])
  focusKey.value = ROOT_KEY
  remember()
}

// Enter (or a click on the name) follows the row: a gate opens or closes, a
// transfer opens its tree, an event shows where it is used.
function activate(row, via = 'key') {
  focusKey.value = row.key
  const n = row.node
  if (n.kind === 'transfer') return emit('open-tree', n.tree)
  if (n.kind === 'gate' && via === 'key') return toggle(row)
  return emit('open-event', n.name)
}
function onRowClick(row) {
  focusKey.value = row.key
  if (row.expandable) toggle(row)
}

function focusRow(key, { scroll = false } = {}) {
  focusKey.value = key
  const el = rowEls.get(key)
  if (!el) return
  el.focus({ preventScroll: !scroll })
  if (scroll) el.scrollIntoView({ block: 'center' })
  else el.scrollIntoView({ block: 'nearest' })
}

function onKeydown(event) {
  if (event.target?.getAttribute?.('role') !== 'treeitem') return
  const action = outlineKeyAction(rows.value, focusKey.value, event.key)
  if (!action) return
  event.preventDefault()
  if (action.focus) nextTick(() => focusRow(action.focus))
  else if (action.expand) expand(action.expand)
  else if (action.collapse) collapse(action.collapse)
  else if (action.expandSiblings) action.expandSiblings.forEach(expand)
  else if (action.activate) {
    const row = rows.value.find(r => r.key === action.activate)
    if (row) activate(row)
  }
  remember()
}

function rowLabel(row) {
  const n = row.node
  const name = `${n.complemented ? 'not ' : ''}${n.name}`
  if (n.kind === 'gate') return `${name}, ${gateLabel(n)} gate${n.description ? `, ${n.description}` : ''}${row.cycle ? ', repeats a gate above' : ''}`
  if (n.kind === 'basic_event') return `${name}, basic event${n.description ? `, ${n.description}` : ''}, probability ${sci(n.probability)}`
  if (n.kind === 'house_event') return `${name}, house event, ${n.state ? 'true' : 'false'}`
  if (n.kind === 'transfer') return `${name}, transfer to fault tree ${n.tree}`
  if (n.kind === 'initiating_event') return `${name}, initiating event, frequency ${sci(n.frequency)}`
  return `${name}, not defined in the model`
}
</script>

<style scoped>
.outline-rows { font-size: 0.875rem; }
.outline-row {
  display: flex;
  align-items: flex-start;
  gap: 0.25rem;
  padding-right: 0.25rem;
  border-radius: 0.375rem;
  cursor: default;
  scroll-margin-top: 7rem;
}
.outline-row:hover { background-color: color-mix(in oklch, var(--color-base-content) 4%, transparent); }
.outline-row.is-focus { background-color: color-mix(in oklch, var(--color-base-content) 7%, transparent); }
.outline-row:focus-visible { outline: 2px solid var(--color-primary); outline-offset: -2px; }
.outline-row.is-reveal .node-name { text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 3px; }
/* One guide per level: thin rules a reader can follow down the tree. */
.outline-indent {
  flex-shrink: 0;
  align-self: stretch;
  width: calc(var(--level) * 1.125rem);
  background-image: repeating-linear-gradient(to right,
    transparent 0 0.5rem,
    color-mix(in oklch, var(--color-base-content) 14%, transparent) 0.5rem calc(0.5rem + 1px),
    transparent calc(0.5rem + 1px) 1.125rem);
}
/* A phone keeps deep trees readable with narrower steps. */
@container (max-width: 40rem) {
  .outline-indent {
    width: calc(var(--level) * 0.75rem);
    background-image: repeating-linear-gradient(to right,
      transparent 0 0.375rem,
      color-mix(in oklch, var(--color-base-content) 14%, transparent) 0.375rem calc(0.375rem + 1px),
      transparent calc(0.375rem + 1px) 0.75rem);
  }
}
.outline-toggle {
  flex-shrink: 0;
  display: inline-grid;
  place-items: center;
  width: 1rem;
  height: 1.75rem;
  color: color-mix(in oklch, var(--color-base-content) 55%, transparent);
}
.gate-type {
  display: inline-block;
  min-width: 2.25rem;
  padding: 0 0.25rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
  font-weight: 600;
  line-height: 1.25rem;
  text-align: center;
  white-space: nowrap;
  border: 1px solid color-mix(in oklch, var(--color-base-content) 22%, transparent);
  color: color-mix(in oklch, var(--color-base-content) 80%, transparent);
}
.node-mark {
  display: inline-grid;
  place-items: center;
  width: 2.25rem;
  color: color-mix(in oklch, var(--color-base-content) 50%, transparent);
  align-self: center;
}
.node-name { text-align: left; overflow-wrap: anywhere; }
.node-name:hover { text-decoration: underline; text-underline-offset: 2px; }
.node-desc {
  font-size: 0.75rem;
  color: color-mix(in oklch, var(--color-base-content) 70%, transparent);
  overflow-wrap: anywhere;
  min-width: 0;
}
.node-value {
  flex-shrink: 0;
  padding-top: 0.25rem;
  font-size: 0.8125rem;
  font-variant-numeric: tabular-nums;
  color: color-mix(in oklch, var(--color-base-content) 85%, transparent);
}
@media (prefers-reduced-motion: reduce) { .transition-transform { transition: none; } }
</style>
