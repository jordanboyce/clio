<template>
  <div data-testid="feature-settings">
    <p class="text-[13px] text-base-content/55 leading-relaxed max-w-[60ch] mb-5">
      Everything the status bar at the bottom reports, and where each one is turned on, off or pointed
      somewhere else. Changes apply to this whole server straight away.
    </p>

    <div v-if="locked" class="alert py-2 mb-5 text-sm" role="note">
      <ShieldAlert :size="16" aria-hidden="true" />
      <span>These settings are managed by an admin. You can view them, but changes are admin-only.</span>
    </div>
    <p v-if="loadError" class="text-xs text-error mb-4" role="alert">{{ loadError }}</p>

    <fieldset :disabled="locked || !loaded" class="contents">
      <div class="divide-y divide-base-300/50">
        <div v-for="f in rows" :key="f.id" class="py-5 first:pt-0" :data-feature-row="f.id">
          <div class="flex items-start justify-between gap-8">
            <div class="min-w-0">
              <div class="flex items-baseline gap-2">
                <span class="text-sm font-medium">{{ f.label }}</span>
                <span v-if="f.state" class="text-xs flex items-center gap-1" :class="STATES[f.state].text" :data-state="f.state">
                  <component :is="STATES[f.state].icon" :size="11" class="self-center" aria-hidden="true" />
                  {{ STATES[f.state].name }}
                </span>
              </div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">{{ f.detail }}</p>
              <p v-if="saved === f.id" class="text-success text-xs mt-1.5" role="status">Saved</p>
              <p v-if="errors[f.id]" class="text-error text-xs mt-1.5" role="alert">{{ errors[f.id] }}</p>
            </div>

            <!-- One switch is the whole control for these -->
            <input
              v-if="TOGGLES[f.id]"
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
              :checked="form[TOGGLES[f.id]]"
              :aria-label="`${f.label} on or off`"
              :data-testid="`feature-toggle-${f.id}`"
              @change="saveToggle(f.id, $event.target.checked)"
            />
            <select
              v-else-if="f.id === 'reranker'"
              class="select select-bordered select-sm flex-shrink-0 w-36"
              :value="form.enable_reranker === null ? 'auto' : String(form.enable_reranker)"
              aria-label="Reranker"
              data-testid="feature-reranker"
              @change="save('reranker', { enable_reranker: { auto: null, true: true, false: false }[$event.target.value] })"
            >
              <option value="auto">Automatic</option>
              <option value="true">On</option>
              <option value="false">Off</option>
            </select>
            <select
              v-else-if="f.id === 'pii'"
              class="select select-bordered select-sm flex-shrink-0 w-36"
              :value="form.pii_scan_action"
              aria-label="What the personal data scan does"
              data-testid="feature-pii"
              @change="save('pii', { pii_scan_action: $event.target.value })"
            >
              <option value="off">Off</option>
              <option value="label">Label</option>
              <option value="flag">Flag for review</option>
              <option value="quarantine">Hold for review</option>
            </select>
            <button
              v-else-if="f.id === 'semantic'"
              type="button"
              class="btn btn-sm btn-outline flex-shrink-0"
              @click="$emit('open-section', 'indexing')"
            >Search model…</button>
          </div>

          <!-- Extra controls under the row -->
          <p v-if="f.id === 'reranker'" class="text-xs text-base-content/45 mt-2 max-w-[60ch]">
            Automatic turns it on when its model is already on this server, so search never waits on a download.
          </p>

          <p v-if="f.id === 'ocr'" class="text-xs text-base-content/45 mt-2 max-w-[60ch]">
            Which engine reads the pages is under
            <button type="button" class="link link-primary" @click="$emit('open-section', 'indexing')">Indexing</button>,
            in Advanced.
          </p>

          <div v-if="f.id === 'transcription' && form.enable_transcription" class="mt-3 flex items-center gap-3">
            <label class="text-xs font-medium" for="feature-whisper-model">Speech model</label>
            <select
              id="feature-whisper-model"
              class="select select-bordered select-sm w-44"
              :value="form.whisper_model"
              @change="save('transcription', { whisper_model: $event.target.value })"
            >
              <option v-if="!WHISPER_MODELS.some((m) => m.id === form.whisper_model)" :value="form.whisper_model" disabled>
                {{ form.whisper_model }} (set in the environment)
              </option>
              <option v-for="m in WHISPER_MODELS" :key="m.id" :value="m.id">{{ m.label }}</option>
            </select>
            <span class="text-xs text-base-content/45">Runs on this server. Larger is slower and more accurate.</span>
          </div>

          <!-- This server's model -->
          <div v-if="f.id === 'ai'" class="mt-3 space-y-3 max-w-xl">
            <p class="text-xs text-base-content/45 max-w-[60ch]">
              The model everyone on this server gets without setting anything up. People can still connect
              their own under
              <button type="button" class="link link-primary" @click="$emit('open-section', 'providers')">AI Providers</button>.
            </p>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div class="form-control">
                <label class="label p-0 pb-1" for="feature-ai-provider"><span class="label-text text-xs font-medium">Provider</span></label>
                <select id="feature-ai-provider" v-model="ai.provider" class="select select-bordered select-sm w-full" data-testid="feature-ai-provider">
                  <option value="">None</option>
                  <option v-for="p in aiProviders" :key="p.id" :value="p.id">{{ p.label }}</option>
                </select>
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1" for="feature-ai-model"><span class="label-text text-xs font-medium">Model</span></label>
                <input id="feature-ai-model" v-model.trim="ai.model" class="input input-bordered input-sm w-full" :disabled="!ai.provider" placeholder="The provider's default" />
              </div>
              <div v-if="aiNeedsUrl" class="form-control sm:col-span-2">
                <label class="label p-0 pb-1" for="feature-ai-url"><span class="label-text text-xs font-medium">Address</span></label>
                <input
                  id="feature-ai-url"
                  v-model.trim="ai.baseUrl"
                  type="url"
                  class="input input-bordered input-sm w-full"
                  :placeholder="ai.provider === 'ollama' ? 'http://localhost:11434' : 'http://your-server:8000/v1'"
                  data-testid="feature-ai-url"
                />
              </div>
              <div v-if="ai.provider && ai.provider !== 'ollama'" class="form-control">
                <label class="label p-0 pb-1" for="feature-ai-key"><span class="label-text text-xs font-medium">API key</span></label>
                <input
                  id="feature-ai-key"
                  v-model="ai.key"
                  type="password"
                  autocomplete="off"
                  class="input input-bordered input-sm w-full"
                  :placeholder="ai.keySet ? 'A key is saved. Type to replace it.' : (ai.provider === 'openai_compatible' ? 'Only if the server needs one' : 'Required')"
                />
              </div>
              <div v-if="ai.provider" class="form-control">
                <label class="label p-0 pb-1" for="feature-ai-label"><span class="label-text text-xs font-medium">Name people see</span></label>
                <input id="feature-ai-label" v-model.trim="ai.label" class="input input-bordered input-sm w-full" placeholder="For example: Lab model" />
              </div>
            </div>
            <button type="button" class="btn btn-sm btn-primary" :disabled="saving === 'ai'" data-testid="feature-ai-save" @click="saveAi">
              <span v-if="saving === 'ai'" class="loading loading-spinner loading-xs"></span>
              Save
            </button>
          </div>

          <div v-if="f.id === 'research' && form.research_lookup_enabled" class="mt-3 max-w-xl">
            <label class="text-xs font-medium" for="feature-research-email">Contact email sent with lookups (optional)</label>
            <div class="flex gap-2 mt-1">
              <input id="feature-research-email" v-model.trim="form.research_contact_email" type="email" class="input input-bordered input-sm flex-1" placeholder="library@your-organisation.example" />
              <button type="button" class="btn btn-sm btn-outline" @click="save('research', { research_contact_email: form.research_contact_email })">Save</button>
            </div>
            <p class="text-xs text-base-content/45 mt-1 max-w-[60ch]">
              Lookups send an identifier printed in the paper, such as its DOI, to OpenAlex, Crossref and arXiv.
              They ask for a contact address and answer faster with one.
            </p>
          </div>

          <div v-if="f.id === 'git'" class="mt-3 max-w-xl">
            <label class="text-xs font-medium" for="feature-git-hosts">Git servers that may be cloned</label>
            <div class="flex gap-2 mt-1">
              <input id="feature-git-hosts" v-model.trim="form.git_allowed_hosts" class="input input-bordered input-sm flex-1 font-mono" placeholder="git.corp.example, *.corp.example" data-testid="feature-git-hosts" />
              <button type="button" class="btn btn-sm btn-outline" data-testid="feature-git-save" @click="save('git', { git_allowed_hosts: form.git_allowed_hosts })">Save</button>
            </div>
            <p class="text-xs text-base-content/45 mt-1 max-w-[60ch]">
              Empty allows any public host. With hosts listed, only those can be cloned, including ones on
              your own network.
            </p>
          </div>

          <p v-if="f.id === 'mcp'" class="text-xs text-base-content/45 mt-2 max-w-[60ch]">
            Tokens, defaults and what agents may see are on the
            <button type="button" class="link link-primary" @click="$emit('switch-tab', 'mcp')">MCP</button> page.
          </p>
          <p v-if="f.id === 'pii'" class="text-xs text-base-content/45 mt-2 max-w-[60ch]">
            The threshold, what counts as personal data and masking are under Policy on the
            <button type="button" class="link link-primary" @click="$emit('switch-tab', 'admin')">Admin</button> page.
          </p>
        </div>
      </div>
    </fieldset>

    <p v-if="offline" class="text-xs text-base-content/50 mt-5 leading-relaxed max-w-[60ch]">
      <ShieldCheck :size="13" class="inline -mt-0.5 mr-1 text-success" aria-hidden="true" />This server runs in
      offline mode, which is set in its environment and overrides the switches here: hosted models, web links
      and catalogue lookups stay off whatever they say.
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { CheckCircle2, CircleAlert, MinusCircle, ShieldAlert, ShieldCheck, XCircle } from 'lucide-vue-next'
import http from '../utils/http'
import { useProviderStore } from '../stores/providerStore'

