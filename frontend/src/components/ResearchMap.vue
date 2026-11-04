<template>
  <div class="space-y-2" data-testid="research-map">
    <p v-if="graph && !graph.stats.analyzed" class="text-sm text-base-content/60 py-8 text-center">
      Analyze your sources to draw the graph: their reference lists are what connect them.
    </p>

    <div
      v-else
      ref="frame"
      class="research-graph @container relative flex rounded-box border border-base-300 bg-base-100 overflow-hidden h-[calc(100dvh-22.25rem)] md:h-[calc(100dvh-18.25rem)] min-h-[420px] select-none"
    >
      <!-- Canvas area: every overlay is positioned inside it, never against the viewport -->
      <div class="relative flex-1 min-w-0">
        <div ref="host" class="absolute inset-0" data-testid="research-graph-canvas" />

        <p v-if="graph && !shown.nodes.length" class="absolute inset-0 grid place-items-center text-sm text-base-content/55 pointer-events-none">
          Nothing to show with these filters.
        </p>

        <!-- Toolbar: search, settings, local graph -->
        <div class="absolute top-3 left-3 right-3 z-20 flex flex-wrap items-start gap-2 pointer-events-none">
          <label class="input input-sm flex items-center gap-1.5 w-60 max-w-full bg-base-100/95 shadow-sm pointer-events-auto">
            <Search :size="13" class="text-base-content/50 shrink-0" aria-hidden="true" />
            <input
              v-model="settings.search"
              type="search"
              class="grow min-w-0"
              placeholder="Find a title, author or year"
              aria-label="Search the graph"
              @keydown.enter.prevent="nextMatch($event.shiftKey ? -1 : 1)"
              @keydown.esc="settings.search = ''"
            />
            <span v-if="settings.search.trim()" class="text-[11px] text-base-content/50 tabular-nums shrink-0" aria-live="polite">
              {{ matchList.length ? `${matchCursor + 1}/${matchList.length}` : 'none' }}
            </span>
          </label>
          <button
            class="btn btn-sm btn-square border-base-300 bg-base-100/95 shadow-sm pointer-events-auto"
            :class="settingsOpen ? 'btn-active' : ''"
            aria-label="Graph settings"
            title="Graph settings"
            :aria-expanded="settingsOpen"
            @click="settingsOpen = !settingsOpen"
          ><Settings2 :size="15" /></button>
          <div v-if="localOf" class="flex items-center gap-2 rounded-full border border-base-300 bg-base-100/95 pl-3 pr-1 py-1 text-xs shadow-sm pointer-events-auto max-w-full">
            <Crosshair :size="13" class="text-primary shrink-0" aria-hidden="true" />
            <span class="max-w-[12rem] truncate" :title="nodeTitle(nodeById[localOf])">Around {{ shortLabel(nodeById[localOf] || {}, 40) }}</span>
            <label class="flex items-center gap-1 text-base-content/60">
              depth
              <input v-model.number="settings.localDepth" type="range" class="range range-xs w-14" v-bind="SLIDERS.localDepth" aria-label="Local graph depth" />
              <span class="tabular-nums w-2">{{ settings.localDepth }}</span>
            </label>
            <button class="btn btn-ghost btn-xs btn-circle" aria-label="Back to the whole graph" @click="localOf = null"><X :size="13" /></button>
          </div>
          <span class="flex-1" />
          <button
            v-if="!panelVisible"
            class="btn btn-sm border-base-300 bg-base-100/95 shadow-sm pointer-events-auto"
            title="Show the map overview"
            @click="settings.showPanel = true"
          ><PanelRightOpen :size="15" aria-hidden="true" />Overview</button>
        </div>

        <!-- Settings drawer (Filters · Color · Display · Layout) -->
        <div
          v-if="settingsOpen"
          class="graph-settings absolute top-14 left-3 z-20 w-64 max-w-[calc(100%-1.5rem)] max-h-[calc(100%-4.5rem)] overflow-y-auto rounded-box border border-base-300 bg-base-100/95 shadow-lg backdrop-blur text-xs"
          data-testid="graph-settings"
        >
          <div class="flex items-center justify-between gap-1 px-3 pt-2">
            <span class="font-medium text-base-content/70">Graph settings</span>
            <span class="flex gap-0.5">
              <button class="btn btn-ghost btn-xs btn-square" title="Restore default settings" aria-label="Restore default settings" @click="restoreDefaults">
                <RotateCcw :size="13" />
              </button>
              <button class="btn btn-ghost btn-xs btn-square" aria-label="Close settings" @click="settingsOpen = false"><X :size="14" /></button>
            </span>
          </div>

          <section v-for="section in sections" :key="section.id" class="border-b border-base-300/60 last:border-b-0">
            <button class="flex w-full items-center gap-1 px-3 py-2 font-medium text-base-content/80 hover:text-base-content" :aria-expanded="open[section.id]" @click="open[section.id] = !open[section.id]">
              <ChevronRight :size="13" class="transition-transform" :class="open[section.id] ? 'rotate-90' : ''" aria-hidden="true" />
              {{ section.label }}
            </button>
            <div v-show="open[section.id]" class="space-y-2.5 px-3 pb-3">
              <template v-if="section.id === 'filters'">
                <label class="setting-row" title="Works your sources cite that are not in this collection"><span>Cited works you don't have</span><input v-model="settings.showCited" type="checkbox" class="toggle toggle-xs" /></label>
                <div v-if="settings.showCited" class="setting-slider">
                  <span>Cited by at least <b class="tabular-nums">{{ settings.minCitations }}</b> of your sources</span>
                  <input v-model.number="settings.minCitations" type="range" class="range range-xs" v-bind="SLIDERS.minCitations" aria-label="Cited by at least" />
                </div>
                <label class="setting-row" title="Sources that cite nothing here and are cited by nothing here"><span>Unconnected sources</span><input v-model="settings.showOrphans" type="checkbox" class="toggle toggle-xs" /></label>
              </template>

              <template v-else-if="section.id === 'groups'">
                <div class="flex flex-wrap gap-1">
                  <button v-for="opt in colorOptions" :key="opt.id" class="btn btn-xs" :class="settings.colorBy === opt.id ? 'btn-active' : ''" :aria-pressed="settings.colorBy === opt.id" @click="settings.colorBy = opt.id">{{ opt.label }}</button>
                </div>
                <select v-if="settings.colorBy === 'relevance'" v-model="relevanceQuestion" class="select select-bordered select-xs w-full" aria-label="Question the sources were screened against">
                  <option v-if="!questions.length" value="">No screening yet — use Screen in the Library</option>
                  <option v-for="q in questions" :key="q" :value="q">{{ q }}</option>
                </select>
                <p class="text-base-content/50">The legend is in the overview panel.</p>
              </template>

              <template v-else-if="section.id === 'display'">
                <label class="setting-row"><span>Arrows</span><input v-model="settings.arrows" type="checkbox" class="toggle toggle-xs" /></label>
                <div class="setting-slider">
                  <span>Size by</span>
                  <div class="flex flex-wrap gap-1">
                    <button v-for="opt in SIZE_OPTIONS" :key="opt.id" class="btn btn-xs" :class="settings.sizeBy === opt.id ? 'btn-active' : ''" :aria-pressed="settings.sizeBy === opt.id" @click="settings.sizeBy = opt.id">{{ opt.label }}</button>
                  </div>
                </div>
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

        <!-- Zoom -->
        <div class="absolute bottom-3 right-3 z-10 join join-vertical shadow-sm">
          <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom in" @click="zoomBy(1.4)"><Plus :size="13" /></button>
          <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom out" @click="zoomBy(1 / 1.4)"><Minus :size="13" /></button>
          <button class="btn btn-xs btn-square join-item bg-base-100/95" aria-label="Zoom to fit" title="Zoom to fit" @click="fit"><Maximize :size="12" /></button>
        </div>
        <p class="absolute bottom-3 left-3 right-14 z-0 truncate text-[11px] text-base-content/40 pointer-events-none">
          {{ shown.nodes.length }} nodes · {{ shown.edges.length }} links · click a dot for details, double-click to open
        </p>
      </div>

      <!-- Side panel: the open source, the selected node, or the map overview.
           Docked beside the canvas when the map is wide enough; over it when not. -->
      <aside
        v-if="panelVisible"
        class="side-panel absolute inset-y-0 right-0 z-30 flex w-full max-w-[23rem] flex-col border-l border-base-300 bg-base-100 shadow-xl @3xl:static @3xl:shadow-none @3xl:shrink-0 select-text"
        :aria-label="showDetails ? 'Source details' : selected ? 'Selected node' : 'Map overview'"
        data-testid="graph-panel"
      >
        <slot v-if="showDetails" name="details" />

        <!-- Selected node -->
        <template v-else-if="selected">
          <header class="flex items-start gap-2 px-4 pt-4 pb-3 border-b border-base-300">
            <div class="min-w-0 flex-1">
              <p class="text-[11px] uppercase tracking-wide text-base-content/50">{{ kindLabel(selected) }}</p>
              <h3 class="font-semibold leading-snug text-sm mt-0.5 wrap-anywhere" data-testid="graph-quickview-title">
                {{ nodeTitle(selected) }}
              </h3>
              <p v-if="metaLine(selected)" class="text-xs text-base-content/60 mt-1 wrap-anywhere">{{ metaLine(selected) }}</p>
            </div>
            <button class="btn btn-ghost btn-xs btn-square" aria-label="Clear selection" @click="selectedId = null"><X :size="15" /></button>
          </header>
          <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-3 space-y-4 text-sm wrap-anywhere" aria-live="polite" data-testid="graph-quickview">
            <div class="flex flex-wrap gap-1.5 text-xs">
              <span class="badge badge-sm badge-ghost tabular-nums">Cited by {{ selectedLinks.citedBy.length }} here</span>
              <span v-if="selected.type === 'paper'" class="badge badge-sm badge-ghost tabular-nums">Cites {{ selectedLinks.cites.length }} here</span>
              <span v-if="verdicts[selected.id]" class="badge badge-sm" :class="VERDICTS[verdicts[selected.id].verdict]?.tone">
                {{ VERDICTS[verdicts[selected.id].verdict]?.label }}
              </span>
            </div>

            <div class="flex flex-wrap gap-2">
              <button v-if="selected.type === 'paper' || selected.in_collection" class="btn btn-sm btn-primary" @click="openNode(selected)">
                <BookOpen :size="14" aria-hidden="true" />Details
              </button>
              <button
                v-else-if="canDownload && selected.downloadable"
                class="btn btn-sm btn-outline"
                :disabled="downloading"
                @click="downloadExternal(selected)"
              ><span v-if="downloading" class="loading loading-spinner loading-xs" /><Download v-else :size="14" aria-hidden="true" />Add to collection</button>
              <button class="btn btn-sm btn-ghost" :title="localOf === selected.id ? 'Back to the whole graph' : 'Show only what this connects to'" @click="localOf = localOf === selected.id ? null : selected.id">
                <Crosshair :size="14" aria-hidden="true" />{{ localOf === selected.id ? 'Whole graph' : 'Focus' }}
              </button>
            </div>

            <section v-if="selected.abstract">
              <h4 class="panel-heading">{{ selected.abstract_source?.kind === 'opening' ? 'Opening' : 'Abstract' }}</h4>
              <p class="leading-relaxed whitespace-pre-line" :class="abstractOpen ? '' : 'line-clamp-6'">{{ selected.abstract }}</p>
              <button v-if="selected.abstract.length > 420" class="btn btn-ghost btn-xs px-0 mt-1" @click="abstractOpen = !abstractOpen">
                {{ abstractOpen ? 'Show less' : 'Show more' }}
              </button>
              <p class="text-[11px] text-base-content/45 mt-1">{{ abstractLabel(selected.abstract_source) }}</p>
            </section>
            <section v-else-if="selected.as_cited">
              <h4 class="panel-heading">As cited</h4>
              <p class="text-xs leading-relaxed text-base-content/75 italic">{{ selected.as_cited }}</p>
            </section>
            <p v-else class="text-xs text-base-content/50">No abstract found.</p>

            <section v-for="group in linkGroups" :key="group.key">
              <h4 class="panel-heading">{{ group.title }} <span class="font-normal tabular-nums">({{ group.items.length }})</span></h4>
              <ul class="space-y-0.5">
                <li v-for="item in group.items.slice(0, group.limit)" :key="item.node.id">
                  <button class="node-row" :title="nodeTitle(item.node)" @click="focusNode(item.node.id)">
                    <span class="legend-dot mt-1" :class="`legend-${groupOf(item.node, settings.colorBy, verdicts, communityOf)}`" aria-hidden="true" />
                    <span class="min-w-0 flex-1 line-clamp-2" :class="isAsCited(item.node) ? 'italic text-base-content/70' : ''">{{ shortLabel(item.node, 120) }}</span>
                  </button>
                </li>
              </ul>
              <button v-if="group.items.length > group.limit" class="btn btn-ghost btn-xs px-0" @click="expanded[group.key] = true">
                Show all {{ group.items.length }}
              </button>
              <p v-if="!group.items.length" class="text-xs text-base-content/50">{{ group.empty }}</p>
            </section>
          </div>
        </template>

        <!-- Map overview: what the map says at a glance -->
        <template v-else>
          <header class="flex items-start gap-2 px-4 pt-4 pb-3 border-b border-base-300">
            <div class="min-w-0 flex-1">
              <h3 class="font-semibold text-sm">Map overview</h3>
              <p class="text-xs text-base-content/60 mt-1">
                {{ insights.papers }} source{{ insights.papers === 1 ? '' : 's' }} · {{ insights.links }} citation link{{ insights.links === 1 ? '' : 's' }}
                <span v-if="settings.showCited"> · {{ insights.missingTotal }} cited work{{ insights.missingTotal === 1 ? '' : 's' }} you don't have</span>
              </p>
            </div>
            <button class="btn btn-ghost btn-xs btn-square" aria-label="Hide the overview" title="Hide the overview" @click="settings.showPanel = false"><PanelRightClose :size="15" /></button>
          </header>
          <div class="flex-1 min-h-0 overflow-y-auto overflow-x-hidden px-4 py-3 space-y-5 text-sm wrap-anywhere" data-testid="graph-overview">
            <div v-if="graph && !insights.links" class="rounded-box bg-base-200/70 p-3 text-xs space-y-2">
              <p>None of these sources cite each other, and no cited work reaches the current threshold.</p>
              <button v-if="settings.showCited && settings.minCitations > 1" class="btn btn-xs" @click="settings.minCitations = 1">
                Show works cited by just one source
              </button>
              <button v-else-if="!settings.showCited" class="btn btn-xs" @click="settings.showCited = true">Show cited works</button>
            </div>

            <ResearchNetworkPanel
              v-if="networks.citation"
              section="papers"
              :citation="networks.citation"
              :node-by-id="nodeById"
              @focus="focusNode"
              @open="emit('open', $event)"
            />

            <section v-if="insights.missing.length">
              <h4 class="panel-heading">Cited most, not in your collection</h4>
              <p class="text-xs text-base-content/55 mb-1.5">What your sources lean on that you haven't added — usually the next thing to read.</p>
              <ul class="space-y-0.5">
                <li v-for="item in insights.missing" :key="item.node.id" class="flex items-start gap-1">
                  <button class="node-row" :title="nodeTitle(item.node)" @click="focusNode(item.node.id)">
                    <span class="count-pill" :title="`Cited by ${item.count} of your sources`">{{ item.count }}</span>
                    <span class="min-w-0 flex-1 line-clamp-2" :class="isAsCited(item.node) ? 'italic text-base-content/70' : ''">{{ shortLabel(item.node, 120) }}</span>
                  </button>
                  <button
                    v-if="canDownload && item.node.downloadable"
                    class="btn btn-ghost btn-xs btn-square mt-0.5"
                    :disabled="downloading"
                    :aria-label="`Add ${shortLabel(item.node, 60)} to the collection`"
                    title="Add to collection"
                    @click="downloadExternal(item.node)"
                  ><Download :size="13" /></button>
                </li>
              </ul>
            </section>

            <section v-if="insights.hubs.length">
              <h4 class="panel-heading">Most cited in your collection</h4>
              <ul class="space-y-0.5">
                <li v-for="item in insights.hubs" :key="item.node.id">
                  <button class="node-row" :title="nodeTitle(item.node)" @click="focusNode(item.node.id)">
                    <span class="count-pill" :title="`Cited by ${item.count} of your sources`">{{ item.count }}</span>
                    <span class="min-w-0 flex-1 line-clamp-2">{{ shortLabel(item.node, 120) }}</span>
                  </button>
                </li>
              </ul>
            </section>

            <section v-if="insights.outside.length">
              <h4 class="panel-heading">In the collection, outside the selection</h4>
              <ul class="space-y-0.5">
                <li v-for="item in insights.outside" :key="item.node.id">
                  <button class="node-row" :title="nodeTitle(item.node)" @click="focusNode(item.node.id)">
                    <span class="count-pill">{{ item.count }}</span>
                    <span class="min-w-0 flex-1 line-clamp-2">{{ shortLabel(item.node, 120) }}</span>
                  </button>
                </li>
              </ul>
            </section>

            <ResearchNetworkPanel
              v-if="networks.authors?.key_nodes?.length"
              section="people"
              :authors="networks.authors"
              @focus="focusNode"
              @open="emit('open', $event)"
            />
            <ResearchNetworkPanel
              v-if="networks.entities?.key_nodes?.length"
              section="concepts"
              :entities="networks.entities"
              @focus="focusNode"
              @open="emit('open', $event)"
            />

            <p v-if="insights.orphans" class="text-xs text-base-content/60">
              {{ insights.orphans }} source{{ insights.orphans === 1 ? '' : 's' }} connect{{ insights.orphans === 1 ? 's' : '' }} to nothing here.
              <button class="link link-hover" @click="settings.showOrphans = !settings.showOrphans">{{ settings.showOrphans ? 'Hide' : 'Show' }} {{ insights.orphans === 1 ? 'it' : 'them' }}</button>
            </p>

            <section>
              <h4 class="panel-heading">Colour</h4>
              <template v-if="settings.colorBy === 'community' && communityRows.length">
                <ul class="space-y-1 text-xs text-base-content/70">
                  <li v-for="item in communityRows" :key="item.group" class="flex items-start gap-2">
                    <span class="legend-dot mt-1" :class="`legend-${item.group}`" aria-hidden="true" />
                    <span class="min-w-0 flex-1 wrap-anywhere">{{ item.label }} <span class="tabular-nums text-base-content/50">· {{ item.size }}</span></span>
                  </li>
                </ul>
                <p v-if="networks.citation?.definitions?.community" class="text-xs text-base-content/55 mt-1.5">{{ networks.citation.definitions.community }}</p>
              </template>
              <ul v-else-if="legend.length" class="space-y-1 text-xs text-base-content/70">
                <li v-for="item in legend" :key="item.group" class="flex items-center gap-2">
                  <span class="legend-dot" :class="`legend-${item.group}`" aria-hidden="true" />{{ item.label }}
                </li>
              </ul>
              <div v-else-if="settings.colorBy === 'year' && years" class="space-y-1 text-xs text-base-content/70">
                <div class="h-2 rounded-full legend-years" aria-hidden="true" />
                <div class="flex justify-between tabular-nums"><span>{{ years.min }}</span><span>{{ years.max }}</span></div>
              </div>
              <p v-else-if="settings.colorBy === 'community'" class="text-xs text-base-content/55">Groups are not available for this map.</p>
              <p v-else class="text-xs text-base-content/55">All dots share one colour.</p>
              <p v-if="settings.colorBy === 'relevance' && relevanceQuestion" class="text-xs text-base-content/55 mt-1">Screened for “{{ relevanceQuestion }}”</p>
              <p class="text-xs text-base-content/50 mt-2">{{ sizeCaption }} Italic titles are references as printed, not verified titles.</p>
            </section>
          </div>
        </template>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, useSlots, watch } from 'vue'
