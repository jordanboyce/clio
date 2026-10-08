// Keyboard shortcut vocabulary shared by the app shell, the command palette
// and the shortcuts sheet. One table so the help always matches the handler.

export const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')

// The modifier that opens the palette etc. Rendered as a glyph on Mac and a
// word everywhere else, matching what the keycap actually says.
export const MOD = isMac ? '⌘' : 'Ctrl'

// True while the caret is in something that takes text — single-key
// shortcuts must never fire there.
export function isTypingTarget(el) {
  if (!(el instanceof HTMLElement)) return false
  if (el.isContentEditable) return true
  const tag = el.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (el.getAttribute('type') || 'text').toLowerCase()
    return !['checkbox', 'radio', 'button', 'submit', 'range', 'color', 'file'].includes(type)
  }
  return false
}

// True when the event carries the platform's primary modifier and nothing
// else that would make it a different chord.
export function isMod(e) {
  return (isMac ? e.metaKey : e.ctrlKey) && !e.altKey
}

// Everything the shell binds, in the order the help sheet lists them.
// `keys` is display-only; the handler in App.vue is the source of truth for
// behaviour, and this table is what it reads to stay honest.
export const SHORTCUT_GROUPS = [
  {
    title: 'Everywhere',
    items: [
      { keys: [MOD, 'K'], label: 'Command palette' },
      { keys: ['?'], label: 'Keyboard shortcuts' },
      { keys: ['Esc'], label: 'Close palette, menu or dialog' },
    ],
  },
  {
    title: 'Go to',
    items: [
      { keys: ['G', 'A'], label: 'Ask', seq: true },
      { keys: ['G', 'F'], label: 'Find', seq: true },
      { keys: ['G', 'C'], label: 'Connect', seq: true },
      { keys: ['G', 'O'], label: 'Collections', seq: true },
      { keys: ['G', 'S'], label: 'Settings', seq: true },
    ],
  },
  {
    title: 'Ask',
    items: [
      { keys: [MOD, 'J'], label: 'New chat' },
      { keys: ['Enter'], label: 'Send' },
      { keys: ['Shift', 'Enter'], label: 'New line' },
      { keys: ['/'], label: 'Slash commands (at the start of a message)' },
    ],
  },
  {
    title: 'Panels and sources',
    items: [
      { keys: [MOD, '\\'], label: 'Toggle sources panel' },
      { keys: [MOD, '.'], label: 'Toggle notes and tools' },
      { keys: [MOD, 'U'], label: 'Add sources' },
      { keys: ['/'], label: 'Filter sources (when the panel has focus)' },
    ],
  },
]
