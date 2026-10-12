<template>
  <div
    v-if="open"
    class="fixed inset-0 z-[200]"
    @click.self="close"
    role="dialog"
    aria-modal="true"
    :aria-labelledby="titleId"
  >
    <!-- Backdrop -->
    <div class="absolute inset-0 bg-base-content/20" @click="close" aria-hidden="true"></div>

    <!-- Drawer Panel -->
    <div class="absolute right-0 top-0 h-full w-80 max-w-[85vw] bg-base-100 shadow-2xl flex flex-col">
      <!-- Header -->
      <div class="flex items-center justify-between p-4 border-b border-base-300">
        <h3 :id="titleId" class="text-sm font-bold">{{ title }}</h3>
        <button
          class="btn btn-ghost btn-sm btn-circle"
          @click="close"
          :aria-label="`Close ${title.toLowerCase()}`"
        >
          <X :size="18" />
        </button>
      </div>

      <!-- Content -->
      <div class="flex-1 overflow-y-auto p-4 space-y-5">

        <!-- Section 1: which model answers. Read-only: it is chosen once, in
             the top bar, and every surface follows it. -->
        <div v-if="hasAnyProvider" class="space-y-1.5">
          <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">AI model</span>
          <div class="flex items-center gap-2 px-3 py-2 rounded-lg border border-base-300 bg-base-200/60 text-sm">
            <Sparkles :size="13" class="text-primary flex-shrink-0" aria-hidden="true" />
            <span class="flex-1 truncate">
              {{ displayName(providerId) }}<span v-if="modelName" class="text-base-content/55"> · {{ modelName }}</span>
            </span>
            <span class="badge badge-xs badge-outline">{{ isLocalProvider(providerId) ? 'local' : 'cloud' }}</span>
          </div>
          <p class="text-xs text-base-content/50 px-1">Change it from the model picker in the top bar. It applies to Ask, Find and Reports.</p>
        </div>

        <!-- No-provider nudge -->
        <div v-else class="flex items-center gap-3 rounded-lg border border-info/30 bg-info/5 px-3 py-2.5">
          <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" class="stroke-info shrink-0 w-4 h-4" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path>
          </svg>
          <p class="text-sm flex-1">Add an AI provider in Settings to enable AI features.</p>
          <button class="btn btn-xs btn-primary flex-shrink-0" @click="$emit('switch-tab', 'settings')">Settings</button>
        </div>

        <!-- Section 2: Answer depth (presets) -->
        <div class="space-y-2">
          <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Answer depth</span>
          <div class="join w-full" role="group" aria-label="Answer depth preset">
            <button
              v-for="p in PRESETS"
              :key="p.key"
              type="button"
              class="btn btn-xs join-item flex-1"
              :class="activePreset === p.key ? 'btn-primary' : 'btn-ghost border border-base-300'"
              :aria-pressed="activePreset === p.key"
              @click="applyPreset(p.key)"
            >{{ p.label }}</button>
          </div>
          <p v-if="!activePreset" class="text-xs text-base-content/40">Custom — adjust in Advanced below.</p>
        </div>

        <!-- Section 3: Advanced (raw knobs) -->
        <details class="collapse collapse-arrow border border-base-300 bg-base-200/40 rounded-lg">
          <summary class="collapse-title min-h-0 py-2.5 px-3 text-xs font-semibold text-base-content/60 uppercase tracking-wider">
            Advanced
          </summary>
          <div class="collapse-content space-y-3 px-3">

            <label class="flex items-center justify-between">
              <span class="text-sm">{{ topKLabel }}</span>
              <input
                v-model.number="topK"
                type="number"
                :min="topKMin"
                :max="topKMax"
                class="input input-bordered input-xs w-16 text-center"
                :aria-label="topKLabel"
              />
            </label>

            <label class="flex items-center justify-between">
              <span class="text-sm">Search mode</span>
              <select v-model="searchMode" class="select select-bordered select-xs" aria-label="Search mode">
                <option value="semantic">Semantic</option>
                <option value="keyword">Keyword</option>
                <option value="hybrid">Hybrid</option>
              </select>
            </label>

            <label v-if="hasWeight && searchMode === 'hybrid'" class="space-y-1 block">
              <span class="text-sm">Semantic weight: {{ Math.round(semanticWeight * 100) }}%</span>
              <input
                v-model.number="semanticWeight"
                type="range"
                min="0"
                max="1"
                step="0.1"
                class="range range-primary range-xs"
                :aria-label="`Semantic weight: ${Math.round(semanticWeight * 100)} percent`"
              />
              <div class="w-full flex justify-between text-xs px-1 text-base-content/40" aria-hidden="true">
                <span>Keywords</span><span>Balanced</span><span>Semantic</span>
              </div>
            </label>

            <label class="flex items-center justify-between cursor-pointer select-none">
              <span class="text-sm">Rerank</span>
              <input type="checkbox" class="toggle toggle-sm toggle-primary" v-model="rerank" />
            </label>

            <label v-if="hasCacheThreshold" class="space-y-1 block">
              <span class="text-sm flex items-center justify-between gap-2">
                <span>Reuse cached answers</span>
                <span class="text-xs text-base-content/60 tabular-nums">{{ cacheThresholdLabel }}</span>
              </span>
              <input
                v-model.number="cacheThreshold"
                type="range"
                min="0.75"
                max="1"
                step="0.01"
                class="range range-primary range-xs"
                :aria-label="`Reuse cached answers: ${cacheThresholdLabel}`"
              />
              <div class="w-full flex justify-between text-xs px-1 text-base-content/40" aria-hidden="true">
                <span>Similar wording</span><span>Identical only</span>
              </div>
              <p class="text-xs text-base-content/50">
                How close a new question must be to one already answered before the stored
                answer is served without calling the model. Questions that differ in numbers
                or negation are never matched.
              </p>
            </label>

            <label v-if="hasSynthesize" class="flex items-center justify-between cursor-pointer select-none">
              <span class="text-sm">Synthesize</span>
              <input type="checkbox" class="toggle toggle-sm toggle-secondary" v-model="synthesize" />
            </label>

            <!-- Surface-specific extras (e.g. Chat's collection scope) -->
            <slot name="advanced" />
          </div>
        </details>

      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Sparkles, X } from 'lucide-vue-next'
