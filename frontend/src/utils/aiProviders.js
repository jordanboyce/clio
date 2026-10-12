/**
 * Shared AI provider utilities.
 * Used by every AI surface (App top bar, SettingsTab, ChatTab, SearchTab,
 * ArtifactsTab) to read/write provider configs, resolve which provider a
 * surface should use (see "Provider resolution chain" below), and build API
 * request headers in a consistent way.
 */

// `models` here are curated RECOMMENDATIONS only (2-3 per provider) — the
// full list is fetched live from the provider via fetchProviderModels(),
// so we never ship a stale 60-model catalog again.
export const PROVIDER_DEFS = [
  {
    id: 'anthropic',
    name: 'Anthropic',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'sk-ant-...',
    keyLink: 'https://console.anthropic.com/settings/keys',
    models: [
      { id: 'claude-sonnet-5', label: 'Claude Sonnet 5 (quality)' },
      { id: 'claude-opus-5', label: 'Claude Opus 5 (best)' },
      { id: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (fast)' },
    ],
  },
  {
    id: 'openai',
    name: 'OpenAI',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'sk-...',
    keyLink: 'https://platform.openai.com/api-keys',
    models: [
      { id: 'gpt-4o', label: 'GPT-4o (quality)' },
      { id: 'gpt-4o-mini', label: 'GPT-4o Mini (fast)' },
    ],
  },
  {
    id: 'grok',
    name: 'Grok (xAI)',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'xai-...',
    keyLink: 'https://console.x.ai/',
    models: [
      { id: 'grok-3', label: 'Grok-3 (quality)' },
      { id: 'grok-3-mini', label: 'Grok-3 Mini (fast)' },
    ],
  },
  {
    id: 'google',
    name: 'Google Gemini',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'AIza...',
    keyLink: 'https://aistudio.google.com/app/apikey',
    models: [
      { id: 'gemini-2.5-pro-preview-03-25', label: 'Gemini 2.5 Pro (quality)' },
      { id: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash (fast)' },
    ],
  },
  {
    id: 'github',
    name: 'GitHub Models',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'github_pat_... or ghp_...',
    keyLink: 'https://github.com/settings/tokens?type=beta',
    models: [
      { id: 'openai/gpt-4o', label: 'GPT-4o (quality)' },
      { id: 'openai/gpt-4o-mini', label: 'GPT-4o Mini (fast)' },
      { id: 'meta/Llama-3.3-70B-Instruct', label: 'Llama 3.3 70B' },
    ],
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'sk-or-...',
    keyLink: 'https://openrouter.ai/keys',
    models: [
      { id: 'anthropic/claude-3.5-sonnet', label: 'Claude 3.5 Sonnet (quality)' },
      { id: 'anthropic/claude-3.5-haiku', label: 'Claude 3.5 Haiku (fast)' },
      { id: 'deepseek/deepseek-chat', label: 'DeepSeek V3' },
    ],
  },
  {
    id: 'ollama',
    name: 'Ollama',
    type: 'local',
    badge: 'local',
    hasBaseUrl: true,
    defaultBaseUrl: 'http://localhost:11434',
    models: [], // populated dynamically via detection
  },
  {
    id: 'ollama_cloud',
    name: 'Ollama Cloud',
    type: 'cloud',
    badge: 'cloud',
    keyPlaceholder: 'your Ollama API key',
    keyLink: 'https://ollama.com/settings/keys',
    models: [
      { id: 'gemma4:31b', label: 'Gemma 4 31B (free tier)' },
      { id: 'gpt-oss:120b', label: 'GPT-OSS 120B (quality)' },
      { id: 'gpt-oss:20b', label: 'GPT-OSS 20B (fast)' },
    ],
  },
]

// Popular OpenAI-compatible endpoints offered as presets when adding a
// custom endpoint. `baseUrl: null` means the user supplies it.
export const CUSTOM_ENDPOINT_PRESETS = [
  { id: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', needsKey: false },
  { id: 'vllm', name: 'vLLM', baseUrl: 'http://localhost:8000/v1', needsKey: false },
  { id: 'litellm', name: 'LiteLLM proxy', baseUrl: 'http://localhost:4000', needsKey: true },
  { id: 'groq', name: 'Groq', baseUrl: 'https://api.groq.com/openai/v1', needsKey: true, keyLink: 'https://console.groq.com/keys' },
  { id: 'together', name: 'Together AI', baseUrl: 'https://api.together.xyz/v1', needsKey: true, keyLink: 'https://api.together.ai/settings/api-keys' },
  { id: 'mistral', name: 'Mistral', baseUrl: 'https://api.mistral.ai/v1', needsKey: true, keyLink: 'https://console.mistral.ai/api-keys' },
  { id: 'deepseek', name: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1', needsKey: true, keyLink: 'https://platform.deepseek.com/api_keys' },
  { id: 'other', name: 'Other (any OpenAI-compatible URL)', baseUrl: null, needsKey: false },
]

const CONFIG_KEY = 'ai_providers_config'
const SETTINGS_KEY = 'ai_settings'

// Providers with a server-stored team key (set via /api/agent/config on a
// hosted instance). Requests for these send no X-AI-Key header — the backend
// falls back to its stored key — so coworkers never enter credentials.
let _serverProviderIds = []

/** Fetch which providers have a server-stored team key. Cached per page load. */
export async function fetchServerProviderIds() {
  try {
    const { default: axios } = await import('axios')
    const resp = await axios.get('/api/agent/config')
    _serverProviderIds = Object.entries(resp.data?.providers || {})
      .filter(([, v]) => v?.configured)
      .map(([id]) => id)
  } catch {
    _serverProviderIds = []
  }
  return _serverProviderIds
}

/** Server-side team-key providers (call fetchServerProviderIds() first). */
export function getServerProviderIds() {
  return _serverProviderIds
}

// The provider an operator wired into the deployment itself (AI_PROVIDER /
// AI_BASE_URL / AI_MODEL in the server's environment). On an on-prem install
// the model belongs to the organisation, not to each person's browser, so
// this counts as configured for everyone and nobody types an internal URL
// into a settings form. Shape: { configured, provider, label, model,
// base_url, key_configured } — never the key itself.
let _deploymentDefault = { configured: false }

/** Fetch the deployment's own provider. Cached per page load. */
export async function fetchDeploymentDefault() {
  try {
    const { default: axios } = await import('axios')
    const resp = await axios.get('/api/ai/deployment')
    _deploymentDefault = resp.data?.configured ? resp.data : { configured: false }
  } catch {
    _deploymentDefault = { configured: false }
  }
  return _deploymentDefault
}

/** The deployment's provider (call fetchDeploymentDefault() first). */
export function getDeploymentDefault() {
  return _deploymentDefault
}

/** True when this id is the deployment's own provider. */
export function isDeploymentProvider(id) {
  return !!_deploymentDefault.configured && _deploymentDefault.provider === id
}

/** Return all provider configs (built-in + custom) from localStorage. */
export function getProvidersConfig() {
  try {
    const raw = localStorage.getItem(CONFIG_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

export function saveProvidersConfig(configs) {
  localStorage.setItem(CONFIG_KEY, JSON.stringify(configs))
}

/** Get config for one provider by id. */
export function getProviderConfig(id) {
  return getProvidersConfig().find(p => p.id === id) || null
}

/** Create or update a provider config entry. */
export function upsertProviderConfig(id, data) {
  const configs = getProvidersConfig()
  const idx = configs.findIndex(p => p.id === id)
  if (idx >= 0) {
    configs[idx] = { ...configs[idx], ...data }
  } else {
    configs.push({ id, ...data })
  }
  saveProvidersConfig(configs)
}

/** Remove a provider config (used for custom providers or key removal). */
export function removeProviderConfig(id) {
  saveProvidersConfig(getProvidersConfig().filter(p => p.id !== id))
}

/** Get the current ai_settings object. Rerank and synthesize default to on. */
export function getAISettings() {
  try {
    const stored = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
    return { rerank: true, synthesize: true, ...stored }
  } catch {
    return { rerank: true, synthesize: true }
  }
}

/** Get the active (app-wide default) provider id. */
export function getActiveProvider() {
  return getAISettings().provider || null
}

/** Set the active (app-wide default) provider. */
export function setActiveProviderLS(id) {
  const settings = getAISettings()
  localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...settings, provider: id }))
  notifyProviderChange()
}

// ── Provider resolution ────────────────────────────────────────────────────
//
// THE single source of truth for "which provider answers". There is one
// choice, made in the top bar, and every AI surface (Chat, Search, Reports,
// …) follows it — no surface keeps its own provider or model, because two
// places to choose is how a chat ends up on one model and a search on another.
//
//   1. Global active provider — `ai_settings.provider`, set from the top-bar
//      model picker or "Use as default" in Settings (only honored while that
//      provider is still configured).
//   2. First configured provider, where "configured" includes the
//      deployment's own provider (AI_PROVIDER on the server — listed first,
//      so an on-prem browser that configured nothing lands on the
//      organisation's model) and server-stored team keys. Call
//      loadServerProviders() — or fetchDeploymentDefault() and
//      fetchServerProviderIds() — first so both count.
//
// The model is the provider's configured model (`providerConfig.model`),
// changed from the same picker.

/**
 * Resolve the provider every surface should use (see above).
 * Returns '' when nothing is configured.
 */
export function resolveProvider() {
  const configured = getConfiguredProviderIds()
  const active = getActiveProvider()
  if (active && configured.includes(active)) return active
  return configured[0] || ''
}

/** Set the model a provider uses everywhere ('' = the provider's own default). */
export function setProviderModel(id, model) {
  upsertProviderConfig(id, { model: model || '' })
  notifyProviderChange()
}

/**
 * Older builds let Chat and Search each pick their own provider and model.
 * Those overrides are retired: one choice, in the top bar. Run once at
 * startup. A chat override that was in force becomes the global default (Chat
 * is the main surface, so that is the model the person was actually using);
 * the rest are dropped so a stale value cannot silently steer one surface.
 */
export function retireSurfaceOverrides() {
  try {
    const chat = localStorage.getItem('clio_provider_override_chat')
    if (chat && getConfiguredProviderIds().includes(chat)) {
      localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...getAISettings(), provider: chat }))
    }
    const stale = []
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i)
      if (key && (key.startsWith('clio_provider_override_')
        || key === 'clio_chat_model_overrides'
        || key === 'clio_search_model_overrides'
        || key === 'clio_selected_providers')) stale.push(key)
    }
    stale.forEach(k => localStorage.removeItem(k))
  } catch { /* storage unavailable: nothing to retire */ }
}

