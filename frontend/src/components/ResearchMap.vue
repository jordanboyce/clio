<template>
  <div class="flex flex-col xl:flex-row gap-3 min-h-0" data-testid="research-map">
    <div class="flex-1 min-w-0 space-y-2">
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-base-content/60">
        <span>{{ graph?.stats?.papers || 0 }} papers · {{ graph?.stats?.internal_edges || 0 }} citations between them</span>
        <label class="flex items-center gap-1.5 cursor-pointer">
          <input v-model="showExternal" type="checkbox" class="checkbox checkbox-xs" />
          Works your papers cite that you don't have
        </label>
        <label v-if="showExternal" class="flex items-center gap-1.5">
          cited by at least
          <select v-model.number="minCitations" class="select select-bordered select-xs w-14">
            <option v-for="n in [1, 2, 3, 5]" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>
      </div>

      <p v-if="graph && !graph.stats.analyzed" class="text-sm text-base-content/60 py-8 text-center">
        Analyze your papers to draw the map: their reference lists are what connect them.
      </p>

      <div v-else class="rounded-box border border-base-300 bg-base-100 overflow-hidden relative">
        <svg
          ref="svg"
          :viewBox="`${view.x} ${view.y} ${view.w} ${view.h}`"
          class="w-full h-[min(65vh,620px)] touch-none select-none"
          role="img"
          :aria-label="`Citation map of ${graph?.stats?.papers || 0} papers`"
          @wheel.prevent="zoom"
          @pointerdown="startPan"
          @pointermove="move"
          @pointerup="endDrag"
          @pointerleave="endDrag"
        >
          <defs>
            <marker id="cite-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" class="fill-base-content/40" />
            </marker>
          </defs>
          <g>
            <path
              v-for="(edge, i) in drawnEdges"
              :key="i"
              :d="edge.d"
              class="fill-none"
              :class="edge.highlight ? 'stroke-primary' : 'stroke-base-content/25'"
              :stroke-width="edge.highlight ? 1.8 : 1"
              :stroke-dasharray="edge.external ? '3 3' : null"
              marker-end="url(#cite-arrow)"
            >
              <title>{{ edge.title }}</title>
            </path>
          </g>
          <g>
            <g
              v-for="node in drawnNodes"
              :key="node.id"
              :transform="`translate(${node.x},${node.y})`"
              class="cursor-pointer outline-none"
              tabindex="0"
              role="button"
              :aria-label="node.label"
              @pointerdown.stop="startNodeDrag($event, node)"
              @click.stop="select(node)"
              @keydown.enter="select(node)"
              @mouseenter="hovered = node.id"
              @mouseleave="hovered = null"
            >
              <circle
                :r="node.r"
                :class="[
                  node.type === 'external' ? 'fill-base-100 stroke-base-content/50' : (node.analyzed ? 'fill-primary/70 stroke-primary' : 'fill-base-300 stroke-base-content/30'),
                  selectedId === node.id ? 'stroke-[3px]' : 'stroke-1',
                ]"
                :stroke-dasharray="node.type === 'external' ? '2 2' : null"
              />
              <text
                v-if="showLabel(node)"
                :y="node.r + 11"
                text-anchor="middle"
                class="fill-base-content text-[10px] pointer-events-none"
              >{{ shortLabel(node) }}</text>
            </g>
          </g>
        </svg>
        <div class="absolute bottom-2 right-2 join">
          <button class="btn btn-xs join-item" aria-label="Zoom in" @click="zoomBy(0.8)">+</button>
          <button class="btn btn-xs join-item" aria-label="Zoom out" @click="zoomBy(1.25)">−</button>
          <button class="btn btn-xs join-item" @click="resetView">Fit</button>
        </div>
      </div>
      <p class="text-[11px] text-base-content/45">
        An arrow points from a paper to a paper it cites. Edges are drawn only where a reference matches by DOI, arXiv id or full title.
      </p>
    </div>

    <!-- Quick view -->
    <aside class="xl:w-80 shrink-0 space-y-2" aria-live="polite">
      <div v-if="!selected" class="text-sm text-base-content/55 xl:pt-8">
        Select a paper to read its abstract.
      </div>
      <div v-else class="rounded-box border border-base-300 p-3 space-y-2">
        <div class="font-medium leading-snug text-sm">{{ selected.title || selected.label }}</div>
        <div v-if="selected.type === 'paper'" class="text-xs text-base-content/60">
          {{ authorsShort(selected.authors) }}<span v-if="selected.year"> · {{ selected.year }}</span>
          · cited by {{ selected.cited_by }} · cites {{ selected.cites }}
        </div>
        <div v-else class="text-xs text-base-content/60">
          Not in this collection · cited by {{ selected.cited_by }} of your papers
          <span v-if="!selected.title"> · title as cited</span>
        </div>
        <p v-if="selected.abstract" class="text-sm leading-relaxed max-h-72 overflow-y-auto">{{ selected.abstract }}</p>
        <p v-else-if="selected.type === 'paper'" class="text-xs text-base-content/50">No abstract found.</p>
        <p v-else class="text-xs text-base-content/70 italic">{{ selected.as_cited }}</p>
        <p v-if="selected.abstract" class="text-[11px] text-base-content/45">{{ sourceLabel(selected.abstract_source) }}</p>
        <div class="flex gap-2">
          <button v-if="selected.type === 'paper'" class="btn btn-xs btn-outline" @click="$emit('open', selected.id)">Open paper</button>
          <button
            v-else-if="canDownload && selected.downloadable"
            class="btn btn-xs btn-outline"
            :disabled="downloading"
            @click="downloadExternal"
          >Download into collection</button>
        </div>
      </div>
    </aside>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { layoutGraph, nodeRadius, edgePath } from '../utils/citationLayout.js'