const props = defineProps({
  // Deployment settings are admin-only under private collections.
  locked: { type: Boolean, default: false },
  // The section is on screen. Read again each time, because other parts of
  // Settings change some of the same values.
  active: { type: Boolean, default: true },
  offline: { type: Boolean, default: false },
})
const emit = defineEmits(['open-section', 'switch-tab', 'changed'])

const providerStore = useProviderStore()

const STATES = {
  on: { name: 'On', icon: CheckCircle2, text: 'text-success' },
  limited: { name: 'Limited', icon: CircleAlert, text: 'text-warning' },
  off: { name: 'Off', icon: MinusCircle, text: 'text-base-content/45' },
  unavailable: { name: 'Unavailable', icon: XCircle, text: 'text-error' },
}
// Features whose whole control is one switch, and the setting behind it.
const TOGGLES = {
  ocr: 'enable_ocr',
  transcription: 'enable_transcription',
  figures: 'extract_figures',
  research: 'research_lookup_enabled',
  links: 'link_indexing_enabled',
  mcp: 'enable_mcp',
}
const WHISPER_MODELS = [
  { id: 'tiny', label: 'tiny (39 MB)' },
  { id: 'base', label: 'base (142 MB)' },
  { id: 'small', label: 'small (466 MB)' },
  { id: 'medium', label: 'medium (1.5 GB)' },
  { id: 'large-v3', label: 'large-v3 (3 GB)' },
]
const LOCAL_AI = [
  { id: 'ollama', label: 'Ollama' },
  { id: 'openai_compatible', label: 'OpenAI-compatible server' },
]
const HOSTED_AI = [
  { id: 'anthropic', label: 'Anthropic' },
  { id: 'openai', label: 'OpenAI' },
  { id: 'google', label: 'Google Gemini' },
  { id: 'grok', label: 'Grok (xAI)' },
  { id: 'github', label: 'GitHub Models' },
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'ollama_cloud', label: 'Ollama Cloud' },
]
const MASKED_SECRET = '********'

