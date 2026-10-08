<template>
  <div
    v-if="show"
    class="fixed inset-0 z-[300] bg-base-200 flex items-center justify-center p-4 sm:p-6 overflow-y-auto"
    role="dialog"
    aria-modal="true"
    aria-labelledby="welcome-title"
  >
    <div class="max-w-lg w-full my-auto">
      <!-- Logo + welcome -->
      <div class="text-center mb-6">
        <span class="brand-art brand-art-mark block w-16 h-16 mx-auto mb-3" role="img" aria-label="Clio"></span>
        <p class="text-[11px] uppercase tracking-[0.22em] font-semibold text-base-content/40 mb-4">Clio</p>
        <h1 id="welcome-title" class="text-2xl font-semibold tracking-tight">
          {{ stage === 'embeddings' ? 'Where should the search index run?' : 'Answers from your sources' }}
        </h1>
        <p class="text-sm text-base-content/60 mt-2 max-w-sm mx-auto leading-relaxed">
          <template v-if="stage === 'embeddings'">
            Every source is turned into searchable vectors. This can happen on this server, in an Ollama you already run, or at a hosted provider you trust.
          </template>
          <template v-else>
            Add sources, ask questions, and check the citations. Connect a local model for private answers; hosted models receive the context used to answer.
          </template>
        </p>
        <!-- Step indicator -->
        <ol class="flex items-center justify-center gap-2 mt-4 text-[11px]" aria-label="Setup steps">
          <li v-for="(step, i) in STEPS" :key="step" class="flex items-center gap-2">
            <span
              class="inline-flex items-center gap-1.5"
              :class="stepIndex === i ? 'text-base-content font-medium' : stepIndex > i ? 'text-base-content/60' : 'text-base-content/35'"
              :aria-current="stepIndex === i ? 'step' : undefined"
            >
              <span class="inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px] tabular-nums"
                :class="stepIndex > i ? 'bg-success/20 text-success' : stepIndex === i ? 'bg-base-content text-base-100' : 'bg-base-content/10'">
                <Check v-if="stepIndex > i" :size="10" aria-hidden="true" /><template v-else>{{ i + 1 }}</template>
              </span>
              {{ step }}
            </span>
            <span v-if="i < STEPS.length - 1" class="w-5 h-px bg-base-content/15" aria-hidden="true"></span>
          </li>
        </ol>
      </div>

      <!-- Stage 2: embeddings -->
      <EmbeddingSetup v-if="stage === 'embeddings'" @done="finish" @skip="finish" />

      <!-- Step 1: choose how to connect -->
      <div v-if="stage === 'provider'" class="grid grid-cols-2 gap-2" role="radiogroup" aria-label="Choose a provider">
        <button
          v-for="card in cards"
          :key="card.id"
          type="button"
          role="radio"
          :aria-checked="selected === card.id"
          class="rounded-box border text-left p-3 transition-colors"
          :class="selected === card.id
            ? 'border-primary bg-primary/5'
            : 'border-base-300 bg-base-100 hover:border-base-content/30'"
          @click="selectCard(card.id)"
        >
          <div class="font-medium text-sm">{{ card.title }}</div>
          <div class="text-xs text-base-content/60 mt-0.5 leading-snug">{{ card.desc }}</div>
        </button>
      </div>

      <!-- Step 1b: inline connect panel -->
      <div v-if="stage === 'provider' && selected" class="mt-4 space-y-3">
        <!-- Anthropic / OpenAI: API key -->
        <template v-if="selected === 'anthropic' || selected === 'openai'">
          <input
            ref="inputRef"
            v-model="apiKey"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :placeholder="selectedDef?.keyPlaceholder"
            class="input input-bordered w-full font-mono text-sm"
            :disabled="busy"
            :aria-label="`${selectedDef?.name} API key`"
            @keyup.enter="connectKey"
          />
          <div class="flex items-center justify-between">
            <a
              :href="selectedDef?.keyLink"
              target="_blank"
              rel="noopener noreferrer"
              class="link link-hover text-xs text-base-content/60"
            >
              Get your key →
            </a>
            <button class="btn btn-primary btn-sm" :disabled="!apiKey.trim() || busy" @click="connectKey">
              <span v-if="busy" class="loading loading-spinner loading-xs"></span>
              {{ busy ? 'Verifying…' : 'Connect' }}
            </button>
          </div>
        </template>

        <!-- Ollama (local): detect, pick a model -->
        <template v-else-if="selected === 'ollama'">
          <div class="join w-full">
            <input
              ref="inputRef"
              v-model="baseUrl"
              type="text"
              spellcheck="false"
              class="input input-bordered input-sm join-item w-full font-mono text-sm"
              :disabled="busy"
              aria-label="Ollama base URL"
              @keyup.enter="models.length ? connectOllama() : detectOllama()"
            />
            <button class="btn btn-sm join-item" :disabled="busy" @click="detectOllama">
              <span v-if="busy" class="loading loading-spinner loading-xs"></span>
              Detect
            </button>
          </div>
          <template v-if="models.length">
            <select v-model="model" class="select select-bordered select-sm w-full" aria-label="Model">
              <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label || m.id }}</option>
            </select>
            <button class="btn btn-primary btn-sm w-full" :disabled="!model" @click="connectOllama">
              Connect
            </button>
          </template>
        </template>

        <!-- Custom OpenAI-compatible endpoint -->
        <template v-else-if="selected === 'custom'">
          <select v-model="presetId" class="select select-bordered select-sm w-full" aria-label="Endpoint preset">
            <option v-for="p in availablePresets" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <input
            v-if="presetId === 'other'"
            v-model="customName"
            type="text"
            placeholder="Name (e.g. My server)"
            class="input input-bordered input-sm w-full text-sm"
            :disabled="busy"
            aria-label="Endpoint name"
          />
          <input
            ref="inputRef"
            v-model="baseUrl"
            type="text"
            spellcheck="false"
            placeholder="https://your-endpoint/v1"
            class="input input-bordered input-sm w-full font-mono text-sm"
            :disabled="busy"
            aria-label="Base URL"
            @keyup.enter="customPrimary"
          />
          <input
            v-model="apiKey"
            type="password"
            autocomplete="off"
            spellcheck="false"
            :placeholder="preset?.needsKey ? 'API key' : 'API key (optional)'"
            class="input input-bordered input-sm w-full font-mono text-sm"
            :disabled="busy"
            aria-label="API key"
            @keyup.enter="customPrimary"
          />
          <a
            v-if="preset?.keyLink"
            :href="preset.keyLink"
            target="_blank"
            rel="noopener noreferrer"
            class="link link-hover text-xs text-base-content/60 block"
          >
            Get your key →
          </a>
          <select
            v-if="customId && models.length"
            v-model="model"
            class="select select-bordered select-sm w-full"
            aria-label="Model"
          >
            <option v-for="m in models" :key="m.id" :value="m.id">{{ m.label || m.id }}</option>
          </select>
          <input
            v-else-if="customId"
            v-model="model"
            type="text"
            spellcheck="false"
            placeholder="Model id (optional)"
            class="input input-bordered input-sm w-full font-mono text-sm"
            aria-label="Model id"
            @keyup.enter="customPrimary"
          />
          <button
            class="btn btn-primary btn-sm w-full"
            :disabled="!baseUrl.trim() || busy"
            @click="customPrimary"
          >
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            {{ busy ? 'Testing…' : customId ? 'Connect' : 'Test & Connect' }}
          </button>
        </template>

        <!-- Inline error -->
        <div v-if="error" class="alert alert-error text-sm py-2" role="alert">
          <span>{{ error }}</span>
        </div>
      </div>

      <!-- Quiet footer: more providers + skip -->
      <div v-if="stage === 'provider'" class="text-center text-xs text-base-content/55 mt-6 leading-relaxed">
        <template v-if="!offline">
          More providers (Gemini, Grok, OpenRouter, GitHub, Ollama Cloud) are available in Settings.
          <span class="text-base-content/30" aria-hidden="true">·</span>
        </template>
        <template v-else>
          This deployment runs air-gapped — only local and self-hosted models are available.
          <span class="text-base-content/30" aria-hidden="true">·</span>
        </template>
        <button class="link link-hover" @click="skipProvider">Start with search</button>
      </div>

      <!-- Trust copy footer -->
      <div class="mt-8 text-center text-[11px] text-base-content/45 leading-relaxed">
        Sources are stored on your Clio server. Hosted AI and embedding providers receive the text they process.
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { Check } from 'lucide-vue-next'
import http from '../utils/http'
import EmbeddingSetup from './EmbeddingSetup.vue'
import { useUserStore } from '../stores/userStore'
import {
  PROVIDER_DEFS,
  CUSTOM_ENDPOINT_PRESETS,
  upsertProviderConfig,
  setActiveProviderLS,
  testProviderConnection,
  fetchProviderModels,
  isRemoteDeployment,
} from '../utils/aiProviders.js'

