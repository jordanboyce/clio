// What this browser holds that belongs to a person rather than to the device:
// chat history, cached search results, notes, the source selection and the AI
// provider keys they typed in. None of it is scoped to an account, so when a
// different person signs in on the same browser it has to go - otherwise they
// would read the previous person's chats and spend their API keys.
//
// Device preferences (theme, panel widths, sort order) stay.

const EXACT_KEYS = [
  'clio_chat_history_v1',
  'clio_chat_history_v2',
  'clio_search_cache_v2',
  'clio_source_selection',
  'clio_current_collection',
  'clio_chat_model_overrides',
  'clio_search_model_overrides',
  'clio_selected_providers',
  'ai_providers_config',
  'ai_settings',
  'chat_provider',
  'ollama_model',
]

const PREFIXES = ['analysis_notes:', 'clio_provider_override_']

const LAST_USER_KEY = 'clio_last_user'

export function wipePersonalBrowserData() {
  try {
    for (const key of EXACT_KEYS) localStorage.removeItem(key)
    for (const key of Object.keys(localStorage)) {
      if (PREFIXES.some((p) => key.startsWith(p))) localStorage.removeItem(key)
    }
    sessionStorage.clear()
  } catch { /* storage unavailable: nothing to protect */ }
}

// Record who this browser's personal data belongs to. Returns true when it
// belonged to someone else and was just wiped, so the caller can reload and
// drop whatever the previous person left in memory.
export function adoptBrowserUser(email) {
  if (!email) return false
  try {
    const previous = localStorage.getItem(LAST_USER_KEY)
    localStorage.setItem(LAST_USER_KEY, email)
    if (previous && previous !== email) {
      wipePersonalBrowserData()
      return true
    }
  } catch { /* storage unavailable */ }
  return false
}