import ForceGraph from 'force-graph'
import { forceCollide, forceX, forceY } from 'd3-force-3d'
import {
  BookOpen, ChevronRight, Crosshair, Download, Maximize, Minus, PanelRightClose, PanelRightOpen, Plus, RotateCcw,
  Search, Settings2, X,
} from 'lucide-vue-next'
import {
  DEFAULT_SETTINGS, RELEVANCE_LEGEND, SIZE_OPTIONS, SLIDERS, TYPE_LEGEND, communityLegend, connections,
  groupOf, isAsCited, labelAlpha, labelRank, loadSettings, mapInsights, matchesQuery, metricIndex, metricRadius,
  neighborIds, nodeRadius, placeLabels, saveSettings, shortLabel, visibleGraph, wrapLabel, yearRange, yearStrength,
} from '../utils/citationGraph.js'
import ResearchNetworkPanel from './ResearchNetworkPanel.vue'
import { researchApi, authorsShort, abstractLabel, VERDICTS } from '../utils/researchApi.js'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  documentIds: { type: Array, default: null },
  canDownload: { type: Boolean, default: false },
  refreshKey: { type: Number, default: 0 },
  // The Library's current question, the default for color by relevance.
  question: { type: String, default: '' },
  // The source whose details the parent shows in this map's side panel
  // (through the #details slot), or null.
  detailsId: { type: String, default: null },
})
const emit = defineEmits(['open', 'close-details'])
const slots = useSlots()