/**
 * Broadcast that provider config/selection changed so long-lived surfaces
 * re-resolve. localStorage isn't reactive, hence an explicit signal.
 *
 * The signal is a plain listener list (not a window CustomEvent, which is
 * what this used to be): stores/providerStore.js subscribes once and turns
 * it into Vue reactivity for every consumer.
 */
const _changeListeners = []

export function onProviderChange(fn) {
  _changeListeners.push(fn)
}

export function notifyProviderChange() {
  for (const fn of _changeListeners) {
    try { fn() } catch { /* one bad listener must not mute the rest */ }
  }
}

/**
 * Returns ids of all providers that are considered "configured":
 *  - cloud provider: has a non-empty apiKey in this browser, OR a
 *    server-stored team key (see fetchServerProviderIds)
 *  - ollama: marked available=true
 *  - custom: has a non-empty baseUrl
 */
export function getConfiguredProviderIds() {
  const configs = getProvidersConfig()
  const result = []

  // First, so a browser that has configured nothing resolves to the
  // deployment's own model (resolveProvider falls back to configured[0]).
  // An explicit choice in this browser still wins — that is step 1/2 of the
  // chain, which runs before this fallback.
  if (_deploymentDefault.configured) result.push(_deploymentDefault.provider)

  for (const def of PROVIDER_DEFS) {
    const cfg = configs.find(c => c.id === def.id)
    if (def.type === 'local') {
      if (cfg?.available) result.push(def.id)
    } else if (cfg?.apiKey || _serverProviderIds.includes(def.id)) {
      result.push(def.id)
    }
  }

  // Custom providers
  for (const cfg of configs) {
    if (cfg.isCustom && cfg.baseUrl) result.push(cfg.id)
  }

  return [...new Set(result)]
}

