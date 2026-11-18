<template>
  <!-- A PRA model (docs/PRA.md) opened as a source: what it is, then its
       fault trees, event trees and end states, each a list beside a reader
       (the Library's pattern). On narrow screens the list and the reader take
       turns. Everything is read from /api/pra; nothing here computes. -->
  <div class="@container h-full flex flex-col min-h-0 bg-base-100" data-testid="pra-viewer">
    <!-- Overview: the model, where it came from, its size and what the reader could not represent -->
    <header class="shrink-0 border-b border-base-300/70 px-4 pt-3 pb-3 md:px-6 space-y-1.5" :class="reading ? '@max-4xl:pb-2' : ''">
      <div class="flex items-start gap-2">
        <button class="btn btn-ghost btn-sm btn-square -ml-2 shrink-0" :aria-label="closeLabel" :title="closeLabel" data-testid="pra-close" @click="$emit('close')">
          <ArrowLeft :size="16" aria-hidden="true" />
        </button>
        <div class="min-w-0 flex-1">
          <h2 ref="heading" tabindex="-1" class="pra-heading text-lg font-semibold leading-snug [overflow-wrap:anywhere]">
            {{ model?.name || filename || 'PRA model' }}
          </h2>
          <p class="text-xs text-base-content/65 [overflow-wrap:anywhere]">
            {{ tool.format }}<span v-if="model?.source_files?.length" :title="filesTitle"> · {{ plural(model.source_files.length, 'file') }}</span><span v-if="model?.filename"> · {{ model.filename }}</span>
          </p>
        </div>
      </div>
      <template v-if="model">
        <p class="text-xs text-base-content/65 tabular-nums" :class="reading ? '@max-4xl:hidden' : ''" data-testid="pra-counts">{{ countsLine(model.counts) }}</p>
        <p v-if="model.description" class="text-sm text-base-content/80 max-w-[72ch]" :class="reading ? '@max-4xl:hidden' : ''">{{ model.description }}</p>
        <section v-if="model.warnings?.length" class="max-w-[90ch]" :class="reading ? '@max-4xl:hidden' : ''" aria-label="What Clio could not represent" data-testid="pra-warnings">
          <ul class="space-y-0.5">
            <li v-for="(w, i) in shownWarnings" :key="i" class="flex items-start gap-1.5 text-xs text-base-content/75">
              <AlertTriangle :size="12" class="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
              <span :class="warningsOpen ? '' : 'line-clamp-1'">{{ w }}</span>
            </li>
          </ul>
          <button
            v-if="warningsClamped"
            class="mt-0.5 ml-[1.125rem] text-xs link link-hover text-base-content/60"
            :aria-expanded="warningsOpen"
            data-testid="pra-warnings-toggle"
            @click="warningsOpen = !warningsOpen"
          >{{ warningsOpen ? 'Show less' : `Show ${model.warnings.length > 1 ? 'all' : 'in full'}${model.warnings_total ? ` (${model.warnings_total})` : ''}` }}</button>
        </section>
      </template>
    </header>

    <p v-if="loadState === 'loading'" class="flex items-center justify-center gap-2 py-16 text-sm text-base-content/65" role="status">
      <span class="loading loading-spinner loading-sm" aria-hidden="true" />Reading the model…
    </p>
    <div v-else-if="loadState === 'error'" class="py-16 text-center space-y-2 px-4" role="alert">
      <p class="text-sm">The model could not be opened.</p>
      <p class="text-xs text-base-content/65 max-w-md mx-auto">{{ loadError }}</p>
      <button class="btn btn-sm" @click="load()">Try again</button>
    </div>

    <template v-else-if="model">
      <!-- Which part of the model -->
      <div class="shrink-0 px-4 md:px-6 pt-2.5 pb-2" :class="reading ? '@max-4xl:hidden' : ''">
        <div class="segmented max-w-full overflow-x-auto" role="tablist" aria-label="Part of the model">
          <button
            v-for="v in views"
            :key="v.id"
            class="segment"
            role="tab"
            :aria-selected="loc.view === v.id"
            :class="loc.view === v.id ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
            :data-testid="`pra-view-${v.id}`"
            @click="showView(v.id)"
          >{{ v.label }}<span class="tabular-nums text-base-content/55">{{ v.count.toLocaleString('en-US') }}</span></button>
        </div>
      </div>

      <!-- List and reader -->
      <div class="flex-1 min-h-0 @4xl:grid @4xl:grid-cols-[minmax(15rem,20rem)_minmax(0,1fr)]">
        <!-- List -->
        <nav
          class="h-full min-h-0 flex flex-col @4xl:border-r @4xl:border-base-300/70"
          :class="reading ? '@max-4xl:hidden' : ''"
          :aria-label="currentView.label"
        >
          <div class="shrink-0 px-4 md:px-6 @4xl:pr-3 pb-2">
            <label class="input input-sm input-bordered flex items-center gap-2 w-full">
              <Search :size="13" class="text-base-content/50 shrink-0" aria-hidden="true" />
              <input v-model="filter" type="search" class="grow min-w-0" :placeholder="currentView.placeholder" :aria-label="currentView.placeholder" data-testid="pra-filter" />
            </label>
            <p v-if="filter" class="text-xs text-base-content/60 mt-1 tabular-nums">{{ filtered.length.toLocaleString('en-US') }} of {{ currentItems.length.toLocaleString('en-US') }}</p>
          </div>
          <ul ref="listEl" class="flex-1 min-h-0 overflow-y-auto divide-y divide-base-300/60 border-t border-base-300/60" data-testid="pra-list">
            <li v-for="item in visibleItems" :key="item.name">
              <button
                class="w-full text-left px-4 md:px-6 @4xl:pr-3 py-2 block"
                :class="loc.name === item.name ? 'bg-base-200/80' : 'hover:bg-base-200/40'"
                :aria-current="loc.name === item.name ? 'true' : undefined"
                @click="select(item.name)"
              >
                <span class="block text-sm font-medium leading-snug [overflow-wrap:anywhere]">{{ item.name }}</span>
                <span v-if="item.description" class="block text-xs text-base-content/70 leading-snug line-clamp-2">{{ item.description }}</span>
                <span v-if="item.meta" class="block text-xs text-base-content/55 tabular-nums mt-0.5">{{ item.meta }}</span>
              </button>
            </li>
            <li v-if="filtered.length > visibleItems.length" class="px-4 md:px-6 py-2">
              <button class="btn btn-xs btn-ghost" @click="listLimit += 400">Show {{ Math.min(400, filtered.length - visibleItems.length) }} more</button>
            </li>
            <li v-if="!filtered.length" class="px-4 md:px-6 py-6 text-sm text-base-content/60">
              {{ filter ? `Nothing matches “${filter}”.` : currentView.empty }}
            </li>
          </ul>
        </nav>

        <!-- Reader -->
        <article
          v-if="loc.name"
          ref="reader"
          class="h-full min-h-0 overflow-y-auto px-4 md:px-6 pt-1 pb-8 @4xl:pt-0"
          :aria-label="`${loc.name}`"
          data-testid="pra-reader"
        >
          <div class="sticky top-0 z-10 bg-base-100 pt-2 pb-2 -mx-1 px-1">
            <div class="flex flex-wrap items-center gap-x-1 gap-y-1">
              <button class="btn btn-ghost btn-xs -ml-2 @4xl:hidden" data-testid="pra-back-to-list" @click="clearSelection">
                <ArrowLeft :size="13" aria-hidden="true" />{{ currentView.allLabel }}
              </button>
              <button v-if="history.length" class="btn btn-ghost btn-xs -ml-2 max-w-full" data-testid="pra-history-back" @click="back">
                <Undo2 :size="13" aria-hidden="true" /><span class="truncate">Back to {{ historyLabel }}</span>
              </button>
            </div>
            <h3 class="text-base font-semibold leading-snug [overflow-wrap:anywhere] mt-0.5">
              <template v-if="loc.sequence">
                <button class="link link-hover" @click="go({ view: 'event-trees', name: loc.name })">{{ loc.name }}</button>
                <span class="text-base-content/50"> / </span>sequence {{ loc.sequence }}
              </template>
              <template v-else>{{ loc.name }}</template>
            </h3>
            <p v-if="readerSubtitle" class="text-sm text-base-content/75 leading-snug">{{ readerSubtitle }}</p>
            <p v-if="readerMeta" class="text-xs text-base-content/60 tabular-nums mt-0.5">{{ readerMeta }}</p>
            <div v-if="panes.length > 1" class="segmented mt-2" role="tablist" aria-label="Show">
              <button
                v-for="p in panes"
                :key="p.id"
                class="segment"
                role="tab"
                :aria-selected="loc.pane === p.id"
                :class="loc.pane === p.id ? 'is-on text-base-content' : 'text-base-content/65 hover:text-base-content'"
                :data-testid="`pra-pane-${p.id}`"
                @click="loc = { ...loc, pane: p.id }"
              >{{ p.label }}</button>
            </div>
          </div>

          <PraFaultTreeOutline
            v-if="loc.view === 'fault-trees' && loc.pane === 'logic'"
            :key="`ft:${loc.name}`"
            :collection-id="collectionId"
            :document-id="documentId"
            :tree="loc.name"
            :reveal="loc.reveal || ''"
            :cache="outlineCache"
            @open-tree="(name, reveal) => go({ view: 'fault-trees', name, reveal })"
            @open-event="openEvent"
          />
          <PraEventTree
            v-else-if="loc.view === 'event-trees' && !loc.sequence"
            :key="`et:${loc.name}`"
            :collection-id="collectionId"
            :document-id="documentId"
            :name="loc.name"
            :fault-trees="treeNames"
            @open-tree="(name) => go({ view: 'fault-trees', name })"
            @open-event-tree="(name) => go({ view: 'event-trees', name })"
            @open-sequence="(seq) => go({ view: 'event-trees', name: loc.name, sequence: seq, pane: 'cut-sets' })"
            @open-event="openEvent"
          />
          <PraAnalysis
            v-else
            :key="`an:${analysis.context}:${analysis.eventTree || ''}:${loc.pane}`"
            :collection-id="collectionId"
            :document-id="documentId"
            :context="analysis.context"
            :event-tree="analysis.eventTree"
            :kind="analysis.kind"
            :mode="loc.pane === 'importance' ? 'importance' : 'cut-sets'"
            :tool="tool.tool"
            :compute-options="computeOptions[analysisKey] || null"
            :stored="analysis.kind === 'fault_tree' ? Boolean(treeInfo?.has_cut_sets) : null"
            @computed="(opts) => { computeOptions = { ...computeOptions, [analysisKey]: opts } }"
            @open-event="openEvent"
          />
        </article>
        <div v-else class="hidden @4xl:block px-6 py-10 text-sm text-base-content/65" data-testid="pra-reader-empty">
          <p>{{ currentView.choose }}</p>
          <p v-if="loc.view === 'fault-trees' && storedTrees" class="mt-1 text-xs text-base-content/55">
            {{ plural(storedTrees, 'fault tree') }} carry cut sets from the model; the others can be computed here.
          </p>
        </div>
      </div>
    </template>

    <PraEventPanel
      v-if="eventName"
      :collection-id="collectionId"
      :document-id="documentId"
      :name="eventName"
      @close="closeEvent"
      @open-event="openEvent"
      @open-tree="(name, reveal) => { closeEvent(); go({ view: 'fault-trees', name, reveal }) }"
      @open-event-tree="(name) => { closeEvent(); go({ view: 'event-trees', name }) }"
      @open-cut-sets="openCutSetList"
    />
  </div>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { AlertTriangle, ArrowLeft, Search, Undo2 } from 'lucide-vue-next'
import PraFaultTreeOutline from './PraFaultTreeOutline.vue'
import PraEventTree from './PraEventTree.vue'
import PraAnalysis from './PraAnalysis.vue'
import PraEventPanel from './PraEventPanel.vue'
import { praApi } from '../utils/praApi'
import { countsLine, modelTool, plural } from '../utils/praFormat'

const props = defineProps({
  documentId: { type: String, required: true },
  collectionId: { type: String, default: 'default' },
  filename: { type: String, default: '' },
  // { view: 'fault-trees' | 'event-trees', name } to open on, e.g. from a citation.
  focus: { type: Object, default: null },
  // What the back arrow returns to ("Ask"), for its label.
  returnLabel: { type: String, default: '' },
})
defineEmits(['close'])

const closeLabel = computed(() => (props.returnLabel ? `Back to ${props.returnLabel}` : 'Close the model'))

const model = ref(null)
const loadState = ref('loading')
const loadError = ref('')
const heading = ref(null)
const reader = ref(null)
const listEl = ref(null)
const warningsOpen = ref(false)

async function load() {
  loadState.value = 'loading'
  try {
    model.value = await praApi.model(props.collectionId, props.documentId)
    loadState.value = 'ready'
    applyFocus(props.focus)
  } catch (err) {
    loadError.value = err?.message || 'The request failed.'
    loadState.value = 'error'
  }
}
onMounted(() => {
  load()
  nextTick(() => heading.value?.focus())
})

const tool = computed(() => modelTool(model.value?.model_format, model.value?.source_format))
const filesTitle = computed(() => {
  const files = model.value?.source_files || []
  return files.slice(0, 40).join('\n') + (files.length > 40 ? `\nand ${files.length - 40} more` : '')
})
// One line per warning until asked; the first two always show.
const shownWarnings = computed(() => (warningsOpen.value ? model.value.warnings : model.value.warnings.slice(0, 3)))
const warningsClamped = computed(() => (model.value?.warnings || []).some(w => w.length > 110) || (model.value?.warnings?.length || 0) > 3)

// ── lists ──
const storedLists = computed(() => {
  // A list per (kind, event tree, context); an export can repeat one (two end state files).
  const out = new Map()
  for (const c of model.value?.cut_set_lists || []) {
    const key = `${c.kind}|${c.event_tree || ''}|${c.context}`
    if (!out.has(key)) out.set(key, c)
  }
  return out
})
const storedFor = (kind, context, eventTree = '') => storedLists.value.get(`${kind}|${eventTree}|${context}`) || null
const treeNames = computed(() => new Set((model.value?.fault_trees || []).map(t => t.name)))
const storedTrees = computed(() => (model.value?.fault_trees || []).filter(t => t.has_cut_sets).length)

const faultTreeItems = computed(() => (model.value?.fault_trees || []).map(t => {
  const stored = storedFor('fault_tree', t.name)
  const parts = [plural(t.gate_count, 'gate')]
  if (t.top && t.top !== t.name) parts.push(`top ${t.top}`)
  if (stored) parts.push(`${plural(stored.cut_sets, 'cut set')} stored`)
  else if (t.has_cut_sets) parts.push('cut sets stored')
  return { name: t.name, description: t.description, meta: parts.join(' · ') }
}))
const eventTreeItems = computed(() => (model.value?.event_trees || []).map(e => ({
  name: e.name,
  description: e.description,
  meta: [e.initiating_event, plural(e.sequences, 'sequence')].filter(Boolean).join(' · '),
})))
const endStateItems = computed(() => {
  const names = new Map((model.value?.end_states || []).map(e => [e.name, e.description]))
  for (const c of storedLists.value.values()) if (c.kind === 'end_state' && !names.has(c.context)) names.set(c.context, '')
  return [...names].map(([name, description]) => {
    const stored = storedFor('end_state', name)
    return { name, description, meta: stored ? `${plural(stored.cut_sets, 'cut set')} stored` : 'No cut sets stored' }
  })
})

const views = computed(() => [
  { id: 'fault-trees', label: 'Fault trees', count: faultTreeItems.value.length, items: faultTreeItems.value,
    placeholder: 'Find a fault tree', allLabel: 'All fault trees', empty: 'This model has no fault trees.',
    choose: 'Choose a fault tree to read its logic, cut sets and importance.' },
  { id: 'event-trees', label: 'Event trees', count: eventTreeItems.value.length, items: eventTreeItems.value,
    placeholder: 'Find an event tree', allLabel: 'All event trees', empty: 'This model has no event trees.',
    choose: 'Choose an event tree to read its sequences.' },
  { id: 'end-states', label: 'End states', count: endStateItems.value.length, items: endStateItems.value,
    placeholder: 'Find an end state', allLabel: 'All end states', empty: 'This model has no end states.',
    choose: 'Choose an end state to read its cut sets.' },
].filter(v => v.count || v.id === 'fault-trees'))
const currentView = computed(() => views.value.find(v => v.id === loc.value.view) || views.value[0])
const currentItems = computed(() => currentView.value.items)

const filter = ref('')
const listLimit = ref(400)
const filtered = computed(() => {
  const f = filter.value.trim().toLowerCase()
  if (!f) return currentItems.value
  return currentItems.value.filter(i => i.name.toLowerCase().includes(f) || String(i.description || '').toLowerCase().includes(f))
})
const visibleItems = computed(() => filtered.value.slice(0, listLimit.value))
watch(filter, () => { listLimit.value = 400 })

// ── where the reader is ──
// { view, name, pane, sequence, reveal }; history holds where Back returns
// to after following a transfer, an event or a sequence.
const loc = ref({ view: 'fault-trees', name: null, pane: 'logic', sequence: null })
const history = ref([])
const lastByView = {}
const reading = computed(() => Boolean(loc.value.name))

function defaultPane(view) {
  return view === 'fault-trees' ? 'logic' : view === 'event-trees' ? 'sequences' : 'cut-sets'
}

function go(next, { remember = true } = {}) {
  const target = { pane: defaultPane(next.view), sequence: null, reveal: '', ...next }
  if (target.view === 'event-trees' && target.sequence && !next.pane) target.pane = 'cut-sets'
  const same = target.view === loc.value.view && target.name === loc.value.name && target.sequence === loc.value.sequence
  if (remember && loc.value.name && !same) history.value = [...history.value.slice(-30), { ...loc.value, reveal: '' }]
  loc.value = target
  if (target.view !== currentView.value?.id) filter.value = ''
  nextTick(() => { if (!same && reader.value) reader.value.scrollTop = 0 })
}
function back() {
  const prev = history.value[history.value.length - 1]
  history.value = history.value.slice(0, -1)
  if (prev) loc.value = prev
}
const historyLabel = computed(() => {
  const prev = history.value[history.value.length - 1]
  if (!prev) return ''
  return prev.sequence ? `${prev.name} / ${prev.sequence}` : prev.name
})
function select(name) {
  go({ view: currentView.value.id, name }, { remember: false })
  history.value = []
}
function showView(id) {
  if (loc.value.name) lastByView[loc.value.view] = { ...loc.value }
  filter.value = ''
  history.value = []
  loc.value = lastByView[id] || { view: id, name: null, pane: defaultPane(id), sequence: null }
}
function clearSelection() {
  loc.value = { view: loc.value.view, name: null, pane: defaultPane(loc.value.view), sequence: null }
  history.value = []
}

function applyFocus(focus) {
  if (!focus?.name) return
  const known = focus.view === 'event-trees'
    ? (model.value.event_trees || []).some(e => e.name === focus.name)
    : treeNames.value.has(focus.name)
  if (!known) return
  go({ view: focus.view, name: focus.name }, { remember: false })
  // Show the opened tree in the list too, however far down it sits.
  nextTick(() => listEl.value?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'center' }))
}
watch(() => props.focus, (f) => { if (model.value) applyFocus(f) })