const ui = useUiStore()
const jobsStore = useBackgroundJobsStore()

const sections = [
  { id: 'filters', label: 'Filters' },
  { id: 'groups', label: 'Color' },
  { id: 'display', label: 'Display' },
  { id: 'forces', label: 'Layout' },
]
const open = reactive({ filters: true, groups: true, display: false, forces: false })
const colorOptions = [
  { id: 'type', label: 'Type' }, { id: 'relevance', label: 'Relevance' }, { id: 'year', label: 'Year' },
  { id: 'community', label: 'Community' }, { id: 'none', label: 'None' },
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
const abstractOpen = ref(false)
const expanded = reactive({})
const frame = ref(null)
const host = ref(null)

// ── data ────────────────────────────────────────────────────────────────

// The network measures (key papers, people, concepts, communities). They are
// extra: if a call fails the map still works and the panel simply has no data.
const NO_NETWORKS = { citation: null, authors: null, entities: null }
const networks = shallowRef(NO_NETWORKS)
let graphSeq = 0
let networkSeq = 0
let networkScope = ''

async function loadGraph(args) {
  const seq = ++graphSeq
  try {
    const data = await researchApi.graph(props.collectionId, args)
    if (seq === graphSeq) graph.value = data
  } catch (err) {
    if (seq === graphSeq) ui.toastError(err, 'Could not load the graph')
  }
}

// Separate from the graph, because the analysis can take far longer than the
// map: the map never waits for it. Results of another collection or selection
// are cleared at once rather than shown under this one.
async function loadNetworks(args) {
  const seq = ++networkSeq
  const scope = `${props.collectionId}|${(props.documentIds || []).join(',')}`
  if (scope !== networkScope) {
    networkScope = scope
    networks.value = NO_NETWORKS
  }
  const results = await Promise.allSettled(['citation', 'authors', 'entities'].map(
    kind => researchApi.network(props.collectionId, { ...args, graph: kind })))
  if (seq !== networkSeq) return   // a newer request has superseded this one
  const [citation, authors, entities] = results.map(r => (r.status === 'fulfilled' ? r.value : null))
  networks.value = { citation, authors, entities }
}

function load() {
  const args = { includeExternal: settings.showCited, minCitations: settings.minCitations, documentIds: props.documentIds }
  loadGraph(args)
  loadNetworks(args)
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

const metrics = computed(() => metricIndex(networks.value.citation))
const communityOf = computed(() => Object.fromEntries(
  Object.values(metrics.value.byId).filter(r => r.community !== null && r.community !== undefined).map(r => [r.id, r.community])))
const communityRows = computed(() => communityLegend(networks.value.citation))
const sizeDefinition = computed(() => networks.value.citation?.definitions?.[settings.sizeBy] || '')
const sizeCaption = computed(() => settings.sizeBy === 'citations' || !sizeDefinition.value
  ? 'Bigger dots have more links.' : `Dot size: ${sizeDefinition.value}`)

// Radius of a dot: links by default, or a network measure when chosen.
function radiusOf(node) {
  const size = settings.nodeSize
  if (settings.sizeBy === 'citations') return nodeRadius(node, shown.value.degree[node.id] || 0, size)
  const row = metrics.value.byId[node.id]
  return metricRadius(node, row?.[settings.sizeBy], metrics.value.max[settings.sizeBy], size)
}

const nodeById = computed(() => Object.fromEntries((graph.value?.nodes || []).map(n => [n.id, n])))
const selected = computed(() => nodeById.value[selectedId.value] || null)
const shown = computed(() => visibleGraph(graph.value, settings, localOf.value))
const years = computed(() => yearRange(shown.value.nodes))
const insights = computed(() => mapInsights(shown.value))
const matchList = computed(() => settings.search.trim()
  ? shown.value.nodes.filter(n => matchesQuery(n, settings.search))
    .sort((a, b) => (shown.value.degree[b.id] || 0) - (shown.value.degree[a.id] || 0))
  : [])
const matchIds = computed(() => new Set(matchList.value.map(n => n.id)))
const matchCursor = ref(0)
watch(() => settings.search, () => { matchCursor.value = 0 })
const focus = computed(() => {
  const id = hoveredId.value || selectedId.value
  return id ? neighborIds(id, shown.value.edges) : null
})
watch(selectedId, (id) => {
  abstractOpen.value = false
  for (const key of Object.keys(expanded)) delete expanded[key]
  if (id) settings.showPanel = true
  if (!id && localOf.value) localOf.value = null
})

const showDetails = computed(() => Boolean(props.detailsId && slots.details))
const panelVisible = computed(() => showDetails.value || Boolean(selected.value) || settings.showPanel)

// The source open in the details panel is the one lit on the map.
watch(() => props.detailsId, (id) => {
  if (!id) return
  const node = nodeById.value[id] || (graph.value?.nodes || []).find(n => n.document_id === id)
  if (node) selectedId.value = node.id
})

const selectedLinks = computed(() => selected.value ? connections(selected.value.id, shown.value) : { cites: [], citedBy: [] })
const linkGroups = computed(() => {
  if (!selected.value) return []
  const groups = [{ key: 'citedBy', title: 'Cited by, in this map', items: selectedLinks.value.citedBy, empty: 'Nothing drawn here cites it.' }]
  if (selected.value.type === 'paper') {
    groups.unshift({ key: 'cites', title: 'Cites, in this map', items: selectedLinks.value.cites,
      empty: settings.showCited ? 'None of its references are drawn here.' : 'Turn on cited works in Filters to see its references.' })
  }
  return groups.map(g => ({ ...g, limit: expanded[g.key] ? Infinity : 8 }))
})

const legend = computed(() => {
  if (settings.colorBy === 'relevance') return RELEVANCE_LEGEND
  if (settings.colorBy === 'type') return TYPE_LEGEND
  return []
})

function nodeTitle(node) {
  if (!node) return ''
  return node.title || node.label || node.filename || ''
}
function kindLabel(node) {
  if (node.type === 'paper') return node.kind === 'book' ? 'Book in your collection' : 'Source in your collection'
  if (node.in_collection) return 'In your collection, outside the selection'
  return isAsCited(node) ? 'Cited work · reference as printed' : 'Cited work · not in your collection'
}
function metaLine(node) {
  return [authorsShort(node.authors), node.year, node.venue].filter(Boolean).join(' · ')
}

// ── colours, read from the active theme ─────────────────────────────────

const colors = reactive({
  content: '#888', primary: '#55f', secondary: '#c4c', info: '#3ad', base: '#fff', warning: '#fb0', success: '#2b6',
  accent: '#1bb', error: '#e43', neutral: '#445',
})
// Community slots 0-7 take these theme colours in order.
const COMMUNITY_COLORS = ['primary', 'secondary', 'accent', 'info', 'success', 'warning', 'error', 'neutral']

function readColors() {
  const style = getComputedStyle(frame.value || document.documentElement)
  for (const [key, cssVar] of Object.entries({
    content: '--color-base-content', primary: '--color-primary', secondary: '--color-secondary',
    info: '--color-info', base: '--color-base-100', warning: '--color-warning', success: '--color-success',
    accent: '--color-accent', error: '--color-error', neutral: '--color-neutral',
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
  const group = groupOf(node, settings.colorBy, verdicts.value, communityOf.value)
  if (group === 'community-other') return withAlpha(colors.content, 0.25)
  if (group.startsWith('community-')) return colors[COMMUNITY_COLORS[Number(group.slice(10))]]
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

// Labels are drawn in screen space after the frame: a constant, readable
// size at any zoom, wrapped to a fixed width, kept inside the canvas, and
// thinned so they never print on top of each other.
const LABEL_PX = 11
const LABEL_LINE = 13.5
const LABEL_WIDTH = { normal: 150, anchor: 230 }
const FONT_STACK = '"Inter Variable", Inter, system-ui, sans-serif'

let fg = null
let resizeObserver = null
let themeObserver = null
let fitPending = true
let lastClick = { id: null, at: 0 }
const nodeCache = new Map()   // keeps positions when the drawn set changes
const labelCache = new Map()  // wrapped lines per node and style

function labelFont(node, anchor) {
  return `${isAsCited(node) ? 'italic ' : ''}${anchor ? 600 : 400} ${LABEL_PX}px ${FONT_STACK}`
}

function measureLabel(ctx, node, anchor) {
  const text = anchor ? nodeTitle(node) : shortLabel(node, 90)
  const key = `${anchor ? 'a' : 'n'}|${node.id}|${text}`
  let hit = labelCache.get(key)
  if (!hit) {
    ctx.font = labelFont(node, anchor)
    const measure = s => ctx.measureText(s).width
    const lines = wrapLabel(text, anchor ? LABEL_WIDTH.anchor : LABEL_WIDTH.normal, measure, anchor ? 3 : 2)
    hit = { lines, w: Math.ceil(Math.max(0, ...lines.map(measure))), h: lines.length * LABEL_LINE }
    if (labelCache.size > 4000) labelCache.clear()
    labelCache.set(key, hit)
  }
  return hit
}

function isDimmed(node) {
  const searching = Boolean(settings.search.trim())
  return (focus.value && !focus.value.has(node.id)) || (searching && !matchIds.value.has(node.id))
}

function drawNode(node, ctx, scale) {
  const r = radiusOf(node)
  const searching = Boolean(settings.search.trim())
  const isMatch = matchIds.value.has(node.id)
  ctx.globalAlpha = isDimmed(node) ? 0.14 : 1

  ctx.beginPath()
  ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
  if (node.type === 'external' && !node.in_collection && settings.colorBy !== 'community') {
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
  ctx.globalAlpha = 1
}

function drawLabels(ctx, scale) {
  if (!fg) return
  const anchorId = hoveredId.value || selectedId.value
  const fade = labelAlpha(scale, settings.textFade)
  const rankCtx = { anchorId, focusIds: focus.value, matchIds: matchIds.value, degree: shown.value.degree }
  const items = []
  for (const node of fg.graphData().nodes) {
    if (!Number.isFinite(node.x) || isDimmed(node)) continue
    const emphasised = Boolean(focus.value?.has(node.id)) || matchIds.value.has(node.id)
    const alpha = emphasised ? 1 : fade
    if (alpha <= 0.02) continue
    const anchor = node.id === anchorId
    const at = fg.graph2ScreenCoords(node.x, node.y)
    const r = radiusOf(node) * scale
    const m = measureLabel(ctx, node, anchor)
    items.push({ id: node.id, node, x: at.x, y: at.y + r + 3, w: m.w, h: m.h, lines: m.lines, alpha, anchor,
      force: anchor, rank: labelRank(node, rankCtx) })
  }
  items.sort((a, b) => a.rank - b.rank)
  const placed = placeLabels(items, { left: 0, top: 0, right: fg.width(), bottom: fg.height() })

  const dpr = window.devicePixelRatio || 1
  ctx.save()
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.textAlign = 'center'
  ctx.textBaseline = 'top'
  ctx.lineJoin = 'round'
  for (const p of placed) {
    ctx.globalAlpha = p.alpha * (p.anchor || !focus.value || focus.value.has(p.id) ? 1 : 0.6)
    ctx.font = labelFont(p.node, p.anchor)
    const cx = p.rect.left + p.w / 2
    p.lines.forEach((line, i) => {
      const y = p.rect.top + i * LABEL_LINE
      // A halo in the background colour keeps text legible over links.
      ctx.lineWidth = 3
      ctx.strokeStyle = withAlpha(colors.base, 0.9)
      ctx.strokeText(line, cx, y)
      ctx.fillStyle = colors.content
      ctx.fillText(line, cx, y)
    })
  }
  ctx.restore()
}

function paintPointerArea(node, color, ctx) {
  const r = radiusOf(node)
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
  fg.d3Force('collide', forceCollide(node => radiusOf(node) + 2))
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

function openNode(node) {
  if (node.type === 'paper' || node.in_collection) emit('open', node.document_id || node.id)
}

function onNodeClick(node) {
  const now = Date.now()
  const double = lastClick.id === node.id && now - lastClick.at < 350
  lastClick = { id: node.id, at: now }
  selectedId.value = node.id
  // Double-click opens the source, as in Obsidian. With a source already
  // open beside the map, a single click moves the details along with it.
  if (double || props.detailsId) {
    if (node.type === 'paper' || node.in_collection) openNode(node)
    else if (props.detailsId) emit('close-details')
  }
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
    .onRenderFramePost(drawLabels)
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
    .onBackgroundClick(() => { selectedId.value = null; settingsOpen.value = false })
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
  themeObserver = new MutationObserver(() => { readColors(); labelCache.clear(); redraw() })
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'class'] })
  applyForces()
  setData()
}

watch(host, (el) => { if (el) nextTick(init) })
watch(shown, () => { setData(); if (fg) { fitPending = true; fg.d3ReheatSimulation() } })
watch(() => [settings.centerForce, settings.repelForce, settings.linkForce, settings.linkDistance, settings.nodeSize, settings.sizeBy], applyForces)
watch(networks, () => {
  // Dot sizes only move, and the layout only needs reheating, if they depend on it.
  if (settings.sizeBy !== 'citations') applyForces()
  redraw()
})
watch(() => [hoveredId.value, selectedId.value, settings.search, settings.colorBy, settings.arrows,
  settings.textFade, settings.nodeSize, settings.sizeBy, settings.linkThickness, matchCursor.value], redraw)
watch(settings, () => saveSettings(settings), { deep: true })
watch(localOf, () => nextTick(() => fit()))

// Escape steps back: settings, then the selection. Not while a dialog is open.
function onKey(event) {
  if (event.key !== 'Escape' || document.querySelector('dialog[open]')) return
  if (settingsOpen.value) settingsOpen.value = false
  else if (selectedId.value && !props.detailsId) selectedId.value = null
}
onMounted(() => window.addEventListener('keydown', onKey))

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  resizeObserver?.disconnect()
  themeObserver?.disconnect()
  if (fg) { fg.pauseAnimation(); fg._destructor?.() }
  fg = null
})

// ── controls ────────────────────────────────────────────────────────────

function fit() { fg?.zoomToFit(400, 60) }
function zoomBy(factor) { if (fg) fg.zoom(fg.zoom() * factor, 250) }

// Select a node and bring it into view, close enough to read its label.
function focusNode(id) {
  selectedId.value = id
  const node = nodeCache.get(id)
  if (!node || !fg || !Number.isFinite(node.x)) return
  fg.centerAt(node.x, node.y, 400)
  if (fg.zoom() < 1.6) fg.zoom(1.8, 400)
}

function nextMatch(step = 1) {
  const list = matchList.value
  if (!list.length) return
  matchCursor.value = (matchCursor.value + step + list.length) % list.length
  focusNode(list[matchCursor.value].id)
}

function restoreDefaults() {
  Object.assign(settings, { ...DEFAULT_SETTINGS })
}

async function downloadExternal(node) {
  downloading.value = true
  try {
    const out = await researchApi.download(props.collectionId, { reference_ids: node.reference_ids })
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
.wrap-anywhere { overflow-wrap: anywhere; word-break: normal; }
.panel-heading { margin-bottom: 0.375rem; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.025em; color: color-mix(in oklch, var(--color-base-content) 55%, transparent); }
.node-row { display: flex; width: 100%; min-width: 0; align-items: flex-start; gap: 0.5rem; border-radius: 0.375rem; padding: 0.25rem 0.375rem; margin-inline: -0.375rem; text-align: left; font-size: 0.8125rem; line-height: 1.35; }
.node-row:hover, .node-row:focus-visible { background: color-mix(in oklch, var(--color-base-content) 7%, transparent); }
.count-pill { flex-shrink: 0; min-width: 1.5rem; margin-top: 0.05rem; border-radius: 9999px; padding: 0 0.35rem; text-align: center; font-size: 0.6875rem; font-variant-numeric: tabular-nums; background: color-mix(in oklch, var(--color-base-content) 10%, transparent); color: color-mix(in oklch, var(--color-base-content) 75%, transparent); }
.setting-row { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; cursor: pointer; color: color-mix(in oklch, var(--color-base-content) 80%, transparent); }
.setting-slider { display: flex; flex-direction: column; gap: 0.3rem; color: color-mix(in oklch, var(--color-base-content) 70%, transparent); }
.legend-dot { width: 0.6rem; height: 0.6rem; border-radius: 9999px; flex-shrink: 0; }
.legend-article, .legend-node { background: color-mix(in oklch, var(--color-base-content) 72%, transparent); }
.legend-book { background: var(--color-secondary); }
.legend-outside { background: var(--color-info); }
.legend-cited { border: 1.5px solid color-mix(in oklch, var(--color-base-content) 55%, transparent); }
.legend-year { background: var(--color-primary); }
.legend-relevant { background: var(--color-success); }
.legend-partial { background: var(--color-info); }
.legend-not_established { background: var(--color-warning); }
.legend-not_relevant { background: color-mix(in oklch, var(--color-base-content) 25%, transparent); }
.legend-unscreened { background: color-mix(in oklch, var(--color-base-content) 50%, transparent); }
.legend-community-0 { background: var(--color-primary); }
.legend-community-1 { background: var(--color-secondary); }
.legend-community-2 { background: var(--color-accent); }
.legend-community-3 { background: var(--color-info); }
.legend-community-4 { background: var(--color-success); }
.legend-community-5 { background: var(--color-warning); }
.legend-community-6 { background: var(--color-error); }
.legend-community-7 { background: var(--color-neutral); }
.legend-community-other { background: color-mix(in oklch, var(--color-base-content) 25%, transparent); }
.legend-years { background: linear-gradient(to right, color-mix(in oklch, var(--color-primary) 30%, transparent), var(--color-primary)); }
</style>
