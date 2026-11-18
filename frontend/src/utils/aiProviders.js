/**
 * Shared AI provider utilities.
 * Used by every AI surface (App top bar, SettingsTab, ChatTab, SearchTab,
 * ArtifactsTab) to read/write provider configs, resolve which provider a
 * surface should use (see "Provider resolution chain" below), and build API
 * request headers in a consistent way.
 */

// No model names live here. What a provider offers, what each model can do,
// and which ones are recommended all come from the server
// (fetchProviderModels -> POST /api/ai/models, built from
// services/model_catalog.py), so a new model generation is a server change.
//
// `type` says where a provider's models run: 'local' is a machine the user
// or their organisation runs, 'cloud' is a company's service. `description`
// is the one plain line the provider list shows under the name.
export const PROVIDER_DEFS = [
  {
    id: 'ollama',
    name: 'Ollama',
    type: 'local',
    badge: 'local',
    description: 'Open models you run yourself, on this machine or a server on your network.',
    hasBaseUrl: true,
    defaultBaseUrl: 'http://localhost:11434',
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    type: 'cloud',
    badge: 'cloud',
    description: 'Claude models, run by Anthropic.',
    keyPlaceholder: 'sk-ant-...',
    keyLink: 'https://console.anthropic.com/settings/keys',
  },
  {
    id: 'openai',
    name: 'OpenAI',
    type: 'cloud',
    badge: 'cloud',
    description: 'GPT models, run by OpenAI.',
    keyPlaceholder: 'sk-...',
    keyLink: 'https://platform.openai.com/api-keys',
  },
  {
    id: 'google',
    name: 'Google Gemini',
    type: 'cloud',
    badge: 'cloud',
    description: 'Gemini models, run by Google. AI Studio keys include a free allowance.',
    keyPlaceholder: 'AIza...',
    keyLink: 'https://aistudio.google.com/app/apikey',
  },
  {
    id: 'grok',
    name: 'Grok (xAI)',
    type: 'cloud',
    badge: 'cloud',
    description: 'Grok models, run by xAI.',
    keyPlaceholder: 'xai-...',
    keyLink: 'https://console.x.ai/',
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    type: 'cloud',
    badge: 'cloud',
    description: 'Many companies’ models through one key; your text goes on to the one you pick.',
    keyPlaceholder: 'sk-or-...',
    keyLink: 'https://openrouter.ai/keys',
  },
  {
    id: 'ollama_cloud',
    name: 'Ollama Cloud',
    type: 'cloud',
    badge: 'cloud',
    description: 'Large open models, run by Ollama on its own servers.',
    keyPlaceholder: 'your Ollama API key',
    keyLink: 'https://ollama.com/settings/keys',
  },
]

// Providers that no longer exist (GitHub Models, retired 2026-07-30). A
// browser that saved one keeps working: migrateLegacySettings() drops the
// saved config, and nothing resolves to it in the meantime.
export const RETIRED_PROVIDER_IDS = ['github']

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
    // Through the app's client, so the startup bootstrap can answer it (utils/bootstrap.js).
    const { default: http } = await import('./http')
    const resp = await http.get('/api/agent/config', { quiet: true })
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
    // Through the app's client, so the startup bootstrap can answer it (utils/bootstrap.js).
    const { default: http } = await import('./http')
    const resp = await http.get('/api/ai/deployment', { quiet: true })
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

/**
 * The config this browser actually set up for a provider, or null.
 *
 * migrateLegacySettings() seeds an Ollama row ({available: false}) so the
 * Settings form has something to edit. That seed is not a choice: until the
 * Ollama check passes in Settings it must not shadow the deployment's own
 * Ollama model, or a first visit asks the server for a default model that
 * was never installed there.
 */
