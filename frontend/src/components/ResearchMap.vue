<template>
  <div class="space-y-2" data-testid="research-map">
    <p v-if="graph && !graph.stats.analyzed" class="text-sm text-base-content/60 py-8 text-center">
      Analyze your sources to draw the graph: their reference lists are what connect them.
    </p>

    <div
      v-else
      ref="frame"
      class="research-graph relative rounded-box border border-base-300 bg-base-100 overflow-hidden h-[calc(100dvh-13rem)] min-h-[420px] select-none"
    >
      <div ref="host" class="absolute inset-0" data-testid="research-graph-canvas" />

      <p v-if="graph && !shown.nodes.length" class="absolute inset-0 grid place-items-center text-sm text-base-content/55 pointer-events-none">
        Nothing to show with these filters.
      </p>

      <!-- Local graph: the selection's neighbourhood, like Obsidian's local graph -->
      <div v-if="localOf" class="absolute top-3 left-3 z-10 flex items-center gap-2 rounded-full border border-base-300 bg-base-100/95 pl-3 pr-1 py-1 text-xs shadow-sm">
        <Crosshair :size="13" class="text-primary shrink-0" aria-hidden="true" />
        <span class="max-w-[14rem] truncate">Local graph · {{ shortLabel(nodeById[localOf] || {}, 40) }}</span>
        <label class="flex items-center gap-1 text-base-content/60">
          depth
          <input v-model.number="settings.localDepth" type="range" class="range range-xs w-16" v-bind="SLIDERS.localDepth" aria-label="Local graph depth" />
          <span class="tabular-nums w-2">{{ settings.localDepth }}</span>
        </label>
        <button class="btn btn-ghost btn-xs btn-circle" aria-label="Back to the whole graph" @click="localOf = null"><X :size="13" /></button>
      </div>

      <!-- Settings drawer (Filters · Groups · Display · Forces) -->
      <div class="absolute top-3 right-3 z-20 flex flex-col items-end gap-2">
        <button
          v-if="!settingsOpen"
          class="btn btn-sm btn-square border-base-300 bg-base-100/95 shadow-sm"
          aria-label="Graph settings"
          title="Graph settings"
          @click="settingsOpen = true"
        ><Settings2 :size="16" /></button>
        <div
          v-else
          class="graph-settings w-64 max-h-[calc(100dvh-16rem)] overflow-y-auto rounded-box border border-base-300 bg-base-100/95 shadow-lg backdrop-blur text-xs"
          data-testid="graph-settings"
        >
          <div class="flex items-center justify-end gap-1 px-2 pt-2">
            <button class="btn btn-ghost btn-xs btn-square" title="Restore default settings" aria-label="Restore default settings" @click="restoreDefaults">
              <RotateCcw :size="13" />
            </button>
            <button class="btn btn-ghost btn-xs btn-square" aria-label="Close settings" @click="settingsOpen = false"><X :size="14" /></button>
          </div>

          <section v-for="section in sections" :key="section.id" class="border-b border-base-300/60 last:border-b-0">
            <button class="flex w-full items-center gap-1 px-3 py-2 font-medium text-base-content/80 hover:text-base-content" :aria-expanded="open[section.id]" @click="open[section.id] = !open[section.id]">
              <ChevronRight :size="13" class="transition-transform" :class="open[section.id] ? 'rotate-90' : ''" aria-hidden="true" />
              {{ section.label }}
            </button>
            <div v-show="open[section.id]" class="space-y-2.5 px-3 pb-3">
              <template v-if="section.id === 'filters'">
                <label class="input input-bordered input-xs flex items-center gap-1.5 w-full">
                  <Search :size="12" class="text-base-content/50" aria-hidden="true" />
                  <input v-model="settings.search" type="search" class="grow" placeholder="Search title, author, year" aria-label="Search the graph" @keydown.enter="centerOnMatch" />
                </label>
                <p v-if="settings.search.trim()" class="text-base-content/50 -mt-1">{{ matchIds.size }} match{{ matchIds.size === 1 ? '' : 'es' }} · Enter to jump</p>
                <label class="setting-row" title="Works your sources cite that are not in this collection"><span>Cited works you don't have</span><input v-model="settings.showCited" type="checkbox" class="toggle toggle-xs" /></label>
                <div v-if="settings.showCited" class="setting-slider">
                  <span>Cited by at least <b class="tabular-nums">{{ settings.minCitations }}</b></span>
                  <input v-model.number="settings.minCitations" type="range" class="range range-xs" v-bind="SLIDERS.minCitations" aria-label="Cited by at least" />
                </div>
                <label class="setting-row" title="Sources that cite nothing here and are cited by nothing here"><span>Unconnected sources</span><input v-model="settings.showOrphans" type="checkbox" class="toggle toggle-xs" /></label>
              </template>

              <template v-else-if="section.id === 'groups'">
                <div class="join w-full">
                  <button v-for="opt in colorOptions" :key="opt.id" class="btn btn-xs join-item flex-1" :class="settings.colorBy === opt.id ? 'btn-active' : ''" @click="settings.colorBy = opt.id">{{ opt.label }}</button>
                </div>
                <template v-if="settings.colorBy === 'relevance'">
                  <select v-model="relevanceQuestion" class="select select-bordered select-xs w-full" aria-label="Question the sources were screened against">
                    <option v-if="!questions.length" value="">No screening yet — use Screen in the Library</option>
                    <option v-for="q in questions" :key="q" :value="q">{{ q }}</option>
                  </select>
                  <ul class="space-y-1">
                    <li v-for="item in RELEVANCE_LEGEND" :key="item.group" class="flex items-center gap-2 text-base-content/70">
                      <span class="legend-dot" :class="`legend-${item.group}`" aria-hidden="true" />{{ item.label }}
                    </li>
                  </ul>
                </template>
                <ul v-else-if="settings.colorBy === 'type'" class="space-y-1">
                  <li v-for="item in TYPE_LEGEND" :key="item.group" class="flex items-center gap-2 text-base-content/70">
                    <span class="legend-dot" :class="`legend-${item.group}`" aria-hidden="true" />{{ item.label }}
                  </li>
                </ul>
                <div v-else-if="settings.colorBy === 'year' && years" class="space-y-1 text-base-content/70">
                  <div class="h-2 rounded-full legend-years" aria-hidden="true" />
                  <div class="flex justify-between tabular-nums"><span>{{ years.min }}</span><span>{{ years.max }}</span></div>
                </div>
              </template>

              <template v-else-if="section.id === 'display'">
                <label class="setting-row"><span>Arrows</span><input v-model="settings.arrows" type="checkbox" class="toggle toggle-xs" /></label>
                <div v-for="key in ['textFade', 'nodeSize', 'linkThickness']" :key="key" class="setting-slider">
                  <span>{{ sliderLabels[key] }}</span>
                  <input v-model.number="settings[key]" type="range" class="range range-xs" v-bind="SLIDERS[key]" :aria-label="sliderLabels[key]" />
                </div>
              </template>

              <template v-else-if="section.id === 'forces'">
                <div v-for="key in ['centerForce', 'repelForce', 'linkForce', 'linkDistance']" :key="key" class="setting-slider">
                  <span>{{ sliderLabels[key] }}</span>
                  <input v-model.number="settings[key]" type="range" class="range range-xs" v-bind="SLIDERS[key]" :aria-label="sliderLabels[key]" />
                </div>
              </template>
            </div>
          </section>
        </div>
      </div>

      <!-- Quick view of the selected node -->
      <aside
        v-if="selected"
        class="absolute z-10 bottom-3 left-3 w-80 max-w-[calc(100%-1.5rem)] max-h-[55%] overflow-y-auto rounded-box border border-base-300 bg-base-100/95 p-3 space-y-2 shadow-lg backdrop-blur"
        aria-live="polite"
        data-testid="graph-quickview"
      >
        <div class="flex items-start gap-2">
          <div class="font-medium leading-snug text-sm flex-1">{{ selected.title || selected.label }}</div>
          <button class="btn btn-ghost btn-xs btn-square" aria-label="Close" @click="selectedId = null"><X :size="14" /></button>
        </div>
        <div v-if="selected.type === 'paper'" class="text-xs text-base-content/60">
          {{ authorsShort(selected.authors) }}<span v-if="selected.year"> · {{ selected.year }}</span>
          <span v-if="selected.kind === 'book'"> · book</span>
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
          <button class="btn btn-xs btn-ghost" @click="localOf = localOf === selected.id ? null : selected.id">
            <Crosshair :size="12" aria-hidden="true" />{{ localOf === selected.id ? 'Whole graph' : 'Local graph' }}
          </button>
        </div>
      </aside>

      <!-- Zoom -->
      <div class="absolute bottom-3 right-3 z-10 join join-vertical shadow-sm">
        <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom in" @click="zoomBy(1.4)"><Plus :size="13" /></button>
        <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom out" @click="zoomBy(1 / 1.4)"><Minus :size="13" /></button>
        <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom to fit" title="Zoom to fit" @click="fit"><Maximize :size="12" /></button>
      </div>
      <p class="absolute bottom-3 left-1/2 -translate-x-1/2 z-0 text-[11px] text-base-content/40 pointer-events-none hidden md:block" :class="selected ? 'md:hidden' : ''">
        {{ shown.nodes.length }} nodes · {{ shown.edges.length }} links · scroll to zoom, drag to move, double-click to open
      </p>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, shallowRef, watch } from 'vue'
