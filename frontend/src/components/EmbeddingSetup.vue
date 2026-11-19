<template>
  <div class="space-y-3" data-testid="embedding-setup">
    <div v-if="loading" class="space-y-2" aria-busy="true">
      <div v-for="n in 3" :key="n" class="skeleton h-14 w-full rounded-box" aria-hidden="true"></div>
      <span class="sr-only">Checking what is available on this server</span>
    </div>

    <template v-else-if="options">
      <div class="grid gap-2" role="radiogroup" aria-label="Where should the search index run">
        <div v-for="card in cards" :key="card.id">
          <button
            type="button"
            role="radio"
            :aria-checked="choice === card.id"
            :disabled="busy || card.disabled"
            :data-testid="`embedding-card-${card.id}`"
            class="w-full rounded-box border text-left p-3 transition-colors disabled:opacity-50"
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

          <!-- B1: why "On this server" cannot be chosen, shown on the card
               itself without a click; the operator's detail one click away. -->
          <div
            v-if="card.id === 'local' && localBlocked"
            class="mt-1 px-3 py-2 rounded-box bg-base-200/70 text-xs leading-relaxed"
            data-testid="local-unavailable-reason"
          >
            <p>
              Not available here: this server cannot reach {{ sourceName }}, so the model cannot be downloaded.
              <button type="button" class="link link-primary ml-1" :disabled="loading" @click="load">Check again</button>
            </p>
            <details class="mt-1">
              <summary class="cursor-pointer text-base-content/70">How to fix this</summary>
              <ul class="mt-1 list-disc pl-4 space-y-0.5 text-base-content/70">
                <li>Pick Ollama, your own embedding server, or a hosted provider above.</li>
                <li>Or copy the model into this server's data folder from a machine that has it (see the air-gap guide).</li>
                <li>Or point the server at a model mirror your network allows (the HF_ENDPOINT setting), then check again.</li>
              </ul>
            </details>
          </div>
        </div>
      </div>

      <!-- Per-choice detail -->
      <div v-if="choice === 'local'" class="text-xs text-base-content/60 leading-relaxed px-1">
        <template v-if="options.local.cached">The model is already on this server. Nothing to download.</template>
        <template v-else-if="!localBlocked">One download of about {{ options.local.size_mb }} MB, kept with your data. Text never leaves this machine.</template>
      </div>

      <div v-else-if="choice === 'ollama'" class="space-y-2">
        <div v-if="!options.ollama.reachable" class="notice notice-warning">
          <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
          <span>Ollama is not reachable at {{ options.ollama.base_url }}. Start it, then choose again.</span>
        </div>
        <template v-else>
          <label class="form-control">
            <span class="label-text text-xs">Embedding model</span>
            <select v-model="ollamaModel" class="select select-bordered select-sm" :disabled="busy" data-testid="ollama-model">
              <option v-for="m in ollamaChoices" :key="m" :value="m">{{ m }}<template v-if="!ollamaInstalled(m)"> · will be pulled</template></option>
            </select>
          </label>
        </template>
      </div>

      <!-- B5: a site whose only option is its own endpoint (vLLM, TEI, a gateway). -->
      <div v-else-if="choice === 'custom'" class="space-y-2" data-testid="custom-embedding">
        <label class="form-control">
          <span class="label-text text-xs">Address of the embedding server</span>
          <input v-model="custom.baseUrl" class="input input-bordered input-sm font-mono" placeholder="https://embeddings.example.org/v1" :disabled="busy" autocomplete="off" spellcheck="false" />
        </label>
        <label class="form-control">
          <span class="label-text text-xs">Model name</span>
          <input v-model="custom.model" class="input input-bordered input-sm font-mono" placeholder="The model name the server serves" :disabled="busy" autocomplete="off" spellcheck="false" />
        </label>
        <label class="form-control">
          <span class="label-text text-xs">Key (if the server asks for one)</span>
          <input v-model="custom.key" type="password" class="input input-bordered input-sm font-mono" :disabled="busy" autocomplete="off" spellcheck="false" />
        </label>
        <div class="flex items-center gap-2 flex-wrap">
          <button type="button" class="btn btn-sm" :disabled="busy || testing || !customComplete" data-testid="custom-test" @click="testCustom">
            <span v-if="testing" class="loading loading-spinner loading-xs" aria-hidden="true"></span>Try it
          </button>
          <span v-if="customResult?.ok" class="text-xs text-success" data-testid="custom-result">
            It answered: {{ customResult.dimensions }}-dimension vectors in {{ customResult.seconds }} s.
          </span>
          <span v-else-if="customResult" class="text-xs text-error" data-testid="custom-result">{{ customResult.error }}</span>
        </div>
        <p class="text-xs text-base-content/55 leading-relaxed px-1">The text of every source is sent to this server to be indexed. One embedding is tried first, so a wrong address or model is caught before anything is saved.</p>
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
            <span class="truncate">{{ status.status === 'downloading' ? (status.provider === 'ollama' ? 'Pulling the model into Ollama' : 'Downloading the search model') : 'Loading the search model' }}<span v-if="status.model" class="text-base-content/50"> · {{ status.model }}</span></span>
            <span v-if="status.progress?.percent != null" class="tabular-nums text-base-content/60">{{ status.progress.percent }}%</span>
          </div>
          <progress class="progress progress-primary w-full h-1 mt-1" :value="status.progress?.percent ?? undefined" max="100"></progress>
        </div>
      </div>
      <div v-else-if="status?.status === 'error'" class="notice notice-error">
        <CircleAlert :size="14" class="text-error" aria-hidden="true" />
        <span class="flex-1">{{ status.error || 'The search model could not be prepared.' }}</span>
      </div>
      <!-- B4: missing was a silent state; say what the app banner says. -->
      <div v-else-if="status?.status === 'missing'" class="notice notice-warning" role="status" data-testid="embedding-missing">
        <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
        <span class="flex-1">{{ missingText }}</span>
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
        <button type="button" class="btn btn-primary btn-sm" :disabled="busy || !canApply" data-testid="embedding-apply" @click="apply">
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
import { ref, computed, reactive, onMounted, onBeforeUnmount } from 'vue'
import { Laptop, Server, Cloud, Network, AlertTriangle, CircleAlert, Check } from 'lucide-vue-next'
import http from '../utils/http'
import { ollamaBaseName, sameOllamaModel } from '../utils/embeddingChoice'

