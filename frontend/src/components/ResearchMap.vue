<template>
  <div class="space-y-2" data-testid="research-map">
    <p v-if="graph && !graph.stats.analyzed" class="text-sm text-base-content/60 py-8 text-center">
      Analyze your sources to draw the map: their reference lists are what connect them.
    </p>

    <div v-else class="relative flex gap-3 h-[calc(100dvh-13rem)] min-h-[420px]">
    <div class="research-flow relative flex-1 min-w-0 rounded-box border border-base-300 bg-base-100 overflow-hidden">
      <VueFlow
        v-if="flowNodes.length"
        :nodes="flowNodes"
        :edges="flowEdges"
        :node-types="nodeTypes"
        :min-zoom="0.15"
        :max-zoom="2"
        :nodes-connectable="false"
        :edges-updatable="false"
        :elevate-edges-on-select="true"
        @nodes-initialized="showReadable"
        @node-click="onNodeClick"
        @node-mouse-enter="({ node }) => (hoveredId = node.id)"
        @node-mouse-leave="hoveredId = null"
        @pane-click="selectedId = null"
      >
        <Background :gap="18" :size="1" pattern-color="var(--color-base-300)" />
        <Controls position="bottom-left" :show-interactive="false" @fit-view="partial = false" />
        <MiniMap
          position="bottom-right"
          :width="160"
          :height="110"
          pannable
          zoomable
          :node-color="miniColor"
          :node-stroke-width="0"
          mask-color="color-mix(in oklch, var(--color-base-content) 8%, transparent)"
        />
      </VueFlow>
      <p v-else-if="graph" class="text-sm text-base-content/60 p-8 text-center">
        Nothing to draw with these settings.
      </p>

      <!-- Toolbar -->
      <div class="absolute top-2 left-2 right-2 sm:right-auto z-10 flex flex-wrap items-center gap-2 rounded-box border border-base-300 bg-base-100/95 px-2 py-1.5 text-xs shadow-sm">
        <label class="input input-bordered input-xs flex items-center gap-1 w-48">
          <Search :size="12" aria-hidden="true" />
          <input v-model="query" type="search" class="grow" placeholder="Find title, author, year" aria-label="Find on the map" @keydown.enter="jumpToMatch" />
        </label>
        <span v-if="query.trim()" class="text-base-content/55 tabular-nums">{{ matches.length }} found</span>
        <label class="flex items-center gap-1 cursor-pointer" title="Works your sources cite that are not among them">
          <input v-model="showExternal" type="checkbox" class="checkbox checkbox-xs" />Cited works
        </label>
        <select v-if="showExternal" v-model.number="minCitations" class="select select-bordered select-xs w-auto" aria-label="Show cited works cited by at least">
          <option v-for="n in [1, 2, 3, 5]" :key="n" :value="n">cited by {{ n }}+</option>
        </select>
        <label v-if="looseCount" class="flex items-center gap-1 cursor-pointer" :title="`${looseCount} sources cite nothing here and are cited by nothing here`">
          <input v-model="hideLoose" type="checkbox" class="checkbox checkbox-xs" />Hide unconnected ({{ looseCount }})
        </label>
        <label v-if="selectedId" class="flex items-center gap-1 cursor-pointer">
          <input v-model="focusOnly" type="checkbox" class="checkbox checkbox-xs" />Only its neighbours
        </label>
        <span class="text-base-content/50 hidden md:inline">
          {{ graph?.stats?.papers || 0 }} sources · {{ graph?.stats?.internal_edges || 0 }} citations between them
        </span>
      </div>

      <p v-if="partial" class="absolute top-[3.4rem] left-2 z-10 rounded bg-base-100/90 px-2 py-0.5 text-[11px] text-base-content/60">
        Showing part of the map at a readable size — drag the overview or press ⛶ to see it all.
      </p>
    </div>

      <!-- Quick view: beside the canvas, never over it, so whatever is
           selected stays in sight. A sheet over the canvas on narrow screens. -->
      <aside
        v-if="selected"
        class="research-quickview absolute inset-x-2 bottom-2 z-20 max-h-[60%] md:static md:inset-auto md:w-80 md:max-h-none md:shrink-0 overflow-y-auto rounded-box border border-base-300 bg-base-100 p-3 space-y-2"
        aria-live="polite"
      >
        <div class="flex items-start gap-2">
          <div class="font-medium leading-snug text-sm flex-1">{{ selected.title || selected.label }}</div>
          <button class="btn btn-ghost btn-xs btn-square" aria-label="Close" @click="selectedId = null"><X :size="14" /></button>
        </div>
        <div v-if="selected.type === 'paper'" class="text-xs text-base-content/60">
          {{ authorsShort(selected.authors) }}<span v-if="selected.year"> · {{ selected.year }}</span>
          · cited by {{ selected.cited_by }} · cites {{ selected.cites }}
        </div>
        <div v-else class="text-xs text-base-content/60">
          {{ selected.in_collection ? 'In your collection, outside the selection' : 'Not in your collection' }}
          · cited by {{ selected.cited_by }} of these sources<span v-if="!selected.title"> · title as cited</span>
        </div>
        <template v-if="selected.abstract">
          <p class="text-sm leading-relaxed">{{ selected.abstract }}</p>
          <p class="text-[11px] text-base-content/45">{{ abstractLabel(selected.abstract_source) }}</p>
        </template>
        <p v-else-if="selected.as_cited" class="text-xs text-base-content/70 italic">{{ selected.as_cited }}</p>
        <p v-else class="text-xs text-base-content/50">No abstract found.</p>
        <div class="flex flex-wrap gap-2">
          <button v-if="selected.type === 'paper' || selected.in_collection" class="btn btn-xs btn-outline"
                  @click="$emit('open', selected.document_id || selected.id)">Open</button>
          <button
            v-else-if="canDownload && selected.downloadable"
            class="btn btn-xs btn-outline"
            :disabled="downloading"
            @click="downloadExternal"
          >Download into collection</button>
          <button class="btn btn-xs btn-ghost" @click="focusOnly = !focusOnly">{{ focusOnly ? 'Show all' : 'Only its neighbours' }}</button>
        </div>
      </aside>
    </div>
    <p class="text-[11px] text-base-content/45">
      Arrows point from a source to the works it cites, drawn only where a reference matches by DOI, arXiv id or full
      title. Scroll to zoom, drag to pan, and use the overview in the corner to jump around.
    </p>
  </div>