import ForceGraph from 'force-graph'
import { forceCollide, forceX, forceY } from 'd3-force-3d'
import { ChevronRight, Crosshair, Maximize, Minus, Plus, RotateCcw, Search, Settings2, X } from 'lucide-vue-next'
import {
  DEFAULT_SETTINGS, RELEVANCE_LEGEND, SLIDERS, TYPE_LEGEND, groupOf, labelAlpha, loadSettings, matchesQuery, neighborIds,
  nodeRadius, saveSettings, shortLabel, visibleGraph, yearRange, yearStrength,
} from '../utils/citationGraph.js'
import { researchApi, authorsShort, abstractLabel } from '../utils/researchApi.js'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentIds: { type: Array, default: null },
  canDownload: { type: Boolean, default: false },
  refreshKey: { type: Number, default: 0 },
  // The Library's current question, the default for color by relevance.
  question: { type: String, default: '' },
})
const emit = defineEmits(['open'])

const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()

const sections = [
  { id: 'filters', label: 'Filters' },
  { id: 'groups', label: 'Color' },
  { id: 'display', label: 'Display' },
  { id: 'forces', label: 'Layout' },
]
const open = reactive({ filters: true, groups: false, display: false, forces: false })
const colorOptions = [
  { id: 'type', label: 'Type' }, { id: 'relevance', label: 'Relevance' }, { id: 'year', label: 'Year' }, { id: 'none', label: 'None' },
]
const sliderLabels = {
  textFade: 'Titles appear when zoomed in to', nodeSize: 'Dot size', linkThickness: 'Line thickness',
  centerForce: 'Center force', repelForce: 'Repel force', linkForce: 'Link force', linkDistance: 'Link distance',
}