// Where the search index (embeddings) runs. One choice, four honest
// options, and the download progress shown where the choice was made.
const emit = defineEmits(['done', 'skip'])

const loading = ref(true)
const options = ref(null)
const choice = ref('local')
const ollamaModel = ref('')  // set from the server's options
const hostedId = ref('')
const hostedKey = ref('')
const custom = reactive({ baseUrl: '', model: '', key: '' })
const customResult = ref(null)
const testing = ref(false)
const busy = ref(false)
const error = ref('')
const status = ref(null)
let pollTimer = null

const hostedChoices = computed(() => (options.value?.hosted || []).filter(h => !h.blocked_offline))
const hosted = computed(() => hostedChoices.value.find(h => h.id === hostedId.value) || null)
const installed = computed(() => options.value?.ollama?.embedding_models || [])
// B2: "name" and "name:latest" are one Ollama model.
function ollamaInstalled(name) {
  return installed.value.some(m => sameOllamaModel(m, name))
}
const ollamaChoices = computed(() => {
  const suggested = options.value?.ollama?.suggested
  const list = [...installed.value]
  if (suggested && !ollamaInstalled(suggested)) list.push(suggested)
  const seen = new Set()
  return list.filter(m => { const key = ollamaBaseName(m); if (seen.has(key)) return false; seen.add(key); return true })
})
const localBlocked = computed(() => {
  const local = options.value?.local
  return Boolean(local && local.available && !local.cached && local.huggingface_reachable === false)
})
const sourceName = computed(() => options.value?.local?.model_source || 'the model download site')
const customComplete = computed(() => custom.baseUrl.trim().length > 0 && custom.model.trim().length > 0)
const missingText = computed(() => {
  const model = status.value?.model ? ` (${status.value.model})` : ''
  return status.value?.can_download
    ? `The search model${model} is not on this server yet. Download it once and it stays with your data.`
    : `The search model${model} is not on this server, and this network cannot download it. Sources still index and search works by keyword. For search by meaning, choose another option above or pre-seed the model.`
})