export function getActiveProviderConfig(id) {
  const cfg = getProviderConfig(id)
  if (!cfg) return null
  if (id === 'ollama' && !cfg.available) return null
  return cfg
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
  const cfg = getActiveProviderConfig(providerId)
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
    // No model chosen: send none, and the server uses its default.
    if (resolvedModel) {
      headers['X-Ollama-Model'] = resolvedModel
      headers['X-AI-Model'] = resolvedModel
    }
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

// ── Live model listing & connection testing ────────────────────────────────

const _modelCache = new Map()

const NO_DEFAULTS = Object.freeze({ model: null, fast: null, vision: null })

/**
 * One server capability record as the pickers show it. Recommended models
 * keep the server's label and role ("<label> (quality)"); every
 * other model is shown by its id, as before. `badges` lists what the
 * provider (or the server's table) says the model can do.
 */
export function toPickerModel(record) {
  const roles = (record.roles || []).filter(r => r !== 'vision' || record.roles.length === 1)
  const label = record.recommended
    ? `${record.label || record.id}${roles.length ? ` (${roles.join(', ')})` : ''}`
    : record.id
  const badges = []
  if (record.vision === true) badges.push('vision')
  if (record.tools === true) badges.push('tools')
  return {
    ...record,
    label,
    recommended: !!record.recommended,
    badges,
    // <option> text: the label plus the abilities, since an option holds no markup.
    display: badges.length ? `${label} · ${badges.join(' · ')}` : label,
  }
}

/**
 * Fetch the models a provider actually offers, using the stored credentials
 * (or explicit overrides before they're saved). Returns
 *   { models: [record + {label, recommended, badges, display}],
 *     defaults: {model, fast, vision}, suggested: {embedding, vision},
 *     listed: bool, error: string|null }
 * The server puts its recommendations first, and recommends only models the
 * provider listed. When the provider cannot be asked (offline, blocked, bad
 * key), the server answers with its recommendations and an error.
 * Cached per page load; pass { force: true } after changing credentials
 * (that also asks the server to skip its own short cache).
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

  let result
  try {
    const { default: axios } = await import('axios')
    const options = { headers }
    if (force) options.params = { refresh: true }
    const resp = await axios.post('/api/ai/models', null, options)
    const data = resp.data || {}
    // A server from before capability records sends bare ids only.
    const records = Array.isArray(data.records)
      ? data.records
      : (data.models || []).map(id => ({ id, label: id }))
    result = {
      models: records.map(toPickerModel),
      defaults: { ...NO_DEFAULTS, ...(data.defaults || {}) },
      suggested: data.suggested || {},
      listed: data.listed ?? records.length > 0,
      error: data.error || null,
    }
  } catch (err) {
    result = { models: [], defaults: { ...NO_DEFAULTS }, suggested: {}, listed: false, error: err?.response?.data?.detail || err.message }
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

/**
 * Whether a URL points inside this network: localhost, a one-label name
 * ("ollama", "gpu-box"), or a loopback, private or link-local address.
 * Any dotted name counts as outside, matching the server's rule
 * (services/data_controls.host_is_internal) so the UI never calls a public
 * endpoint private.
 */
export function isPrivateUrl(url) {
  let host = ''
  try {
    const raw = String(url || '').trim()
    if (!raw) return false
    host = new URL(raw.includes('://') ? raw : `http://${raw}`).hostname.toLowerCase()
  } catch {
    return false
  }
  host = host.replace(/^\[|\]$/g, '').replace(/\.$/, '')
  if (!host) return false
  if (host === 'localhost' || host.endsWith('.localhost')) return true
  const v4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/)
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])]
    return a === 127 || a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 169 && b === 254)
  }
  if (host.includes(':')) {
    // IPv6: loopback, unique-local (fc00::/7) and link-local (fe80::/10).
    return host === '::1' || /^f[cd][0-9a-f]{0,2}:/.test(host) || /^fe[89ab][0-9a-f]?:/.test(host)
  }
  return !host.includes('.')
}

/**
 * Where a provider's models run, as the provider list groups them:
 * 'network' (this machine or one the user's organisation runs, so text
 * stays inside) or 'hosted' (a company's service). The deployment's own
 * provider follows what the server says about its endpoint; a custom
 * endpoint follows its URL, so LM Studio on localhost is 'network' and Groq
 * is 'hosted'.
 */
export function providerLocation(providerId) {
  if (isDeploymentProvider(providerId) && typeof _deploymentDefault.external === 'boolean') {
    return _deploymentDefault.external ? 'hosted' : 'network'
  }
  const cfg = getProviderConfig(providerId)
  if (cfg?.isCustom) return isPrivateUrl(cfg.baseUrl) ? 'network' : 'hosted'
  const def = PROVIDER_DEFS.find(d => d.id === providerId)
  if (def) return def.type === 'local' ? 'network' : 'hosted'
  if (providerId === 'openai_compatible' && isDeploymentProvider(providerId)) {
    return isPrivateUrl(_deploymentDefault.base_url) ? 'network' : 'hosted'
  }
  return 'hosted'
}