const settings = reactive(loadSettings())
const settingsOpen = ref(false)
const graph = shallowRef(null)
const selectedId = ref(null)
const hoveredId = ref(null)
const localOf = ref(null)
const downloading = ref(false)
const frame = ref(null)
const host = ref(null)

// ── data ────────────────────────────────────────────────────────────────

async function load() {
  try {
    graph.value = await researchApi.graph(props.collectionId, {
      includeExternal: settings.showCited, minCitations: settings.minCitations, documentIds: props.documentIds,
    })
  } catch (err) {
    ui.toastError(err, 'Could not load the graph')
  }
}
watch(() => [props.collectionId, props.refreshKey, settings.showCited, settings.minCitations,
  (props.documentIds || []).join(',')], load, { immediate: true })

// Screening verdicts for one question, from Research → Screen.
const questions = ref([])
const relevanceQuestion = ref('')
const verdicts = shallowRef({})
async function loadVerdicts() {
  try {
    const data = await researchApi.readingList(props.collectionId, relevanceQuestion.value || null)
    questions.value = data.questions || []
    if (!relevanceQuestion.value && questions.value.length) {
      relevanceQuestion.value = questions.value.includes(props.question) ? props.question : questions.value[0]
      return   // the watcher below loads that question's verdicts
    }
    verdicts.value = relevanceQuestion.value
      ? Object.fromEntries((data.items || []).map(i => [i.document_id, i])) : {}
  } catch { verdicts.value = {} }
}
watch(() => [props.collectionId, props.refreshKey, relevanceQuestion.value, settings.colorBy], () => {
  if (settings.colorBy === 'relevance') loadVerdicts()
}, { immediate: true })
watch(verdicts, () => redraw())