import { researchApi, authorsShort, sourceLabel } from '../utils/researchApi.js'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  canDownload: { type: Boolean, default: false },
  refreshKey: { type: Number, default: 0 },
})
defineEmits(['open'])

const W = 1000
const H = 700
const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()
const graph = ref(null)
const positions = ref({})
const showExternal = ref(true)
const minCitations = ref(2)
const selectedId = ref(null)
const hovered = ref(null)
const downloading = ref(false)
const view = reactive({ x: 0, y: 0, w: W, h: H })
const svg = ref(null)
let drag = null

async function load() {
  try {
    graph.value = await researchApi.graph(props.collectionId, {
      includeExternal: showExternal.value, minCitations: minCitations.value,
    })
    positions.value = layoutGraph(graph.value.nodes, graph.value.edges, {
      width: W, height: H, iterations: graph.value.nodes.length > 150 ? 120 : 300,
    })
    resetView()
  } catch (err) {
    ui.toastError(err, 'Could not load the citation map')
  }
}
watch(() => [props.collectionId, props.refreshKey, showExternal.value, minCitations.value], load, { immediate: true })

const nodeById = computed(() => Object.fromEntries((graph.value?.nodes || []).map(n => [n.id, n])))
const selected = computed(() => nodeById.value[selectedId.value] || null)
const focusId = computed(() => hovered.value || selectedId.value)

const drawnNodes = computed(() => (graph.value?.nodes || []).map(n => ({
  ...n, ...(positions.value[n.id] || { x: W / 2, y: H / 2 }), r: nodeRadius(n),
})))
const drawnEdges = computed(() => (graph.value?.edges || []).map(e => {
  const from = positions.value[e.source]
  const to = positions.value[e.target]
  if (!from || !to) return null
  const target = nodeById.value[e.target]
  return {
    d: edgePath(from, to, target ? nodeRadius(target) : 6),
    external: e.external,
    highlight: focusId.value && (e.source === focusId.value || e.target === focusId.value),
    title: `${nodeById.value[e.source]?.label} cites ${target?.label} (${e.reason})`,
  }
}).filter(Boolean))

function shortLabel(node) {
  const text = node.type === 'paper'
    ? [authorsShort(node.authors), node.year].filter(Boolean).join(' ') || node.label
    : node.label
  return text.length > 28 ? `${text.slice(0, 27)}…` : text
}
function showLabel(node) {
  return node.type === 'paper' || node.id === focusId.value || (graph.value?.nodes.length || 0) < 40
}
function select(node) { selectedId.value = node.id }

// ── pan, zoom, drag ──
function toSvgPoint(evt) {
  const rect = svg.value.getBoundingClientRect()
  return { x: view.x + ((evt.clientX - rect.left) / rect.width) * view.w, y: view.y + ((evt.clientY - rect.top) / rect.height) * view.h }
}
function startPan(evt) { drag = { kind: 'pan', start: { x: evt.clientX, y: evt.clientY }, view: { ...view } } }
function startNodeDrag(evt, node) { drag = { kind: 'node', id: node.id } }
function move(evt) {
  if (!drag || !svg.value) return
  if (drag.kind === 'node') {
    const p = toSvgPoint(evt)
    positions.value = { ...positions.value, [drag.id]: p }
  } else {
    const rect = svg.value.getBoundingClientRect()
    view.x = drag.view.x - ((evt.clientX - drag.start.x) / rect.width) * view.w
    view.y = drag.view.y - ((evt.clientY - drag.start.y) / rect.height) * view.h
  }
}
function endDrag() { drag = null }
function zoomBy(factor, center = { x: view.x + view.w / 2, y: view.y + view.h / 2 }) {
  const w = Math.min(W * 3, Math.max(W / 8, view.w * factor))
  const h = w * (view.h / view.w)
  view.x = center.x - (center.x - view.x) * (w / view.w)
  view.y = center.y - (center.y - view.y) * (h / view.h)
  view.w = w
  view.h = h
}
function zoom(evt) { zoomBy(evt.deltaY > 0 ? 1.1 : 0.9, toSvgPoint(evt)) }
// Fit the drawing to the nodes, keeping the element's aspect ratio, so a
// small library is drawn large instead of as a speck in the middle.
function resetView() {
  const pts = Object.values(positions.value)
  if (!pts.length) { Object.assign(view, { x: 0, y: 0, w: W, h: H }); return }
  const pad = 60
  const minX = Math.min(...pts.map(p => p.x)) - pad
  const maxX = Math.max(...pts.map(p => p.x)) + pad
  const minY = Math.min(...pts.map(p => p.y)) - pad
  const maxY = Math.max(...pts.map(p => p.y)) + pad
  const rect = svg.value?.getBoundingClientRect()
  const aspect = rect && rect.width && rect.height ? rect.height / rect.width : H / W
  let w = Math.max(maxX - minX, 240)
  let h = Math.max(maxY - minY, 240 * aspect)
  if (h / w > aspect) w = h / aspect
  else h = w * aspect
  Object.assign(view, { x: (minX + maxX) / 2 - w / 2, y: (minY + maxY) / 2 - h / 2, w, h })
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
