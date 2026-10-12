<template>
  <div class="search-view flex flex-col h-full min-h-0 min-w-0">

    <header class="flex items-center justify-between gap-3 flex-shrink-0 mb-4">
      <div class="min-w-0">
        <h2 class="text-lg font-semibold">Search sources</h2>
        <p class="text-sm text-base-content/75 truncate">{{ collectionStore.currentCollection?.name || 'Current collection' }}</p>
      </div>
      <div class="flex items-center gap-1">
        <button v-if="searchStore.searched" class="btn btn-ghost btn-sm min-h-11" :disabled="loading" @click="startNewSearch"><Plus :size="16" />New search</button>
        <button v-if="cacheStats.count > 0" class="btn btn-ghost btn-sm min-h-11" :disabled="loading" @click="historyModal.open()"><History :size="16" />History</button>
      </div>
    </header>

    <SearchSettings
      v-model:open="searchSettingsOpen"
      :configured-providers="configuredProviderIds"
      :provider-id="selectedProviders[0] || ''"
      :search-mode="searchMode"
      v-model:semantic-weight="semanticWeight"
      v-model:rerank="localRerank"
      v-model:synthesize="localSynthesize"
      @switch-tab="$emit('switch-tab', $event)"
    />

    <div class="flex-shrink-0">
      <div class="relative rounded-xl border border-base-content/25 bg-base-100 focus-within:border-primary focus-within:ring-1 focus-within:ring-primary">
        <SlashCommandPicker ref="slashPickerRef" :show="slashPickerOpen" :model-value="searchStore.query" @select="onSlashSelect" @close="slashPickerOpen = false" />
        <div class="flex items-center gap-2 p-2">
          <label for="search-query" class="sr-only">Search your indexed documents</label>
          <input id="search-query" ref="searchInputRef" v-model="searchStore.query" type="text"
            class="min-w-0 flex-1 bg-transparent border-none outline-none text-base px-2 min-h-11 placeholder:text-base-content/60"
            placeholder="Ask a question or find a passage…" :disabled="loading"
            @input="onInputChange" @keydown="onKeydown" @blur="onInputBlur" />
          <button v-if="!loading" class="btn btn-primary min-h-11" :disabled="searchButtonDisabled" @click="search"><SearchIcon :size="17" /><span>Search</span></button>
          <button v-else class="btn btn-outline min-h-11" @click="cancelSearch"><X :size="17" />Cancel</button>
        </div>
      </div>
      <div class="search-toolbar flex flex-wrap items-center gap-x-2 gap-y-2 mt-3">
        <div class="join" role="group" aria-label="Match passages by">
          <button v-for="option in matchModes" :key="option.value" type="button"
            class="btn btn-sm join-item min-h-11 font-medium"
            :class="searchMode === option.value ? 'btn-primary' : 'btn-outline border-base-content/25'"
            :aria-pressed="searchMode === option.value" :title="option.description" :disabled="loading"
            @click="searchMode = option.value">{{ option.label }}</button>
        </div>

        <label class="option-chip" :class="{ 'is-on': localSynthesize && aiActive, 'is-unavailable': !configuredProviderIds.length }"
          :title="configuredProviderIds.length ? 'Summarize the matching passages with source references' : 'Connect an AI provider in Settings to enable answers'">
          <input v-model="localSynthesize" type="checkbox" class="toggle toggle-sm toggle-primary" :disabled="loading || !configuredProviderIds.length" />
          <span>AI answer</span>
        </label>
        <label class="option-chip" :class="{ 'is-on': localRerank && aiActive, 'is-unavailable': !configuredProviderIds.length }"
          :title="configuredProviderIds.length ? 'Let the AI put the most relevant passages first (slower)' : 'Connect an AI provider in Settings to enable ranking'">
          <input v-model="localRerank" type="checkbox" class="toggle toggle-sm toggle-primary" :disabled="loading || !configuredProviderIds.length" />
          <span>AI ranking</span>
        </label>

        <label class="option-chip">
          <span>Results</span>
          <select v-model.number="searchStore.topK" class="select select-sm min-h-9 h-9 w-auto pr-8 bg-base-100 border-none focus:outline-none" aria-label="Maximum results" :disabled="loading">
            <option v-for="n in resultLimits" :key="n" :value="n">{{ n }}</option>
          </select>
        </label>

        <button type="button" class="btn btn-ghost btn-sm min-h-11 gap-1.5 ml-auto" :disabled="loading" @click="searchSettingsOpen = true" :aria-expanded="searchSettingsOpen">
          <SlidersHorizontal :size="16" aria-hidden="true" />
          <span>{{ providerSummary }}</span>
        </button>
        <button type="button" class="btn btn-ghost btn-sm min-h-11 font-mono" :disabled="loading" @click="toggleSlashPicker" :aria-expanded="slashPickerOpen" title="Slash commands: /tools, /stats, /docs">/</button>
      </div>
      <p v-if="searchDisabled" class="text-sm text-base-content/75 mt-2">No searchable passages yet. Add sources to this collection to get started.</p>
    </div>

    <!-- Loading indicator -->
    <div v-if="loading" role="status" aria-live="polite" class="flex-shrink-0 mt-3 rounded-xl border border-primary/30 bg-primary/5 p-3">
      <div class="flex items-center gap-2">
        <span class="loading loading-spinner loading-sm text-primary"></span>
        <span class="text-sm font-semibold">{{ loadingHeadline }}</span>
        <span class="loading loading-dots loading-xs text-primary"></span>
      </div>
      <p class="text-xs text-base-content/70 mt-1">{{ loadingPhaseMessage }}</p>
      <div class="mt-2 flex flex-wrap gap-2">
        <span class="badge badge-sm badge-outline">{{ searchModeLabel }}</span>
        <span class="badge badge-sm badge-outline">Top {{ searchStore.topK }}</span>
        <span v-if="aiActive" class="badge badge-sm badge-outline badge-primary">
          AI answer
        </span>
      </div>
      <progress class="progress progress-primary w-full mt-2"></progress>
    </div>

    <!-- Error Alert -->
    <div v-if="error" role="alert" class="flex-shrink-0 mt-3 alert alert-error">
      <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
          d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
      <span>{{ error }}</span>
    </div>

    <p v-if="notice" role="status" class="text-sm text-base-content/75 mt-3">{{ notice }}</p>
    <p v-if="providerWarnings" role="status" class="text-sm text-base-content/80 mt-3">{{ providerWarnings }}</p>

    <!-- Results area (scrollable) -->
    <div class="flex-1 overflow-y-auto min-h-0 space-y-5 mt-4 pr-1 scroll-quiet" :aria-busy="loading">

    <!-- Empty state (no search run yet) -->
    <div
      v-if="showEmptyState"
      class="flex flex-col items-center justify-center h-full text-center gap-5 py-8 px-4"
    >
      <div class="flex flex-col items-center gap-2">
        <div class="w-11 h-11 rounded-full bg-base-200 flex items-center justify-center">
          <SearchIcon :size="20" class="text-base-content/50" aria-hidden="true" />
        </div>
        <h3 class="text-lg font-semibold">Find the passage you need</h3>
        <p class="text-sm text-base-content/75 max-w-md leading-relaxed">Search by idea, phrase, or question. Open a matching source to read it in context.</p>
      </div>
      <div class="flex flex-col gap-1.5 w-full max-w-md">
        <button
          v-for="suggestion in searchSuggestions"
          :key="suggestion"
          class="btn btn-ghost min-h-11 h-auto py-3 justify-start text-left font-normal text-base-content border border-base-300"
          @click="useSearchSuggestion(suggestion)"
        >
          <Sparkles :size="13" class="text-base-content/70 flex-shrink-0" aria-hidden="true" />
          <span class="whitespace-normal">{{ suggestion }}</span>
        </button>
      </div>
    </div>

    <!-- Cached-result notice (served instantly from the local search cache) -->
    <div v-if="servedFromCache && !loading" class="flex items-center gap-1.5 text-sm text-base-content/75">
      <span>Saved result &middot; {{ formatTimeAgo(servedFromCache.timestamp) }}</span>
      <span aria-hidden="true">&middot;</span>
      <button class="link link-primary min-h-11" @click="refreshSearch">Refresh</button>
    </div>

    <section v-if="synthesizedAnswers.length" aria-label="Synthesized answers" class="space-y-5">
      <article v-for="(aiResponse, index) in synthesizedAnswers" :key="index" class="rounded-xl border border-base-300 bg-base-200/40 p-4 sm:p-6 min-w-0">
        <header class="flex flex-wrap items-center justify-between gap-3 mb-5">
          <div>
            <h3 class="text-lg font-semibold">Answer</h3>
            <p class="text-sm text-base-content/75 mt-1">{{ getProviderDisplayNameLocal(aiResponse.provider) }} · Based on retrieved passages</p>
          </div>
          <button class="btn btn-ghost btn-sm min-h-11" @click="copyAnswer(aiResponse, index)"><Copy :size="15" />{{ copiedAnswer === index ? 'Copied' : 'Copy answer' }}</button>
        </header>
        <AnswerEvidence :content="aiResponse.synthesis" :sources="aiResponse.results || searchStore.results" />
      </article>
    </section>

    <!-- Slash command output (takes the place of results when a / command was run) -->
    <div
      v-if="slashOutput"
      class="mt-4 rounded-xl border border-base-300 bg-base-200 shadow-sm"
    >
      <div class="flex items-center justify-between px-4 py-2 border-b border-base-300 bg-base-200/60">
        <div class="flex items-center gap-2">
          <code class="font-mono text-xs px-1.5 py-0.5 rounded bg-base-100 border border-base-300">{{ slashOutput.cmd }}</code>
          <span v-if="slashOutput.error" class="badge badge-xs badge-error">error</span>
        </div>
        <button
          class="btn btn-ghost btn-xs btn-circle"
          title="Dismiss"
          aria-label="Dismiss slash command output"
          @click="dismissSlashOutput"
        >
          <X :size="14" />
        </button>
      </div>
      <pre class="p-4 font-mono text-xs leading-snug whitespace-pre-wrap overflow-x-auto">{{ slashOutput.content }}</pre>
    </div>

    <section v-if="searchStore.results.length" aria-label="Matching passages">
      <div class="flex flex-wrap items-baseline justify-between gap-2 border-b border-base-300 pb-3">
        <h3 class="text-base font-semibold">{{ searchStore.results.length }} matching passage{{ searchStore.results.length === 1 ? '' : 's' }}</h3>
        <span class="text-sm text-base-content/75">{{ sourceCount }} source{{ sourceCount === 1 ? '' : 's' }}<span v-if="resultsRankedByAI"> · Ordered by {{ getProviderDisplayNameLocal(resultsRankedByAI) }}</span></span>
        <p class="w-full text-sm text-base-content/75 break-words">Results for “{{ searchStore.lastQuery }}”</p>
      </div>
      <div class="divide-y divide-base-300">
        <SearchResult v-for="(result, index) in searchStore.results" :key="index" :result="result" :index="index" :query="searchStore.lastQuery" />
      </div>
    </section>

    <div v-else-if="searchStore.searched && !loading" class="py-10 text-center">
      <h3 class="font-semibold text-lg">No matching passages</h3>
      <p class="text-sm text-base-content/75 mt-2 max-w-md mx-auto leading-relaxed">Try fewer words or switch to meaning-based search to find related ideas. Check that the sources you need are in this collection.</p>
      <button class="btn btn-outline mt-4 min-h-11" @click="searchSettingsOpen = true">Adjust search settings</button>
    </div>

    </div><!-- end scrollable results area -->

    <!-- Search History Modal -->
    <dialog :ref="historyModal.dialogRef" class="modal" @close="historyModal.onClosed" aria-labelledby="search-history-title">
      <div class="modal-box max-w-3xl">
        <h3 id="search-history-title" class="font-bold text-lg mb-4">Search History</h3>

        <div class="space-y-2 max-h-96 overflow-y-auto">
          <div v-for="(entry, index) in historyEntries" :key="index"
            class="rounded-lg bg-base-200">
            <div class="card-body p-4">
              <div class="flex items-start justify-between gap-4">
                <div class="flex-1">
                  <button class="text-left font-semibold text-sm link link-hover min-h-11" @click="loadHistoryEntry(entry)">{{ entry.query }}</button>
                  <div class="text-xs text-base-content/60 mt-1">
                    {{ formatTimeAgo(entry.timestamp) }}
                    &middot; {{ entry.results?.length || 0 }} results
                    &middot; Top-{{ entry.topK }}
                    <span v-if="entry.aiResponses && entry.aiResponses.length > 0">
                      &middot; {{ entry.aiResponses.length }} AI response(s)
                    </span>
                  </div>
                </div>
                <button
                  class="btn btn-ghost btn-sm min-h-11 min-w-11 text-error"
                  @click.stop="deleteHistoryEntry(entry)"
                  title="Delete from history"
                  :aria-label="`Delete history entry: ${entry.query}`"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" class="w-4 h-4" fill="none" viewBox="0 0 24 24"
                    stroke="currentColor" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                      d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            </div>
          </div>

          <div v-if="historyEntries.length === 0" class="text-center text-base-content/60 py-8">
            No search history yet
          </div>
        </div>

        <div class="modal-action">
          <button class="btn btn-sm btn-error" @click="clearAllHistory" :disabled="historyEntries.length === 0">
            Clear All
          </button>
          <button class="btn btn-sm" @click="historyModal.close()">Close</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import http from '../utils/http'