const props = defineProps({
  show: { type: Boolean, default: false },
})

const emit = defineEmits(['complete', 'skip'])

// Two stages: the answer model, then the search index. The index step is
// skipped when it is already ready (the baked Docker image) or when this
// person cannot change deployment settings (a non-admin on a hosted site).
const STEPS = ['Answers', 'Search index', 'Sources']
const stage = ref('provider')
const stepIndex = computed(() => (stage.value === 'embeddings' ? 1 : 0))
const userStore = useUserStore()
let outcome = 'complete'

const needsEmbeddingStep = async () => {
  if (userStore.privateCollections && !userStore.isAdmin) return false
  try {
    const { data } = await http.get('/api/embedding/status')
    return data.status !== 'ready'
  } catch {
    return false
  }
}
const advance = async (kind) => {
  outcome = kind
  if (await needsEmbeddingStep()) stage.value = 'embeddings'
  else emit(outcome)
}
const finish = () => emit(outcome)
const skipProvider = () => advance('skip')

const CARDS = [
  { id: 'anthropic', title: 'Anthropic', desc: 'Claude models' },
  { id: 'openai', title: 'OpenAI', desc: 'GPT models' },
  { id: 'ollama', title: 'Ollama (local)', desc: 'Private, runs on this machine' },
  { id: 'custom', title: 'Your own endpoint', desc: 'LM Studio, vLLM, Groq, any OpenAI-compatible URL' },
]

