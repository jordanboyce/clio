<template>
  <div class="space-y-3" data-testid="embedding-setup">
    <div v-if="loading" class="space-y-2" aria-busy="true">
      <div v-for="n in 3" :key="n" class="skeleton h-14 w-full rounded-box" aria-hidden="true"></div>
      <span class="sr-only">Checking what is available on this server</span>
    </div>

    <template v-else-if="options">
      <div class="grid gap-2" role="radiogroup" aria-label="Where should the search index run">
        <button
          v-for="card in cards"
          :key="card.id"
          type="button"
          role="radio"
          :aria-checked="choice === card.id"
          :disabled="busy || card.disabled"
          class="rounded-box border text-left p-3 transition-colors disabled:opacity-50"
          :class="choice === card.id ? 'border-primary bg-primary/5' : 'border-base-300 bg-base-100 hover:border-base-content/30'"
          @click="choose(card.id)"
        >
          <div class="flex items-center gap-2">
            <component :is="card.icon" :size="15" class="text-base-content/60 flex-shrink-0" aria-hidden="true" />
            <span class="font-medium text-sm">{{ card.title }}</span>
            <span v-if="card.id === options.recommended" class="text-[10px] uppercase tracking-[0.12em] font-semibold text-primary/80">Recommended</span>
            <span v-if="card.badge" class="ml-auto text-[11px] text-base-content/50">{{ card.badge }}</span>
          </div>
          <p class="text-xs text-base-content/60 mt-1 leading-snug">{{ card.desc }}</p>
        </button>
      </div>

      <!-- Per-choice detail -->
      <div v-if="choice === 'local'" class="text-xs text-base-content/60 leading-relaxed px-1">
        <template v-if="options.local.cached">The model is already on this server. Nothing to download.</template>
        <template v-else-if="options.local.huggingface_reachable === false">This server cannot reach huggingface.co, so the model cannot be downloaded here. Pick Ollama or a hosted provider, or pre-seed the model (see docs/AIRGAP.md).</template>
        <template v-else>One download of about {{ options.local.size_mb }} MB, kept with your data. Text never leaves this machine.</template>
      </div>

      <div v-else-if="choice === 'ollama'" class="space-y-2">
        <div v-if="!options.ollama.reachable" class="notice notice-warning">
          <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
          <span>Ollama is not reachable at {{ options.ollama.base_url }}. Start it, then choose again.</span>
        </div>
        <template v-else>
          <label class="form-control">
            <span class="label-text text-xs">Embedding model</span>
            <select v-model="ollamaModel" class="select select-bordered select-sm" :disabled="busy">
              <option v-for="m in ollamaChoices" :key="m" :value="m">{{ m }}<template v-if="!options.ollama.embedding_models.includes(m)"> · will be pulled</template></option>
            </select>
          </label>
        </template>
      </div>

      <div v-else-if="choice === 'hosted'" class="space-y-2">
        <label class="form-control">
          <span class="label-text text-xs">Provider</span>
          <select v-model="hostedId" class="select select-bordered select-sm" :disabled="busy">
            <option v-for="h in hostedChoices" :key="h.id" :value="h.id">{{ h.label }}<template v-if="h.key_configured"> · key on file</template></option>
          </select>
        </label>
        <input
          v-if="hosted && !hosted.key_configured"
          v-model="hostedKey"
          type="password"
          autocomplete="off"
          spellcheck="false"
          class="input input-bordered input-sm w-full font-mono"
          :placeholder="`${hosted.label} API key`"
          :aria-label="`${hosted.label} API key`"
          :disabled="busy"
        />
        <p class="text-xs text-base-content/55 leading-relaxed px-1">The text of every source is sent to {{ hosted?.label || 'the provider' }} to be indexed. Choose a provider you trust with it.</p>
      </div>

      <!-- Progress while the model lands -->
      <div v-if="status && ['downloading', 'loading'].includes(status.status)" class="notice" role="status" aria-live="polite">
        <span class="loading loading-spinner loading-xs" aria-hidden="true"></span>
        <div class="flex-1 min-w-0">
          <div class="flex items-center justify-between gap-2">
            <span class="truncate">{{ status.status === 'downloading' ? 'Downloading the search model' : 'Loading the search model' }}<span v-if="status.model" class="text-base-content/50"> · {{ status.model }}</span></span>
            <span v-if="status.progress?.percent != null" class="tabular-nums text-base-content/60">{{ status.progress.percent }}%</span>
          </div>
          <progress class="progress progress-primary w-full h-1 mt-1" :value="status.progress?.percent ?? undefined" max="100"></progress>
        </div>
      </div>
      <div v-else-if="status?.status === 'error'" class="notice notice-error">
        <CircleAlert :size="14" class="text-error" aria-hidden="true" />
        <span class="flex-1">{{ status.error || 'The search model could not be prepared.' }}</span>
      </div>
      <div v-else-if="status?.status === 'ready'" class="notice">
        <Check :size="14" class="text-success" aria-hidden="true" />
        <span>Search index ready<span v-if="status.model" class="text-base-content/50"> · {{ status.model }}</span></span>
      </div>

      <div v-if="error" class="notice notice-error" role="alert">
        <CircleAlert :size="14" class="text-error" aria-hidden="true" />
        <span>{{ error }}</span>
      </div>

      <div class="flex items-center gap-2 pt-1">
        <button type="button" class="btn btn-primary btn-sm" :disabled="busy || !canApply" @click="apply">
          <span v-if="busy" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
          {{ applyLabel }}
        </button>
        <button v-if="status && ['downloading', 'loading'].includes(status.status)" type="button" class="btn btn-ghost btn-sm" @click="$emit('done', status)">Continue in the background</button>
        <button v-else type="button" class="btn btn-ghost btn-sm" @click="$emit('skip')">Decide later</button>
      </div>
    </template>

    <div v-else class="notice notice-warning">
      <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
      <span class="flex-1">{{ error || 'Could not read the embedding options from the server.' }}</span>
      <button type="button" class="btn btn-ghost btn-xs" @click="load">Retry</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { Laptop, Server, Cloud, AlertTriangle, CircleAlert, Check } from 'lucide-vue-next'