import { Plus, History, SlidersHorizontal, Sparkles, Copy, X, Search as SearchIcon } from 'lucide-vue-next'
import { answerWithReferences } from '../utils/answerEvidence'
import AnswerEvidence from './AnswerEvidence.vue'
import SearchResult from './SearchResult.vue'
import { storeToRefs } from 'pinia'
import { useSearchStore } from '../stores/searchStore'
import { useCollectionStore } from '../stores/collectionStore'
import SlashCommandPicker from './SlashCommandPicker.vue'
import SearchSettings from './SearchSettings.vue'
import { runSlashCommand, isSlashCommand } from '../utils/slashCommands'
import { useModal } from '../composables/useModal'
import { useProviderStore } from '../stores/providerStore'
import { useStatsStore } from '../stores/statsStore'
import {
  getConfiguredProviderIds,
  buildProviderHeaders,
  getAPIProviderName,
  getProviderDisplayName,
  getAISettings,
  migrateLegacySettings,
  getProviderConfig,
} from '../utils/aiProviders.js'

const emit = defineEmits(['stats-updated', 'switch-tab'])

const providerStore = useProviderStore()
const statsStore = useStatsStore()

// Computed to check if search is available (chunk count previously
// prop-drilled from App; now read from statsStore)
const searchDisabled = computed(() => statsStore.chunks === 0)