/** Whether a provider's models run on this network (see providerLocation). */
export function isLocalProvider(providerId) {
  return providerLocation(providerId) === 'network'
}

// ── Models for a job ───────────────────────────────────────────────────────
//
// Each picker shows the models that can do its job, using the capability
// record the server sends (services/model_catalog.py), never a model name:
//   answers    chat models
//   tools      chat models that call tools (deep search, agent mode)
//   vision     chat models that read images (scanned pages, figures)
//   embedding  embedding models (the search index)
// Models whose kind is not known are offered separately rather than hidden,
// so a new model is never unreachable; models for other jobs are counted,
// not listed.

const JOB_KIND = { answers: 'chat', tools: 'chat', vision: 'chat', embedding: 'embedding' }

/**
 * Split a model list for one job. Returns
 *   { recommended, fits, unknown, unable, otherJobs }
 * where `fits` are the rest of the models that do the job, `unknown` have
 * no known kind, `unable` are of the right kind but lack the ability the
 * job needs (a chat model that "cannot use tools"), and `otherJobs` counts
 * the remainder by kind ({ embedding: 2, speech: 1 }).
 */
export function modelsForJob(models, job = 'answers') {
  const kind = JOB_KIND[job] || 'chat'
  const out = { recommended: [], fits: [], unknown: [], unable: [], otherJobs: {} }
  for (const m of models || []) {
    const kinds = m.kinds || []
    if (!kinds.length) { out.unknown.push(m); continue }
    if (!kinds.includes(kind)) {
      for (const k of kinds) out.otherJobs[k] = (out.otherJobs[k] || 0) + 1
      continue
    }
    if ((job === 'tools' && m.tools === false) || (job === 'vision' && m.vision === false)) {
      out.unable.push(m)
      continue
    }
    if (job === 'vision' && m.vision !== true) { out.unknown.push(m); continue }
    if (m.recommended) out.recommended.push(m)
    else out.fits.push(m)
  }
  return out
}

const KIND_NAMES = {
  chat: 'chat', embedding: 'embedding', image: 'image', speech: 'speech', transcription: 'transcription',
  moderation: 'moderation', rerank: 'reranking', video: 'video', realtime: 'realtime', other: 'other',
}

/** "2 embedding, 1 speech" for the models a picker leaves out. */
export function describeOtherJobs(otherJobs) {
  return Object.entries(otherJobs || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `${n} ${KIND_NAMES[k] || k}`)
    .join(', ')
}

/**
 * What a chat model can do, in words, for the line under a picker:
 * ["Uses tools", "Reads images", "200,000-token context"]. Abilities nobody
 * reported are left out rather than guessed.
 */
export function modelAbilities(record) {
  if (!record) return []
  const out = []
  if (record.tools === true) out.push('Uses tools')
  else if (record.tools === false) out.push('Cannot use tools')
  if (record.vision === true) out.push('Reads images')
  if (record.context_tokens) out.push(`${Number(record.context_tokens).toLocaleString()}-token context`)
  return out
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
      model: ollamaModel || '',
      baseUrl: 'http://localhost:11434',
      available: false,
    })
    changed = true
  }

  // A retired provider's saved key reaches nothing; drop it so it is not
  // offered anywhere, and let the chain pick the next configured provider.
  const kept = configs.filter(c => !RETIRED_PROVIDER_IDS.includes(c.id))
  if (kept.length !== configs.length) changed = true

  if (changed) saveProvidersConfig(kept)
  if (RETIRED_PROVIDER_IDS.includes(getActiveProvider())) {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify({ ...getAISettings(), provider: '' }))
  }

  // Legacy 'chat_provider' (Chat's private pre-unification choice) becomes
  // the global default when none was ever set. Per-surface overrides are
  // retired (retireSurfaceOverrides), so one that disagrees is dropped.
  const legacyChat = localStorage.getItem('chat_provider')
  if (legacyChat !== null) {
    localStorage.removeItem('chat_provider')
    if (legacyChat && !getActiveProvider() && !RETIRED_PROVIDER_IDS.includes(legacyChat)) {
      setActiveProviderLS(legacyChat)
    }
  }
}