const nodeById = computed(() => Object.fromEntries((graph.value?.nodes || []).map(n => [n.id, n])))
const selected = computed(() => nodeById.value[selectedId.value] || null)
const shown = computed(() => visibleGraph(graph.value, settings, localOf.value))
const years = computed(() => yearRange(shown.value.nodes))
const matchIds = computed(() => new Set(shown.value.nodes.filter(n => matchesQuery(n, settings.search)).map(n => n.id)))
const focus = computed(() => {
  const id = hoveredId.value || selectedId.value
  return id ? neighborIds(id, shown.value.edges) : null
})
watch(selectedId, (id) => { if (!id && localOf.value) localOf.value = null })

// ── colours, read from the active theme ─────────────────────────────────

const colors = reactive({ content: '#888', primary: '#55f', secondary: '#c4c', info: '#3ad', base: '#fff', warning: '#fb0', success: '#2b6' })

function readColors() {
  const style = getComputedStyle(frame.value || document.documentElement)
  for (const [key, cssVar] of Object.entries({
    content: '--color-base-content', primary: '--color-primary', secondary: '--color-secondary',
    info: '--color-info', base: '--color-base-100', warning: '--color-warning', success: '--color-success',
  })) {
    const value = style.getPropertyValue(cssVar).trim()
    if (value) colors[key] = value
  }
}

function withAlpha(color, alpha) {
  const c = String(color).trim()
  if (/^(oklch|oklab|lab|lch|rgb|hsl|hwb)\(/.test(c) && !c.includes('/')) return c.replace(/\)$/, ` / ${alpha})`)
  if (/^#[0-9a-f]{6}$/i.test(c)) {
    const n = parseInt(c.slice(1), 16)
    return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
  }
  return c
}

function fillFor(node) {
  const group = groupOf(node, settings.colorBy, verdicts.value)
  if (group === 'relevant') return colors.success
  if (group === 'partial') return colors.info
  if (group === 'not_established') return colors.warning
  if (group === 'not_relevant') return withAlpha(colors.content, 0.25)
  if (group === 'unscreened') return withAlpha(colors.content, 0.5)
  if (group === 'year') return withAlpha(colors.primary, yearStrength(node.year, years.value))
  if (group === 'book') return colors.secondary
  if (group === 'outside') return colors.info
  if (group === 'cited') return withAlpha(colors.content, 0.35)
  return withAlpha(colors.content, 0.72)
}

// ── the force graph ─────────────────────────────────────────────────────

// Label size in graph units: about 7 px on screen at zoom 1.6, where the
// default text fade makes labels fully visible.
const LABEL_SIZE = 4.4

let fg = null
let resizeObserver = null
let themeObserver = null
let fitPending = true
let lastClick = { id: null, at: 0 }
const nodeCache = new Map()   // keeps positions when the drawn set changes