const searchButtonDisabled = computed(() => {
  if (loading.value || !searchStore.query.trim()) return true
  // Slash commands bypass the vector index and don't need indexed content.
  if (isSlashCommand(searchStore.query)) return false
  return searchDisabled.value
})

// Use the search store for persistent state
const searchStore = useSearchStore()
const collectionStore = useCollectionStore()

// Local state for loading and error (not persisted)
const loading = ref(false)
const error = ref('')
const notice = ref('')
const providerWarnings = ref('')
const copiedAnswer = ref(null)
const synthesizedAnswers = computed(() => searchStore.aiResponses.filter(response => response.synthesis))
const sourceCount = computed(() => new Set(searchStore.results.map(result => result.document_id || result.filename)).size)
let copyTimer
async function copyAnswer(response, index) {
  try {
    await navigator.clipboard.writeText(answerWithReferences({ content: response.synthesis, sources: response.results || searchStore.results }))
    copiedAnswer.value = index
    clearTimeout(copyTimer)
    copyTimer = setTimeout(() => { copiedAnswer.value = null }, 2000)
  } catch { notice.value = 'Could not copy the answer. Select the text to copy it manually.' }
}

// Slash command picker + inline output (rendered above results)
const slashPickerOpen = ref(false)
const slashPickerRef = ref(null)
const searchInputRef = ref(null)
const slashOutput = ref(null) // { cmd, content, error }

