// Ask's answer depth: Quick, or Deep search.
//
// Deep search was called Research before R9, and its value was 'research'.
// That spelling is still in browsers (the saved `chat_depth` choice, the
// `depth` of every saved answer) and the server still accepts it, so read
// it as 'deep' everywhere a depth comes in.

export const DEPTHS = ['quick', 'deep']

/** 'quick' or 'deep'; null for anything else. */
export function normalizeDepth(value) {
  if (value === 'research') return 'deep'
  return DEPTHS.includes(value) ? value : null
}

/** How an answer's depth is named under it. */
export function depthLabel(value) {
  return normalizeDepth(value) === 'quick' ? 'Quick' : 'Deep search'
}