const features = ref([])
const loaded = ref(false)
const loadError = ref('')
const saving = ref('')
const saved = ref('')
const errors = reactive({})
const form = reactive({
  enable_reranker: null,
  enable_ocr: false,
  enable_transcription: true,
  whisper_model: 'base',
  extract_figures: true,
  research_lookup_enabled: true,
  research_contact_email: '',
  link_indexing_enabled: true,
  git_allowed_hosts: '',
  enable_mcp: true,
  pii_scan_action: 'label',
})
const ai = reactive({ provider: '', loadedProvider: '', model: '', baseUrl: '', key: '', keySet: false, label: '' })

// Offline mode refuses hosted providers, so they are not offered there.
const aiProviders = computed(() => {
  const list = props.offline ? LOCAL_AI : [...LOCAL_AI, ...HOSTED_AI]
  return list.some((p) => p.id === ai.provider) || !ai.provider
    ? list
    : [...list, { id: ai.provider, label: ai.provider }]
})
const aiNeedsUrl = computed(() => ['ollama', 'openai_compatible', 'openai'].includes(ai.provider))

// The server's order, so this reads the same as the status bar.
const rows = computed(() => features.value.map((f) => (STATES[f.state] ? f : { ...f, state: 'unavailable' })))

async function loadFeatures() {
  const { data } = await http.get('/api/features')
  features.value = data.features || []
}