const toggleSlashPicker = () => {
  slashPickerOpen.value = !slashPickerOpen.value
  if (slashPickerOpen.value) {
    nextTick(() => searchInputRef.value?.focus())
  }
}

const onInputChange = () => {
  slashPickerOpen.value = (searchStore.query || '').trimStart().startsWith('/')
}

const onInputBlur = () => {
  setTimeout(() => {
    slashPickerOpen.value = false
  }, 150)
}

const onKeydown = (e) => {
  if (slashPickerOpen.value && slashPickerRef.value?.handleKeydown(e)) return
  if (e.key === 'Enter' && !e.isComposing) {
    e.preventDefault()
    search()
  }
}

const onSlashSelect = (cmd) => {
  searchStore.query = cmd
  slashPickerOpen.value = false
  search()
}

const runInlineSlashCommand = async (input) => {
  const result = await runSlashCommand(input, {
    collectionId: collectionStore.currentCollectionId,
    collection: collectionStore.currentCollection,
  })
  slashOutput.value = result
  // Clear any stale search results so the slash output is the primary view.
  searchStore.clearResults()
  servedFromCache.value = null
}

const dismissSlashOutput = () => {
  slashOutput.value = null
}

// Empty state (no search run yet, nothing else on screen)
const searchSuggestions = [
  'What are the key requirements?',
  'important dates and deadlines',
  'definitions of key terms',
]

