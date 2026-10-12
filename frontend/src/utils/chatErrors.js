const PROVIDER_ERROR_PREFIX = 'Chat failed:'
const PROVIDER_ERROR_SUMMARY = "The AI provider couldn't complete this request. Check the selected model or endpoint, then retry."

export function presentChatError(message) {
  const text = String(message || '').trim() || 'Chat failed. Please try again.'
  if (!text.startsWith(PROVIDER_ERROR_PREFIX)) {
    return { message: text, detail: '' }
  }

  return {
    message: PROVIDER_ERROR_SUMMARY,
    detail: text.slice(PROVIDER_ERROR_PREFIX.length).trim(),
  }
}