// ── the reader's header and panes ──
const treeInfo = computed(() => (model.value?.fault_trees || []).find(t => t.name === loc.value.name) || null)
const eventTreeInfo = computed(() => (model.value?.event_trees || []).find(e => e.name === loc.value.name) || null)
const endStateInfo = computed(() => endStateItems.value.find(e => e.name === loc.value.name) || null)

const panes = computed(() => {
  const v = loc.value.view
  if (v === 'fault-trees') return [{ id: 'logic', label: 'Logic' }, { id: 'cut-sets', label: 'Cut sets' }, { id: 'importance', label: 'Importance' }]
  if (v === 'event-trees' && !loc.value.sequence) return []
  return [{ id: 'cut-sets', label: 'Cut sets' }, { id: 'importance', label: 'Importance' }]
})
const readerSubtitle = computed(() => {
  const v = loc.value.view
  if (v === 'fault-trees') return treeInfo.value?.description || (treeInfo.value ? '' : 'A gate, read as its own tree')
  if (v === 'event-trees' && !loc.value.sequence) return eventTreeInfo.value?.description || ''
  if (v === 'end-states') return endStateInfo.value?.description || ''
  return ''
})
const readerMeta = computed(() => {
  const v = loc.value.view
  if (v === 'fault-trees' && treeInfo.value) {
    return [treeInfo.value.top && treeInfo.value.top !== treeInfo.value.name ? `Top gate ${treeInfo.value.top}` : '',
      plural(treeInfo.value.gate_count, 'gate')].filter(Boolean).join(' · ')
  }
  if (v === 'event-trees' && !loc.value.sequence && eventTreeInfo.value) {
    return [plural(eventTreeInfo.value.functional_events?.length || 0, 'functional event'),
      plural(eventTreeInfo.value.sequences, 'sequence')].filter(Boolean).join(' · ')
  }
  return ''
})
const analysis = computed(() => {
  const v = loc.value.view
  if (v === 'event-trees') return { context: loc.value.sequence, eventTree: loc.value.name, kind: 'sequence' }
  if (v === 'end-states') return { context: loc.value.name, eventTree: null, kind: 'end_state' }
  return { context: loc.value.name, eventTree: null, kind: treeInfo.value ? 'fault_tree' : 'gate' }
})
const analysisKey = computed(() => `${analysis.value.kind}|${analysis.value.eventTree || ''}|${analysis.value.context}`)
// The options a context was computed with in this visit, so Importance asks
// for the same list the Cut sets pane computed (the server has it cached).
const computeOptions = ref({})
// Each tree's outline keeps its loaded levels and open gates while the viewer is open.
const outlineCache = new Map()

// ── the event panel ──
const eventName = ref('')
let eventReturnFocus = null
function openEvent(name) {
  if (!eventName.value) eventReturnFocus = document.activeElement
  eventName.value = String(name || '').replace(/^\//, '')
}
function closeEvent() {
  eventName.value = ''
  const el = eventReturnFocus
  eventReturnFocus = null
  nextTick(() => { if (el && document.contains(el)) el.focus() })
}
function openCutSetList({ context, kind, eventTree }) {
  closeEvent()
  if (kind === 'sequence' && eventTree) go({ view: 'event-trees', name: eventTree, sequence: context, pane: 'cut-sets' })
  else if (kind === 'end_state') go({ view: 'end-states', name: context, pane: 'cut-sets' })
  else go({ view: 'fault-trees', name: context, pane: 'cut-sets' })
}
</script>

<style scoped>
/* Focus lands on the heading when the viewer opens, so a screen reader
   starts at the model's name; it is not a control, so it draws no ring. */
.pra-heading:focus, .pra-heading:focus-visible { outline: none; }
</style>