const showEmptyState = computed(() =>
  !searchStore.searched && !loading.value && !slashOutput.value && !error.value
)

const useSearchSuggestion = (suggestion) => {
  searchStore.query = suggestion
  nextTick(() => searchInputRef.value?.focus())
}

// Match settings live in the store (persisted) so they survive tab switches.
const { searchMode, semanticWeight } = storeToRefs(searchStore)

const matchModes = [
  { value: 'hybrid', label: 'Meaning + keywords', description: 'Combine related ideas with matching words (recommended)' },
  { value: 'semantic', label: 'Meaning', description: 'Find related ideas, even when the wording differs' },
  { value: 'keyword', label: 'Keywords', description: 'Prioritize matching words, names, and terms' },
]
const searchModeLabel = computed(() => matchModes.find(option => option.value === searchMode.value)?.label || 'Meaning')

// Result-count presets; an unusual saved value stays selectable rather than being silently changed.
const resultLimits = computed(() => {
  const presets = [5, 10, 20, 50]
  const current = Number(searchStore.topK)
  return presets.includes(current) ? presets : [...presets, current].sort((a, b) => a - b)
})

// Which passages list is on screen: the first successful provider's order when
// AI ranking ran, otherwise the plain retrieval order.
const resultsRankedByAI = computed(() => {
  const options = searchStore.lastOptions
  if (!options?.rerank || !options.providers?.length) return null
  return searchStore.aiResponses[0]?.provider || options.providers[0].id
})

// AbortController for cancelling ongoing searches
let abortController = null

// When the current results were served from the local search cache:
// { timestamp } of the cached entry (null after a fresh search).
const servedFromCache = ref(null)

// History modal state
// Native <dialog> (focus trap, Escape, focus restore)
const historyModal = useModal()

// Settings drawer state
const searchSettingsOpen = ref(false)

// Check which providers are configured
const configuredProviderIds = ref([])

// Find has no provider or model of its own: it follows the one choice made in
// the top bar (see aiProviders.js). Kept as a list because the request and the
// cache identity are per provider.
const selectedProviders = computed(() => {
  const id = providerStore.activeProviderId
  return id && configuredProviderIds.value.includes(id) ? [id] : []
})

const initializeProviders = () => {
  migrateLegacySettings()
  configuredProviderIds.value = getConfiguredProviderIds()
}

// Re-read when the provider config changes. providerStore.version bumps on
// every config write.
watch(() => providerStore.version, initializeProviders)

onMounted(async () => {
  initializeProviders()

  // Server-stored team keys (hosted deployments) count as configured —
  // re-run provider init once we know which providers the server covers.
  await providerStore.loadServerProviders()
  initializeProviders()
})

onBeforeUnmount(() => {
  abortController?.abort()
  clearTimeout(copyTimer)
})

// Get configured AI settings from Settings tab (features only - rerank/synthesize)
// Inline AI feature toggles (read initial value from shared ai_settings, write back on change)
const _initialAISettings = getAISettings()
const localRerank = ref(_initialAISettings.rerank ?? true)
const localSynthesize = ref(_initialAISettings.synthesize ?? true)

watch([localRerank, localSynthesize], () => {
  try {
    const current = getAISettings()
    localStorage.setItem('ai_settings', JSON.stringify({ ...current, rerank: localRerank.value, synthesize: localSynthesize.value }))
  } catch (e) { /* ignore */ }
})

const aiFeaturesEnabled = computed(() => localRerank.value || localSynthesize.value)

// Check if AI will be used for this search (features enabled + providers selected)
const aiActive = computed(() => {
  return aiFeaturesEnabled.value && selectedProviders.value.length > 0
})