/**
 * Build HTTP headers for a provider API call.
 * Returns object suitable for axios `headers` option.
 * Pass modelOverride to use a different model than the saved default.
 */
export function buildProviderHeaders(providerId, modelOverride = null) {
  const cfg = getProviderConfig(providerId)
  const headers = {}
  if (!cfg) {
    // No local config. For the deployment's own provider that is the normal
    // case: the server holds the endpoint and key, and sending nothing is
    // what makes it pick them up. A model override still travels, so someone
    // can switch models on an endpoint that serves several.
    const model = (modelOverride || '').trim()
    if (model && isDeploymentProvider(providerId)) headers['X-AI-Model'] = model
    return headers
  }

  const resolvedModel = (modelOverride && modelOverride.trim()) ? modelOverride.trim() : (cfg.model || '')

  if (cfg.isCustom) {
    if (cfg.apiKey && cfg.apiKey !== 'none') headers['X-AI-Key'] = cfg.apiKey
    if (cfg.baseUrl) headers['X-AI-Base-URL'] = cfg.baseUrl
    if (resolvedModel) headers['X-AI-Model'] = resolvedModel
  } else if (providerId === 'ollama') {
    const baseUrl = cfg.baseUrl || 'http://localhost:11434'
    if (baseUrl !== 'http://localhost:11434') headers['X-AI-Base-URL'] = baseUrl
    const model = resolvedModel || 'llama3.2'
    headers['X-Ollama-Model'] = model
    headers['X-AI-Model'] = model
  } else {
    if (cfg.apiKey) headers['X-AI-Key'] = cfg.apiKey
    if (resolvedModel) headers['X-AI-Model'] = resolvedModel
  }

  return headers
}