async function load() {
  try {
    const [config] = await Promise.all([http.get('/api/config'), loadFeatures()])
    const data = config.data || {}
    for (const key of Object.keys(form)) {
      if (data[key] !== undefined) form[key] = data[key]
    }
    ai.provider = data.ai_provider || ''
    ai.loadedProvider = ai.provider
    ai.model = data.ai_model || ''
    ai.baseUrl = data.ai_base_url || ''
    ai.label = data.ai_provider_label || ''
    ai.keySet = Boolean(data.ai_api_key_set)
    ai.key = ''
    loadError.value = ''
    loaded.value = true
  } catch (error) {
    loadError.value = error?.message || 'Could not read the settings. Reload the page to try again.'
  }
}

// One save per control. The status is read again afterwards because that, not
// the switch, is what says whether the feature now works.
async function save(id, payload) {
  saving.value = id
  errors[id] = ''
  try {
    await http.post('/api/config', payload)
    Object.assign(form, Object.fromEntries(Object.entries(payload).filter(([key]) => key in form)))
    saved.value = id
    setTimeout(() => { if (saved.value === id) saved.value = '' }, 4000)
    emit('changed', id)
    // The status bar shows the same states and otherwise waits for its next poll.
    window.dispatchEvent(new Event('clio:features-changed'))
    return true
  } catch (error) {
    errors[id] = error?.message || 'Could not save.'
    return false
  } finally {
    saving.value = ''
    await Promise.all([loadFeatures().catch(() => {}), reloadForm()])
  }
}

// What the server now holds, so a refused change does not stay on screen.
async function reloadForm() {
  try {
    const { data } = await http.get('/api/config')
    for (const key of Object.keys(form)) {
      if (data[key] !== undefined) form[key] = data[key]
    }
    ai.keySet = Boolean(data.ai_api_key_set)
  } catch {
    // The next visit to the section reads it again.
  }
}

function saveToggle(id, on) {
  return save(id, { [TOGGLES[id]]: on })
}

async function saveAi() {
  const payload = {
    ai_provider: ai.provider,
    ai_model: ai.provider ? ai.model : '',
    ai_base_url: ai.provider && aiNeedsUrl.value ? ai.baseUrl : '',
    ai_provider_label: ai.provider ? ai.label : '',
  }
  if (ai.key && ai.provider && ai.provider !== 'ollama') payload.ai_api_key = ai.key
  // A saved key belongs to the provider it was saved for, so it does not
  // follow a change of provider.
  else if (ai.keySet && ai.provider === ai.loadedProvider && ai.provider !== 'ollama') payload.ai_api_key = MASKED_SECRET
  else payload.ai_api_key = ''
  if (await save('ai', payload)) {
    ai.key = ''
    ai.loadedProvider = ai.provider
    // The provider list and the model picker both show this server's model.
    await providerStore.loadServerProviders()
  }
}

onMounted(load)
watch(() => props.active, (isActive) => { if (isActive) load() })
</script>