import { useProviderStore } from '../stores/providerStore'
import { getProviderDisplayName, isLocalProvider } from '../utils/aiProviders.js'

const props = defineProps({
  title: { type: String, default: 'AI Settings' },
  /** The provider currently answering ('' when none is configured). */
  providerId: { type: String, default: '' },
  topKLabel: { type: String, default: 'Top K results' },
  topKMin: { type: Number, default: 1 },
  topKMax: { type: Number, default: 20 },
})

defineEmits(['switch-tab'])

// v-model bindings. semanticWeight / synthesize are optional: when the parent
// doesn't bind them the corresponding control is hidden.
const open = defineModel('open', { type: Boolean, default: false })
const topK = defineModel('topK', { type: Number, default: 10 })
const searchMode = defineModel('searchMode', { type: String, default: 'hybrid' })
const semanticWeight = defineModel('semanticWeight', { type: Number, default: undefined })
const rerank = defineModel('rerank', { type: Boolean, default: false })
const synthesize = defineModel('synthesize', { type: Boolean, default: undefined })
// Answer-cache similarity floor (Chat only). Hidden when the parent doesn't
// bind it, or binds null because the deployment has the cache off.
const cacheThreshold = defineModel('cacheThreshold', { type: Number, default: undefined })

const providerStore = useProviderStore()
const titleId = 'ai-settings-title'
const hasAnyProvider = computed(() => !!props.providerId)
const modelName = computed(() => providerStore.activeModel || providerStore.deploymentDefault.model || '')
const hasWeight = computed(() => typeof semanticWeight.value === 'number')
const hasSynthesize = computed(() => typeof synthesize.value === 'boolean')
const hasCacheThreshold = computed(() => typeof cacheThreshold.value === 'number')
const cacheThresholdLabel = computed(() =>
  cacheThreshold.value >= 1 ? 'Identical only' : `${Math.round(cacheThreshold.value * 100)}% similar`
)

const close = () => { open.value = false }

const displayName = getProviderDisplayName

// ── Answer depth presets ───────────────────────────────────────────────────
// Selecting a preset writes the underlying values through the same v-models
// (and therefore the same persistence) as the Advanced knobs.

const PRESETS = [
  { key: 'fast',     label: 'Fast',     values: { topK: 5,  mode: 'semantic', semanticWeight: 0.7, rerank: false } },
  { key: 'balanced', label: 'Balanced', values: { topK: 10, mode: 'hybrid',   semanticWeight: 0.7, rerank: true } },
  { key: 'thorough', label: 'Thorough', values: { topK: 20, mode: 'hybrid',   semanticWeight: 0.6, rerank: true } },
]

const activePreset = computed(() => {
  for (const p of PRESETS) {
    const v = p.values
    if (
      topK.value === v.topK &&
      searchMode.value === v.mode &&
      rerank.value === v.rerank &&
      (!hasWeight.value || Math.abs(semanticWeight.value - v.semanticWeight) < 0.001)
    ) return p.key
  }
  return null
})

const applyPreset = (key) => {
  const v = PRESETS.find(p => p.key === key).values
  topK.value = v.topK
  searchMode.value = v.mode
  rerank.value = v.rerank
  if (hasWeight.value) semanticWeight.value = v.semanticWeight
}
</script>