// Air-gapped deployments (OFFLINE_MODE=1): the backend rejects cloud
// providers, so only offer the local paths here.
const offline = ref(false)
onMounted(async () => {
  try {
    const h = await http.get('/health')
    offline.value = !!h.data.offline_mode
  } catch { /* assume standard */ }
})

const cards = computed(() => {
  if (offline.value) {
    return [
      { id: 'ollama', title: 'Ollama (local)', desc: 'Private, runs on this machine' },
      { id: 'custom', title: 'Your own endpoint', desc: 'LM Studio, vLLM, any self-hosted OpenAI-compatible URL' },
    ]
  }
  // Hosted: "runs on this machine" would mislead — the browser's machine is
  // not the server. Cloud providers and custom URLs remain.
  if (isRemoteDeployment()) return CARDS.filter(c => c.id !== 'ollama')
  return CARDS
})

const availablePresets = computed(() =>
  offline.value
    ? CUSTOM_ENDPOINT_PRESETS.filter(p => !p.baseUrl || !p.baseUrl.startsWith('https://'))
    : CUSTOM_ENDPOINT_PRESETS
)

const selected = ref(null)
const apiKey = ref('')
const baseUrl = ref('')
const model = ref('')
const models = ref([])
const presetId = ref(CUSTOM_ENDPOINT_PRESETS[0].id)
const customName = ref('')
const customId = ref('') // set once the endpoint has been validated
const busy = ref(false)
const error = ref('')
const inputRef = ref(null)

const selectedDef = computed(() => PROVIDER_DEFS.find((d) => d.id === selected.value) || null)
const preset = computed(() => CUSTOM_ENDPOINT_PRESETS.find((p) => p.id === presetId.value) || null)