</template>

<script setup>
import { computed, markRaw, nextTick, ref, watch } from 'vue'
import { VueFlow, MarkerType, useVueFlow } from '@vue-flow/core'
import { Background } from '@vue-flow/background'
import { Controls } from '@vue-flow/controls'
import { MiniMap } from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import { Search, X } from 'lucide-vue-next'
import ResearchMapNode from './ResearchMapNode.vue'
import { layoutCitations, neighborhood, matchesQuery, NODE_SIZE } from '../utils/citationLayout.js'
import { researchApi, authorsShort, abstractLabel } from '../utils/researchApi.js'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentIds: { type: Array, default: null },
  canDownload: { type: Boolean, default: false },
  refreshKey: { type: Number, default: 0 },
})
defineEmits(['open'])

const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()
const { fitView, setCenter, getViewport } = useVueFlow()

// Below this zoom the cards' text is too small to read on a laptop. A map
// that would only fit smaller starts at this zoom on its best-connected
// source instead, with the overview minimap for the rest.
const READABLE_ZOOM = 0.7
const partial = ref(false)
const nodeTypes = { source: markRaw(ResearchMapNode), external: markRaw(ResearchMapNode) }

const graph = ref(null)
const showExternal = ref(true)
const minCitations = ref(2)
const hideLoose = ref(true)
const focusOnly = ref(false)
const selectedId = ref(null)
const hoveredId = ref(null)
const query = ref('')
const downloading = ref(false)
const positions = ref({})

async function load() {
  try {
    graph.value = await researchApi.graph(props.collectionId, {
      includeExternal: showExternal.value, minCitations: minCitations.value, documentIds: props.documentIds,
    })
  } catch (err) {
    ui.toastError(err, 'Could not load the citation map')
  }
}
watch(() => [props.collectionId, props.refreshKey, showExternal.value, minCitations.value,
  (props.documentIds || []).join(',')], load, { immediate: true })

const linkedIds = computed(() => {
  const ids = new Set()
  for (const e of graph.value?.edges || []) { ids.add(e.source); ids.add(e.target) }
  return ids
})
const looseCount = computed(() => (graph.value?.nodes || []).filter(n => !linkedIds.value.has(n.id)).length)
const nodeById = computed(() => Object.fromEntries((graph.value?.nodes || []).map(n => [n.id, n])))
const selected = computed(() => nodeById.value[selectedId.value] || null)
const focus = computed(() => selectedId.value ? neighborhood(selectedId.value, graph.value?.edges || []) : null)
const matches = computed(() => (graph.value?.nodes || []).filter(n => matchesQuery(n, query.value)).map(n => n.id))

const visibleNodes = computed(() => {
  let list = graph.value?.nodes || []
  if (hideLoose.value && linkedIds.value.size) list = list.filter(n => linkedIds.value.has(n.id))
  if (focusOnly.value && focus.value) list = list.filter(n => focus.value.has(n.id))
  return list
})
const visibleEdges = computed(() => {
  const ids = new Set(visibleNodes.value.map(n => n.id))
  return (graph.value?.edges || []).filter(e => ids.has(e.source) && ids.has(e.target))
})

let flowReady = false

// Lay out only when the set of drawn nodes changes, so a selection or a
// search does not reshuffle the map under the reader.
watch(() => [visibleNodes.value.map(n => n.id).join('|'), visibleEdges.value.length], async () => {
  positions.value = layoutCitations(visibleNodes.value.map(n => ({ ...n, type: n.type === 'external' ? 'external' : 'source' })),
    visibleEdges.value)
  // The first draw is framed by @nodes-initialized, once Vue Flow has
  // measured the cards; later redraws (a filter, a refresh) here.
  if (flowReady) {
    await nextTick()
    await showReadable()
  }
}, { immediate: true })