import http from '../utils/http'

// Where the search index (embeddings) runs. One choice, three honest
// options, and the download progress shown where the choice was made.
const emit = defineEmits(['done', 'skip'])

const loading = ref(true)
const options = ref(null)
const choice = ref('local')
const ollamaModel = ref('nomic-embed-text')
const hostedId = ref('')
const hostedKey = ref('')
const busy = ref(false)
const error = ref('')
const status = ref(null)
let pollTimer = null

const hostedChoices = computed(() => (options.value?.hosted || []).filter(h => !h.blocked_offline))
const hosted = computed(() => hostedChoices.value.find(h => h.id === hostedId.value) || null)
const ollamaChoices = computed(() => {
  const present = options.value?.ollama?.embedding_models || []
  const suggested = options.value?.ollama?.suggested || 'nomic-embed-text'
  return Array.from(new Set([...present, suggested]))
})

const cards = computed(() => {
  const o = options.value
  if (!o) return []
  const list = []
  list.push({
    id: 'local', icon: Laptop, title: 'On this server',
    desc: o.local.available
      ? `Private. ${o.local.model_label || o.local.model}${o.local.cached ? ', already installed' : `, a ${o.local.size_mb} MB download`}.`
      : `Needs a local runtime: ${o.local.install_hint || 'pip install fastembed'}.`,
    badge: o.local.cached ? 'Ready' : '',
    disabled: !o.local.available || (!o.local.cached && o.local.huggingface_reachable === false),
  })
  list.push({
    id: 'ollama', icon: Server, title: 'Ollama I already run',
    desc: o.ollama.reachable
      ? `Private. Found at ${o.ollama.base_url}${o.ollama.embedding_models.length ? ` with ${o.ollama.embedding_models.join(', ')}` : ''}.`
      : `Not reachable at ${o.ollama.base_url}.`,
    badge: o.ollama.reachable ? 'Detected' : '',
    disabled: !o.ollama.reachable,
  })
  list.push({
    id: 'hosted', icon: Cloud, title: 'A hosted provider I trust',
    desc: o.offline_mode ? 'Not available: this deployment runs air-gapped.' : 'OpenAI, Gemini, Voyage, Mistral, Jina or OpenRouter. Source text leaves this machine.',
    badge: hostedChoices.value.some(h => h.key_configured) ? 'Key on file' : '',
    disabled: o.offline_mode || hostedChoices.value.length === 0,
  })
  return list
})