const cards = computed(() => {
  const o = options.value
  if (!o) return []
  const list = []
  list.push({
    id: 'local', icon: Laptop, title: 'On this server',
    desc: !o.local.available
      ? `Needs a local runtime: ${o.local.install_hint || 'pip install fastembed'}.`
      : `Private. ${o.local.model_label || o.local.model}${o.local.cached ? ', already installed' : `, a ${o.local.size_mb} MB download`}.`,
    badge: o.local.cached ? 'Ready' : (localBlocked.value ? 'Cannot download' : ''),
    disabled: !o.local.available || localBlocked.value,
  })
  list.push({
    id: 'ollama', icon: Server, title: 'Ollama I already run',
    desc: o.ollama.reachable
      ? `Private. Found at ${o.ollama.base_url}${installed.value.length ? ` with ${installed.value.join(', ')}` : ''}.`
      : `Not reachable at ${o.ollama.base_url}.`,
    badge: o.ollama.reachable ? 'Detected' : '',
    disabled: !o.ollama.reachable,
  })
  list.push({
    id: 'custom', icon: Network, title: 'Our own embedding server',
    desc: o.custom?.configured
      ? 'Your site’s embedding service (vLLM, TEI or a gateway that speaks the OpenAI embeddings API) is already set.'
      : 'Your site’s embedding service: vLLM, TEI, or a gateway that speaks the OpenAI embeddings API.',
    badge: o.custom?.configured ? 'Set' : '',
    disabled: false,
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
  if (choice.value === 'local') return options.value.local.available && !localBlocked.value
  if (choice.value === 'ollama') return options.value.ollama.reachable && !!ollamaModel.value
  if (choice.value === 'custom') return customComplete.value && customResult.value?.ok === true
  if (choice.value === 'hosted') return !!hosted.value && (hosted.value.key_configured || hostedKey.value.trim().length > 0)
  return false
})
const applyLabel = computed(() => {
  if (status.value?.status === 'ready' && applied.value) return 'Done'
  if (choice.value === 'local') return options.value?.local?.cached ? 'Use this' : 'Download and use'
  if (choice.value === 'ollama') return ollamaInstalled(ollamaModel.value) ? 'Use this' : 'Pull and use'
  if (choice.value === 'custom' && customResult.value?.ok !== true) return 'Try it first'
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
    if (data.custom?.model) custom.model = data.custom.model
    if (data.custom?.base_url) custom.baseUrl = data.custom.base_url
    if (data.current?.provider && data.current.provider !== 'local') {
      // Something is configured already; reflect it rather than overriding.
      const p = data.current.provider
      choice.value = p === 'ollama' ? 'ollama' : p === 'openai_compatible' ? 'custom' : 'hosted'
      if (choice.value === 'hosted') hostedId.value = p
    }
  } catch (e) {
    options.value = null
    error.value = e?.message || 'Could not read the embedding options.'
  } finally {
    loading.value = false
  }
}

// B5: one embedding through the unsaved values; shows the dimension it read.
async function testCustom() {
  testing.value = true
  customResult.value = null
  try {
    const { data } = await http.post('/api/embedding/test', {
      embedding_provider: 'openai_compatible',
      embedding_base_url: custom.baseUrl.trim(),
      remote_embedding_model: custom.model.trim(),
      embedding_api_key: custom.key.trim() || null,
    })
    customResult.value = data.ok ? data : { ok: false, error: data.error || 'The server did not answer with an embedding.' }
  } catch (e) {
    customResult.value = { ok: false, error: e?.message || 'The server could not be tried.' }
  } finally {
    testing.value = false
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
    const body = { provider: 'local', model: options.value.local.model, api_key: null, warm: true }
    if (choice.value === 'ollama') {
      body.provider = 'ollama'
      body.model = ollamaModel.value
      // B3: the server saves the choice, follows the pull and warms when it
      // ends, all from this one call.
      body.pull = !ollamaInstalled(ollamaModel.value)
    } else if (choice.value === 'custom') {
      body.provider = 'openai_compatible'
      body.model = custom.model.trim()
      body.base_url = custom.baseUrl.trim()
      body.api_key = custom.key.trim() || null
    } else if (choice.value === 'hosted') {
      body.provider = hosted.value.id
      body.model = hosted.value.default_model || null
      body.api_key = hosted.value.key_configured ? null : hostedKey.value.trim()
    }
    const { data } = await http.post('/api/embedding/select', body)
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