const focusInput = () => nextTick(() => inputRef.value?.focus())

const selectCard = (id) => {
  if (busy.value) return
  selected.value = id
  error.value = ''
  apiKey.value = ''
  model.value = ''
  models.value = []
  customId.value = ''
  if (id === 'ollama') baseUrl.value = 'http://localhost:11434'
  else if (id === 'custom') baseUrl.value = preset.value?.baseUrl || ''
  else baseUrl.value = ''
  focusInput()
}

watch(presetId, () => {
  baseUrl.value = preset.value?.baseUrl || ''
  customName.value = ''
  customId.value = ''
  models.value = []
  model.value = ''
  error.value = ''
  focusInput()
})

// Editing credentials invalidates a previous detect/test result.
watch([baseUrl, apiKey], () => {
  if (selected.value === 'custom') customId.value = ''
  if (selected.value === 'ollama' || selected.value === 'custom') {
    models.value = []
    model.value = ''
  }
})

// Anthropic / OpenAI: validate the key, save, activate.
const connectKey = async () => {
  const id = selected.value
  const key = apiKey.value.trim()
  if (!key || busy.value) return
  busy.value = true
  error.value = ''
  const { valid, error: err } = await testProviderConnection(id, { apiKey: key })
  if (!valid) {
    error.value = err || 'Could not verify that key. Double-check you copied it correctly.'
    busy.value = false
    return
  }
  upsertProviderConfig(id, { apiKey: key, model: selectedDef.value?.models?.[0]?.id || '' })
  try {
    // Best-effort server-side store so the MCP chat path can use it too.
    await http.post('/api/agent/config', null, { params: { provider: id, api_key: key } })
  } catch (e) {
    console.warn('Could not store key server-side:', e?.message || e)
  }
  setActiveProviderLS(id)
  busy.value = false
  advance('complete')
}

// Ollama: probe the local server for models.
const detectOllama = async () => {
  if (busy.value) return
  busy.value = true
  error.value = ''
  const url = baseUrl.value.trim() || 'http://localhost:11434'
  const { models: found } = await fetchProviderModels('ollama', { baseUrl: url })
  busy.value = false
  if (found.length) {
    models.value = found
    model.value = found[0].id
  } else {
    error.value = "Ollama isn't reachable — is it running?"
  }
}

const connectOllama = () => {
  if (!model.value) return
  upsertProviderConfig('ollama', {
    baseUrl: baseUrl.value.trim() || 'http://localhost:11434',
    model: model.value,
    available: true,
  })
  setActiveProviderLS('ollama')
  advance('complete')
}

// Custom endpoint: first click validates + lists models, second click saves.
const customPrimary = () => (customId.value ? connectCustom() : testCustom())

const testCustom = async () => {
  const url = baseUrl.value.trim()
  if (!url || busy.value) return
  busy.value = true
  error.value = ''
  const id = `custom_${preset.value?.id || 'other'}_${Date.now()}`
  const key = apiKey.value.trim()
  const { valid, error: err } = await testProviderConnection(id, { apiKey: key, baseUrl: url })
  if (!valid) {
    error.value = err || 'Could not reach that endpoint. Check the URL and key.'
    busy.value = false
    return
  }
  const { models: found } = await fetchProviderModels(id, { apiKey: key, baseUrl: url })
  customId.value = id
  models.value = found
  model.value = found[0]?.id || ''
  busy.value = false
}

const connectCustom = () => {
  if (!customId.value) return
  upsertProviderConfig(customId.value, {
    id: customId.value,
    name: customName.value.trim() || preset.value?.name || 'Custom endpoint',
    baseUrl: baseUrl.value.trim(),
    apiKey: apiKey.value.trim() || 'none',
    model: model.value || '',
    isCustom: true,
  })
  setActiveProviderLS(customId.value)
  advance('complete')
}

watch(
  () => props.show,
  (isShown) => {
    if (isShown && selected.value) focusInput()
  },
)
</script>