/**
 * Returns the backend provider name for a given frontend provider id.
 * Custom providers map to 'openai_compatible'.
 */
export function getAPIProviderName(providerId) {
  const cfg = getProviderConfig(providerId)
  if (cfg?.isCustom) return 'openai_compatible'
  // Unknown ids (e.g. a custom endpoint not yet saved) are OpenAI-compatible
  if (!PROVIDER_DEFS.find(d => d.id === providerId)) return 'openai_compatible'
  return providerId
}

/** Human-readable display name for a provider id. */
export function getProviderDisplayName(providerId) {
  // The deployment's label wins over the generic provider name: on-prem it
  // is "Acme Internal LLM", not "Ollama".
  if (isDeploymentProvider(providerId) && _deploymentDefault.label) {
    return _deploymentDefault.label
  }
  const def = PROVIDER_DEFS.find(d => d.id === providerId)
  if (def) return def.name
  const cfg = getProviderConfig(providerId)
  return cfg?.name || providerId
}

/** Curated recommended models for a provider id. Empty for custom/unknown. */
export function getProviderModels(providerId) {
  return PROVIDER_DEFS.find(d => d.id === providerId)?.models ?? []
}

// ── Live model listing & connection testing ────────────────────────────────

const _modelCache = new Map()

/**
 * Fetch the models a provider actually offers, using the stored credentials
 * (or explicit overrides before they're saved). Returns
 *   { models: [{id, label, recommended}], error: string|null }
 * Curated recommendations come first, then every other live model. Falls
 * back to recommendations alone when the provider can't enumerate models.
 * Cached per page load; pass { force: true } after changing credentials.
 */
export async function fetchProviderModels(providerId, { force = false, apiKey = undefined, baseUrl = undefined } = {}) {
  const cacheKey = providerId
  if (!force && apiKey === undefined && baseUrl === undefined && _modelCache.has(cacheKey)) {
    return _modelCache.get(cacheKey)
  }

  const cfg = getProviderConfig(providerId) || {}
  const headers = { 'X-AI-Provider': getAPIProviderName(providerId) }
  const key = apiKey !== undefined ? apiKey : cfg.apiKey
  if (key && key !== 'none') headers['X-AI-Key'] = key
  const url = baseUrl !== undefined ? baseUrl : cfg.baseUrl
  if (url) headers['X-AI-Base-URL'] = url

  const recommended = getProviderModels(providerId).map(m => ({ ...m, recommended: true }))
  let result
  try {
    const { default: axios } = await import('axios')
    const resp = await axios.post('/api/ai/models', null, { headers })
    const liveIds = resp.data?.models || []
    const seen = new Set(recommended.map(m => m.id))
    const live = liveIds.filter(id => !seen.has(id)).map(id => ({ id, label: id, recommended: false }))
    result = { models: [...recommended, ...live], error: resp.data?.error || null }
  } catch (err) {
    result = { models: recommended, error: err?.response?.data?.detail || err.message }
  }

  if (!result.error && apiKey === undefined && baseUrl === undefined) {
    _modelCache.set(cacheKey, result)
  }
  return result
}