const canApply = computed(() => {
  if (!options.value) return false
  if (choice.value === 'local') return options.value.local.available && (options.value.local.cached || options.value.local.huggingface_reachable !== false)
  if (choice.value === 'ollama') return options.value.ollama.reachable && !!ollamaModel.value
  if (choice.value === 'hosted') return !!hosted.value && (hosted.value.key_configured || hostedKey.value.trim().length > 0)
  return false
})
const applyLabel = computed(() => {
  if (status.value?.status === 'ready' && applied.value) return 'Done'
  if (choice.value === 'local') return options.value?.local?.cached ? 'Use this' : 'Download and use'
  if (choice.value === 'ollama') return options.value?.ollama?.embedding_models?.includes(ollamaModel.value) ? 'Use this' : 'Pull and use'
  return 'Use this'
})
const applied = ref(false)

function choose(id) {
  if (busy.value) return
  choice.value = id
  error.value = ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await http.get('/api/embedding/options')
    options.value = data
    choice.value = data.recommended || 'local'
    if (data.ollama?.suggested) ollamaModel.value = data.ollama.embedding_models?.[0] || data.ollama.suggested
    const withKey = (data.hosted || []).find(h => h.key_configured && !h.blocked_offline)
    hostedId.value = withKey?.id || (data.hosted || []).find(h => !h.blocked_offline)?.id || ''
    if (data.current?.provider && data.current.provider !== 'local') {
      // Something is configured already; reflect it rather than overriding.
      choice.value = data.current.provider === 'ollama' ? 'ollama' : 'hosted'
      if (choice.value === 'hosted') hostedId.value = data.current.provider
    }
  } catch (e) {
    options.value = null
    error.value = e?.message || 'Could not read the embedding options.'
  } finally {
    loading.value = false
  }
}

async function refreshStatus() {
  try {
    const { data } = await http.get('/api/embedding/status')
    status.value = data
    if (data.status === 'ready' && applied.value) {
      stopPolling()
      emit('done', data)
    } else if (['error', 'missing'].includes(data.status)) {
      stopPolling()
    }
  } catch { /* keep the last status */ }
}
function startPolling() {
  stopPolling()
  pollTimer = setInterval(refreshStatus, 1000)
}
function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

async function apply() {
  if (!canApply.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    let provider = 'local'
    let model = options.value.local.model
    let api_key = null
    if (choice.value === 'ollama') {
      provider = 'ollama'
      model = ollamaModel.value
      if (!options.value.ollama.embedding_models.includes(model)) {
        await http.post('/api/embedding/ollama/pull', { model })
      }
    } else if (choice.value === 'hosted') {
      provider = hosted.value.id
      model = hosted.value.default_model || null
      api_key = hosted.value.key_configured ? null : hostedKey.value.trim()
    }
    const { data } = await http.post('/api/embedding/select', { provider, model, api_key, warm: true })
    applied.value = true
    status.value = data.status
    if (data.status?.status === 'ready') {
      emit('done', data.status)
    } else {
      startPolling()
    }
  } catch (e) {
    error.value = e?.message || 'Could not save that choice.'
  } finally {
    busy.value = false
  }
}

onMounted(load)
onBeforeUnmount(stopPolling)
</script>