// Label for the settings button: which AI will answer, or why none will.
const providerSummary = computed(() => {
  if (!configuredProviderIds.value.length) return 'No AI connected'
  const count = selectedProviders.value.length
  if (!count) return 'No AI available'
  const model = providerStore.activeModel || providerStore.deploymentDefault.model || ''
  return getProviderDisplayName(selectedProviders.value[0]) + (model ? ` · ${model}` : '')
})

const loadingHeadline = computed(() => aiActive.value ? 'Searching and preparing AI results' : 'Searching your sources')
const loadingPhaseMessage = computed(() => aiActive.value
  ? 'AI answers and ranking can take a little longer. You can cancel at any time.'
  : 'Finding passages that match your query.')

const cacheStats = computed(() => searchStore.getCacheStats())

const historyEntries = computed(() => {
  // Get cache for current collection (cache is collection-aware: { collectionId: { "query|topK": entry } })
  const collectionId = collectionStore.currentCollectionId || 'default'
  const collectionCache = searchStore.cache[collectionId] || {}
  return Object.values(collectionCache)
    .filter(entry => entry && entry.query && entry.timestamp) // Filter out corrupted entries
    .sort((a, b) => b.timestamp - a.timestamp)
})

const getProviderDisplayNameLocal = getProviderDisplayName