/** Drop the cached model list for a provider (call after key/URL changes). */
export function invalidateModelCache(providerId) {
  if (providerId) _modelCache.delete(providerId)
  else _modelCache.clear()
}

/**
 * Test a provider connection WITHOUT saving it. Accepts explicit credentials
 * so setup UIs can validate before persisting. Returns { valid, error }.
 * providerId may be a custom endpoint id (maps to openai_compatible).
 */
export async function testProviderConnection(providerId, { apiKey = '', baseUrl = '', model = '' } = {}) {
  const headers = { 'X-AI-Provider': getAPIProviderName(providerId) }
  if (apiKey && apiKey !== 'none') headers['X-AI-Key'] = apiKey
  if (baseUrl) headers['X-AI-Base-URL'] = baseUrl
  if (model) headers['X-AI-Model'] = model
  try {
    const { default: axios } = await import('axios')
    const resp = await axios.post('/api/ai/validate-key', null, { headers })
    return { valid: !!resp.data?.valid, error: resp.data?.error || null }
  } catch (err) {
    return { valid: false, error: err?.response?.data?.detail || err.message }
  }
}

/**
 * True when the app is being used at a remote origin (hosted deployment).
 * "Local" options (Ollama on localhost, LM Studio, vLLM presets) point at the
 * *server's* network in that case — an option labeled "runs on this machine"
 * would mislead, because the browser's machine is not the server. Local
 * installs (localhost origins) keep the full list; server-side .env config
 * can still enable any provider regardless of what the UI offers.
 */
export function isRemoteDeployment() {
  const h = window.location.hostname
  return !(h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1')
}

/** Whether a provider is local/private (not cloud). */
export function isLocalProvider(providerId) {
  const cfg = getProviderConfig(providerId)
  if (cfg?.isCustom) return false // custom could be remote
  return PROVIDER_DEFS.find(d => d.id === providerId)?.type === 'local'
}

/**
 * Migrate legacy localStorage keys (ai_api_key_anthropic etc.) to the
 * unified ai_providers_config format. Safe to call on every mount.
 */
export function migrateLegacySettings() {
  const configs = getProvidersConfig()
  let changed = false

  const legacyMap = [
    { id: 'anthropic', keyLS: 'ai_api_key_anthropic', modelLS: 'anthropic_model' },
    { id: 'openai', keyLS: 'ai_api_key_openai', modelLS: 'openai_model' },
  ]

  for (const { id, keyLS, modelLS } of legacyMap) {
    const apiKey = localStorage.getItem(keyLS)
    const model = localStorage.getItem(modelLS) || ''
    if (apiKey && !configs.find(c => c.id === id)) {
      configs.push({ id, apiKey, model })
      changed = true
    }
  }

  const ollamaModel = localStorage.getItem('ollama_model')
  if (!configs.find(c => c.id === 'ollama')) {
    configs.push({
      id: 'ollama',
      model: ollamaModel || 'llama3.2',
      baseUrl: 'http://localhost:11434',
      available: false,
    })
    changed = true
  }

  if (changed) saveProvidersConfig(configs)

  // Legacy 'chat_provider' (Chat's private pre-unification choice) folds into
  // the resolution chain: it becomes the global default when none was ever
  // set, a chat-specific override when it disagrees with the global, and
  // disappears entirely when it matches (chat then follows global).
  const legacyChat = localStorage.getItem('chat_provider')
  if (legacyChat !== null) {
    localStorage.removeItem('chat_provider')
    if (legacyChat) {
      const active = getActiveProvider()
      if (!active) {
        setActiveProviderLS(legacyChat)
      } else if (legacyChat !== active && !getProviderOverride('chat')) {
        setProviderOverride('chat', legacyChat)
      }
    }
  }
}
