<template>
  <div class="max-w-3xl mx-auto py-4 space-y-4">
    <div>
      <h2 class="text-lg font-semibold">Generate</h2>
      <p class="text-sm text-base-content/60">
        Turn this collection into a source-grounded document — every claim cites the sources it came from.
      </p>
    </div>

    <!-- Artifact type picker -->
    <div class="grid grid-cols-2 sm:grid-cols-3 gap-2">
      <button
        v-for="t in types"
        :key="t.type"
        class="text-left rounded-lg border p-3 transition-colors"
        :class="selectedType === t.type
          ? 'border-primary bg-primary/10'
          : 'border-base-300 bg-base-200/40 hover:bg-base-100'"
        @click="selectedType = t.type"
      >
        <div class="font-medium text-sm">{{ t.label }}</div>
        <div class="text-xs text-base-content/60 mt-0.5">{{ t.description }}</div>
      </button>
    </div>

    <!-- Options -->
    <div class="space-y-2">
      <input
        v-model="focus"
        type="text"
        placeholder="Optional focus (e.g. a topic or a specific document)"
        class="input input-bordered input-sm w-full"
      />
      <textarea
        v-model="customInstructions"
        rows="2"
        placeholder="Optional extra instructions"
        class="textarea textarea-bordered textarea-sm w-full"
      ></textarea>
    </div>

    <div class="flex items-center gap-3">
      <button
        class="btn btn-primary btn-sm"
        :disabled="loading || !selectedType || !providerId"
        @click="generate"
      >
        <span v-if="loading" class="loading loading-spinner loading-xs"></span>
        {{ loading ? 'Generating…' : 'Generate' }}
      </button>
      <span v-if="!providerId" class="text-xs text-warning">
        Configure an AI provider in Settings first.
      </span>
      <span v-else class="text-xs text-base-content/50">
        Using {{ providerDisplayName(providerId) }}
      </span>
    </div>

    <!-- Error -->
    <div v-if="error" class="alert alert-error text-sm py-2">
      {{ error }}
    </div>

    <!-- Result -->
    <div v-if="result" class="rounded-lg border border-base-300 bg-base-100">
      <div class="flex items-center justify-between border-b border-base-300 px-4 py-2">
        <h3 class="font-semibold text-sm">{{ result.title }}</h3>
        <div class="flex gap-1">
          <button class="btn btn-ghost btn-xs" @click="copyMarkdown">{{ copied ? 'Copied' : 'Copy' }}</button>
          <button class="btn btn-ghost btn-xs" @click="downloadMarkdown">Download</button>
        </div>
      </div>
      <div
        class="prose prose-sm max-w-none p-4 text-sm artifact-markdown"
        v-html="renderedContent"
      ></div>

      <div v-if="result.sources && result.sources.length" class="border-t border-base-300 px-4 py-3">
        <div class="text-xs font-semibold text-base-content/60 mb-1">Sources</div>
        <ul class="text-xs space-y-0.5">
          <li v-for="(s, i) in result.sources" :key="i" class="text-base-content/70">
            <a v-if="s.page_url" :href="s.page_url" target="_blank" class="link link-hover">
              {{ s.filename }}<span v-if="s.page_number"> — p.{{ s.page_number }}</span>
            </a>
            <span v-else>{{ s.filename }}<span v-if="s.page_number"> — p.{{ s.page_number }}</span></span>
          </li>
        </ul>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { useCollectionStore } from '../stores/collectionStore'
import { useProviderStore } from '../stores/providerStore'
import { useSelectionStore } from '../stores/selectionStore'
import {
  getAPIProviderName,
  buildProviderHeaders,
  getProviderDisplayName,
} from '../utils/aiProviders.js'

marked.setOptions({ gfm: true, breaks: true })

const collectionStore = useCollectionStore()
const selectionStore = useSelectionStore()

const types = ref([])
const selectedType = ref('summary')
const focus = ref('')
const customInstructions = ref('')
const loading = ref(false)
const error = ref('')
const result = ref(null)
const copied = ref(false)

// Generate has no per-surface override: it follows the app-wide default via
// the shared resolution chain and just displays "Using X". Reactivity comes
// from providerStore.version (bumped on every provider config write).
const providerStore = useProviderStore()
const providerId = computed(() => providerStore.activeProviderId)

const providerDisplayName = (id) => getProviderDisplayName(id)

const renderedContent = computed(() => {
  if (!result.value?.content) return ''
  return DOMPurify.sanitize(marked.parse(String(result.value.content)), { ADD_ATTR: ['target', 'rel'] })
})

onMounted(async () => {
  // Server-stored team keys count as configured — providerStore bumps its
  // version once they're known, which re-resolves providerId.
  try {
    await providerStore.loadServerProviders()
  } catch { /* offline from server config is fine — local configs still count */ }

  try {
    const resp = await fetch('/api/artifacts/types')
    if (resp.ok) {
      const data = await resp.json()
      types.value = data.types || []
      if (types.value.length && !types.value.some(t => t.type === selectedType.value)) {
        selectedType.value = types.value[0].type
      }
    }
  } catch (e) {
    // Non-fatal: fall back to a minimal default set.
    types.value = [{ type: 'summary', label: 'Executive summary', description: 'Overview of the collection.' }]
  }
})



const generate = async () => {
  if (!selectedType.value || !providerId.value) return
  loading.value = true
  error.value = ''
  result.value = null
  try {
    const collectionId = collectionStore.currentCollectionId
    const headers = { 'Content-Type': 'application/json', ...buildProviderHeaders(providerId.value) }
    const resp = await fetch(`/api/artifacts?collection_id=${collectionId}`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        artifact_type: selectedType.value,
        provider: getAPIProviderName(providerId.value),
        scope: 'current',
        focus: focus.value || null,
        custom_instructions: customInstructions.value || null,
        // A report honours the same source selection as chat.
        document_ids: selectionStore.active ? selectionStore.currentIds : null,
      }),
    })
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}))
      throw new Error(errBody.detail || `HTTP ${resp.status}`)
    }
    result.value = await resp.json()
  } catch (e) {
    error.value = e.message || 'Generation failed'
  } finally {
    loading.value = false
  }
}

const copyMarkdown = async () => {
  if (!result.value?.content) return
  await navigator.clipboard.writeText(result.value.content)
  copied.value = true
  setTimeout(() => (copied.value = false), 1500)
}

const downloadMarkdown = () => {
  if (!result.value) return
  const blob = new Blob([result.value.content], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${(result.value.title || 'artifact').replace(/[^a-z0-9]+/gi, '_').toLowerCase()}.md`
  a.click()
  URL.revokeObjectURL(url)
}
</script>