function drawNode(node, ctx, scale) {
  const degree = shown.value.degree[node.id] || 0
  const r = nodeRadius(node, degree, settings.nodeSize)
  const isFocus = !focus.value || focus.value.has(node.id)
  const searching = Boolean(settings.search.trim())
  const isMatch = matchIds.value.has(node.id)
  const dim = !isFocus || (searching && !isMatch)
  ctx.globalAlpha = dim ? 0.14 : 1

  ctx.beginPath()
  ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
  if (node.type === 'external' && !node.in_collection) {
    ctx.fillStyle = colors.base
    ctx.fill()
    ctx.lineWidth = 1.1 / scale + 0.4
    ctx.strokeStyle = withAlpha(colors.content, 0.55)
    ctx.stroke()
  } else {
    ctx.fillStyle = node.id === hoveredId.value || node.id === selectedId.value ? colors.primary : fillFor(node)
    ctx.fill()
  }
  if (node.id === selectedId.value || (searching && isMatch)) {
    ctx.beginPath()
    ctx.arc(node.x, node.y, r + 2.2 / scale + 0.6, 0, 2 * Math.PI)
    ctx.lineWidth = 1.6 / scale
    ctx.strokeStyle = node.id === selectedId.value ? colors.primary : colors.warning
    ctx.stroke()
  }

  // Labels fade in with zoom; the hovered node and its neighbours always speak.
  const emphasised = focus.value && focus.value.has(node.id)
  const alpha = emphasised ? 1 : labelAlpha(scale, settings.textFade)
  if (alpha > 0.02 && !dim) {
    // Text belongs to the graph and scales with it, as in Obsidian; the
    // hovered node and its neighbours never shrink below a readable size.
    const size = emphasised ? Math.max(LABEL_SIZE, 11 / scale) : LABEL_SIZE
    ctx.globalAlpha = alpha * (emphasised || !focus.value ? 1 : 0.5)
    ctx.font = `${node.id === hoveredId.value ? 600 : 400} ${size}px "Inter Variable", Inter, system-ui, sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    ctx.fillStyle = colors.content
    ctx.fillText(shortLabel(node, emphasised ? 80 : 40), node.x, node.y + r + 3 / scale)
  }
  ctx.globalAlpha = 1
}

function paintPointerArea(node, color, ctx) {
  const r = nodeRadius(node, shown.value.degree[node.id] || 0, settings.nodeSize)
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(node.x, node.y, r + 3, 0, 2 * Math.PI)
  ctx.fill()
}

function linkLit(link) {
  const anchor = hoveredId.value || selectedId.value
  const s = link.source?.id ?? link.source
  const t = link.target?.id ?? link.target
  return Boolean(anchor) && (s === anchor || t === anchor)
}
function linkColor(link) {
  if (linkLit(link)) return withAlpha(colors.primary, 0.9)
  const faded = hoveredId.value || selectedId.value
  return withAlpha(colors.content, faded ? 0.06 : 0.2)
}
const linkWidth = (link) => settings.linkThickness * (linkLit(link) ? 1.8 : 0.9)

function applyForces() {
  if (!fg) return
  fg.d3Force('center', null)
  fg.d3Force('x', forceX(0).strength(settings.centerForce))
  fg.d3Force('y', forceY(0).strength(settings.centerForce))
  fg.d3Force('charge').strength(-settings.repelForce).distanceMax(600)
  fg.d3Force('link').distance(settings.linkDistance).strength(settings.linkForce)
  // Nodes never sit on top of each other.
  fg.d3Force('collide', forceCollide(node =>
    nodeRadius(node, shown.value.degree[node.id] || 0, settings.nodeSize) + 2))
  fg.d3ReheatSimulation()
}

function redraw() {
  if (!fg) return
  fg.nodeCanvasObject(drawNode)
  fg.linkColor(linkColor).linkWidth(linkWidth)
    .linkDirectionalArrowLength(settings.arrows ? 3.5 * settings.linkThickness + 1 : 0)
    .linkDirectionalArrowColor(linkColor)
}

function setData() {
  if (!fg) return
  const nodes = shown.value.nodes.map(n => {
    const prev = nodeCache.get(n.id)
    const node = prev ? Object.assign(prev, n) : { ...n }
    nodeCache.set(n.id, node)
    return node
  })
  const links = shown.value.edges.map(e => ({ source: e.source, target: e.target, external: e.external, reason: e.reason }))
  fg.graphData({ nodes, links })
}

function onNodeClick(node) {
  const now = Date.now()
  if (lastClick.id === node.id && now - lastClick.at < 350) {
    // Double-click opens the source, as in Obsidian.
    if (node.type === 'paper' || node.in_collection) emit('open', node.document_id || node.id)
  }
  lastClick = { id: node.id, at: now }
  selectedId.value = node.id
}

function init() {
  if (fg || !host.value) return
  readColors()
  fg = ForceGraph()(host.value)
    .backgroundColor('rgba(0,0,0,0)')
    .nodeId('id')
    .nodeLabel(() => '')
    .nodeCanvasObjectMode(() => 'replace')
    .nodeCanvasObject(drawNode)
    .nodePointerAreaPaint(paintPointerArea)
    .linkColor(linkColor)
    .linkWidth(linkWidth)
    .linkLineDash(link => (link.external ? [2, 2] : null))
    .linkDirectionalArrowRelPos(1)
    .linkDirectionalArrowLength(settings.arrows ? 4 : 0)
    .linkDirectionalArrowColor(linkColor)
    .d3VelocityDecay(0.32)
    .cooldownTime(4000)
    .minZoom(0.1)
    .maxZoom(8)
    .onNodeHover(node => {
      hoveredId.value = node?.id ?? null
      host.value.style.cursor = node ? 'pointer' : ''
    })
    .onNodeClick(onNodeClick)
    .onBackgroundClick(() => { selectedId.value = null })
    .onEngineStop(() => {
      if (fitPending) { fitPending = false; fit() }
    })
  const size = () => {
    const rect = host.value?.getBoundingClientRect()
    if (rect && fg) fg.width(rect.width).height(rect.height)
  }
  resizeObserver = new ResizeObserver(size)
  resizeObserver.observe(host.value)
  size()
  themeObserver = new MutationObserver(() => { readColors(); redraw() })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
  applyForces()
  setData()
}

watch(host, (el) => { if (el) nextTick(init) })
watch(shown, () => { setData(); if (fg) { fitPending = true; fg.d3ReheatSimulation() } })
watch(() => [settings.centerForce, settings.repelForce, settings.linkForce, settings.linkDistance, settings.nodeSize], applyForces)
watch(() => [hoveredId.value, selectedId.value, settings.search, settings.colorBy, settings.arrows,
  settings.textFade, settings.nodeSize, settings.linkThickness], redraw)
watch(settings, () => saveSettings(settings), { deep: true })
watch(localOf, () => nextTick(() => fit()))

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  if (fg) { fg.pauseAnimation(); fg._destructor?.() }
  fg = null
})

// ── controls ────────────────────────────────────────────────────────────

function fit() { fg?.zoomToFit(400, 60) }
function zoomBy(factor) { if (fg) fg.zoom(fg.zoom() * factor, 250) }

function centerOnMatch() {
  const id = [...matchIds.value][0]
  const node = id && nodeCache.get(id)
  if (!node || !fg) return
  selectedId.value = id
  fg.centerAt(node.x, node.y, 400)
  fg.zoom(Math.max(fg.zoom(), 2.2), 400)
}

function restoreDefaults() {
  Object.assign(settings, { ...DEFAULT_SETTINGS })
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

<style scoped>
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; cursor: pointer; color: color-mix(in oklch, var(--color-base-content) 80%, transparent); }
.setting-slider { display: flex; flex-direction: column; gap: 0.3rem; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
.legend-dot { width: 0.6rem; height: 0.6rem; border-radius: 9999px; flex-shrink: 0; }
.legend-article { background: color-mix(in oklch, var(--color-base-content) 72%, transparent); }
.legend-book { background: var(--color-secondary); }
.legend-outside { background: var(--color-info); }
.legend-cited { border: 1.5px solid color-mix(in oklch, var(--color-base-content) 55%, transparent); }
.legend-relevant { background: var(--color-success); }
.legend-partial { background: var(--color-info); }
.legend-not_established { background: var(--color-warning); }
.legend-not_relevant { background: color-mix(in oklch, var(--color-base-content) 25%, transparent); }
.legend-unscreened { background: color-mix(in oklch, var(--color-base-content) 50%, transparent); }
.legend-years { background: linear-gradient(to right, color-mix(in oklch, var(--color-primary) 30%, transparent), var(--color-primary)); }
</style>