async function showReadable() {
  flowReady = true
  await fitView({ padding: 0.12 })
  partial.value = false
  if (getViewport().zoom >= READABLE_ZOOM || focusOnly.value) return
  const hub = [...visibleNodes.value].sort((a, b) =>
    ((b.cited_by || 0) + (b.cites || 0)) - ((a.cited_by || 0) + (a.cites || 0)))[0]
  const pos = hub && positions.value[hub.id]
  if (!pos) return
  partial.value = true
  await setCenter(pos.x + NODE_SIZE.source.width / 2, pos.y + NODE_SIZE.source.height / 2,
    { zoom: READABLE_ZOOM })
}

const flowNodes = computed(() => visibleNodes.value.map(n => {
  const type = n.type === 'external' ? 'external' : 'source'
  const size = NODE_SIZE[type]
  const dimmed = Boolean(focus.value && !focus.value.has(n.id)) ||
    Boolean(query.value.trim() && !matches.value.includes(n.id))
  return {
    id: n.id,
    type,
    position: positions.value[n.id] || { x: 0, y: 0 },
    selected: n.id === selectedId.value,
    draggable: true,
    data: { ...n, ...size, dimmed, match: matches.value.includes(n.id) },
  }
}))
const flowEdges = computed(() => visibleEdges.value.map((e, i) => {
  // Hovering a card lights its citations without changing the selection.
  const anchor = hoveredId.value || selectedId.value
  const lit = Boolean(anchor) && (e.source === anchor || e.target === anchor)
  const faded = Boolean(anchor) && !lit
  return {
    id: `e${i}-${e.source}-${e.target}`,
    source: e.source,
    target: e.target,
    type: 'default',
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
    class: ['research-edge', e.external ? 'is-external' : '', lit ? 'is-lit' : '', faded ? 'is-faded' : ''].join(' '),
    label: undefined,
    data: { reason: e.reason },
  }
}))

function onNodeClick({ node }) {
  selectedId.value = node.id
}

function jumpToMatch() {
  const id = matches.value[0]
  const pos = id && positions.value[id]
  if (!pos) return
  selectedId.value = id
  setCenter(pos.x + 110, pos.y + 30, { zoom: 1.1, duration: 300 })
}

watch(focusOnly, () => nextTick(() => { partial.value = false; fitView({ padding: 0.15, duration: 250, maxZoom: 1.2 }) }))
// The quick view takes width from the canvas; re-fit when focused so the
// selection's neighbourhood stays fully in view.
watch(() => Boolean(selectedId.value), () => nextTick(() => {
  if (focusOnly.value) fitView({ padding: 0.15, duration: 200, maxZoom: 1.2 })
}))
watch(selectedId, (id) => { if (!id) focusOnly.value = false })

function miniColor(node) {
  return node.type === 'external' ? 'var(--color-base-300)' : 'var(--color-primary)'
}

async function downloadExternal() {
  downloading.value = true
  try {
    const out = await researchApi.download(props.collectionId, { reference_ids: selected.value.reference_ids })
    if (out.job) jobsStore.addUploadJob(out.job)
    ui.notify(out.queued?.length ? 'Downloading into this collection' : (out.skipped?.[0]?.reason || 'No open copy found'),
      out.queued?.length ? 'success' : 'info')
  } catch (err) {
    ui.toastError(err, 'Download failed')
  } finally {
    downloading.value = false
  }
}
</script>

<style>
/* Vue Flow ships fixed light colours; follow the active daisyUI theme. */
.research-flow .vue-flow__edge-path { stroke: color-mix(in oklch, var(--color-base-content) 22%, transparent); stroke-width: 1; }
.research-flow .research-edge.is-external .vue-flow__edge-path { stroke-dasharray: 4 3; }
.research-flow .research-edge.is-lit .vue-flow__edge-path { stroke: var(--color-primary); stroke-width: 2; }
.research-flow .research-edge.is-faded { opacity: 0.12; }
.research-flow .research-edge.is-lit { z-index: 1; }
.research-flow .vue-flow__arrowhead polyline { stroke: color-mix(in oklch, var(--color-base-content) 45%, transparent); fill: color-mix(in oklch, var(--color-base-content) 45%, transparent); }
.research-flow .vue-flow__node { padding: 0; border: 0; background: transparent; box-shadow: none; }
.research-flow .research-handle { opacity: 0; width: 1px; height: 1px; min-width: 0; min-height: 0; border: 0; }
.research-flow .vue-flow__controls { box-shadow: none; border: 1px solid var(--color-base-300); border-radius: 0.5rem; overflow: hidden; }
.research-flow .vue-flow__controls-button { background: var(--color-base-100); border-bottom-color: var(--color-base-300); fill: var(--color-base-content); color: var(--color-base-content); }
.research-flow .vue-flow__controls-button:hover { background: var(--color-base-200); }
.research-flow .vue-flow__minimap { background: var(--color-base-200); border: 1px solid var(--color-base-300); border-radius: 0.5rem; }
</style>