const formatTimeAgo = (timestamp) => {
  const seconds = Math.floor((Date.now() - timestamp) / 1000)

  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)} minutes ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} hours ago`
  return `${Math.floor(seconds / 86400)} days ago`
}

// Serve a cached entry immediately (no re-cache: the original timestamp
// drives both the "cached · N ago" label and history ordering).
const serveCachedResult = (entry) => {
  searchStore.setSearchResults({
    query: entry.query,
    results: entry.results,
    aiResponses: entry.aiResponses,
    options: entry.options
  }, { cache: false })
  servedFromCache.value = { timestamp: entry.timestamp }
  providerWarnings.value = entry.warnings || ''
  error.value = ''
  notice.value = ''
  slashOutput.value = null
  statsStore.fetchStatsDebounced()
}

// Re-run the current query, bypassing the cache.
const refreshSearch = async () => {
  if (loading.value) return
  searchStore.setQuery(searchStore.lastQuery)
  await executeSearch()
}

const loadHistoryEntry = (entry) => {
  serveCachedResult(entry)

  // Update the query and topK values
  searchStore.setQuery(entry.query)
  searchStore.setTopK(entry.topK)
  if (entry.options) {
    searchMode.value = entry.options.mode
    semanticWeight.value = entry.options.semanticWeight
    localRerank.value = entry.options.rerank
    localSynthesize.value = entry.options.synthesize
  }

  historyModal.close()
}

const deleteHistoryEntry = (entry) => {
  searchStore.deleteCacheEntry(entry.query, entry.topK, entry.options)
}

const clearAllHistory = () => {
  if (confirm('Clear all search history? This cannot be undone.')) {
    searchStore.clearSearchCache()
    historyModal.close()
  }
}

const startNewSearch = () => {
  // Clear the current search state
  searchStore.clearResults()
  searchStore.setQuery('')
  error.value = ''
  servedFromCache.value = null
  notice.value = ''
  providerWarnings.value = ''
  slashOutput.value = null
  nextTick(() => searchInputRef.value?.focus())
}

const search = async () => {
  if (!searchStore.query.trim()) return

  error.value = ''
  notice.value = ''
  providerWarnings.value = ''
  slashPickerOpen.value = false

  // Intercept slash commands before any search machinery — they read collection
  // metadata directly and don't go through the vector index.
  if (isSlashCommand(searchStore.query)) {
    await runInlineSlashCommand(searchStore.query)
    return
  }

  // Clear any prior slash output when running a real search.
  slashOutput.value = null

  // Exact cache hit — serve instantly; the "cached · N ago · Refresh" line
  // above the results lets the user re-run it fresh.
  const cached = searchStore.getCachedResult(searchStore.query, searchStore.topK, currentOptions())
  if (cached) {
    serveCachedResult(cached)
    return
  }

  await executeSearch()
}

// Include every result-affecting setting in the cache identity, never API keys.
const currentOptions = () => ({
  mode: searchMode.value,
  semanticWeight: semanticWeight.value,
  rerank: localRerank.value,
  synthesize: localSynthesize.value,
  providers: selectedProviders.value.map(id => ({
    id, model: getProviderConfig(id)?.model || '',
    baseUrl: getProviderConfig(id)?.baseUrl || '',
  })),
})

const cancelSearch = () => {
  abortController?.abort()
  abortController = null
  loading.value = false
  notice.value = 'Search cancelled. You can edit your query and try again.'
}

watch(() => collectionStore.currentCollectionId, () => {
  cancelSearch()
  searchStore.clearResults()
  servedFromCache.value = null
  slashOutput.value = null
  notice.value = ''
  error.value = ''
  providerWarnings.value = ''
})

const executeSearch = async () => {
  if (loading.value || !searchStore.query.trim()) return
  const controller = new AbortController()
  abortController = controller
  const { signal } = controller
  const query = searchStore.query.trim()
  const topK = Math.max(1, Math.min(50, Math.round(Number(searchStore.topK)) || 10))
  searchStore.topK = topK
  const collectionId = collectionStore.currentCollectionId
  const options = currentOptions()
  const isCurrent = () => !signal.aborted && abortController === controller && collectionStore.currentCollectionId === collectionId
  const body = { query, top_k: topK, mode: options.mode, semantic_weight: options.semanticWeight }
  const endpoint = `/search?collection_id=${encodeURIComponent(collectionId)}`

  loading.value = true
  error.value = ''
  notice.value = ''
  providerWarnings.value = ''
  servedFromCache.value = null
  searchStore.clearResults()

  try {
    if ((options.rerank || options.synthesize) && options.providers.length) {
      const providerResults = await Promise.all(options.providers.map(async ({ id: provider, model }) => {
        try {
          const headers = buildProviderHeaders(provider, model || null)
          const response = await http.post(endpoint, {
            ...body,
            ai: { provider: getAPIProviderName(provider), rerank: options.rerank, synthesize: options.synthesize },
          }, { headers, signal, timeout: 0 })
          return { provider, results: response.data.results, synthesis: response.data.synthesis, aiUsage: response.data.ai_usage }
        } catch (err) {
          if (signal.aborted) throw err
          return { provider, error: err.response?.data?.detail || err.message || 'Request failed' }
        }
      }))
      if (!isCurrent()) return
      const successful = providerResults.filter(result => !result.error)
      if (!successful.length) throw new Error(providerResults[0]?.error || 'All AI searches failed')
      const failed = providerResults.filter(result => result.error)
      const missingAnswers = options.synthesize ? successful.filter(result => !result.synthesis) : []
      providerWarnings.value = [
        ...failed.map(result => `${getProviderDisplayName(result.provider)} could not complete this search.`),
        ...missingAnswers.map(result => `${getProviderDisplayName(result.provider)} returned passages without an AI answer.`),
      ].join(' ')
      searchStore.setSearchResults({
        query, topK, collectionId, options,
        results: successful[0].results,
        // Each provider can rerank differently; its citations must use its own order.
        aiResponses: successful,
        warnings: providerWarnings.value,
      }, { cache: !failed.length && !missingAnswers.length })
    } else {
      const response = await http.post(endpoint, body, { signal, timeout: 0 })
      if (!isCurrent()) return
      searchStore.setSearchResults({ query, topK, collectionId, options, results: response.data.results })
    }
    statsStore.fetchStatsDebounced()
  } catch (err) {
    if (!isCurrent()) return
    error.value = err.response?.data?.detail || err.message || 'Search failed. Please try again.'
  } finally {
    if (abortController === controller) {
      loading.value = false
      abortController = null
    }
  }
}
</script>

<style scoped>
/* Toolbar chips: visible on/off state, 44px touch target, same rhythm as .btn-sm. */
.option-chip {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  min-height: 2.75rem;
  padding-inline: 0.75rem;
  border: 1px solid color-mix(in oklab, var(--color-base-content) 25%, transparent);
  border-radius: var(--radius-field, 0.5rem);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition: border-color 120ms ease, background-color 120ms ease;
}
.option-chip:hover { background-color: var(--color-base-200); }
.option-chip.is-on {
  border-color: color-mix(in oklab, var(--color-primary) 60%, transparent);
  background-color: color-mix(in oklab, var(--color-primary) 8%, transparent);
}
.option-chip.is-unavailable { color: color-mix(in oklab, var(--color-base-content) 55%, transparent); cursor: not-allowed; }
.option-chip:has(:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }
</style>
