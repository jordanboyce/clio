// D6: copying on a plain-HTTP intranet host. The Clipboard API exists only
// in a secure context (HTTPS or localhost); there, the older selection copy
// still works in most browsers; and when nothing copies, the text is shown
// selected for the person to copy by hand. The MCP token in particular is
// shown once, so failing to copy it must never mean losing it.

import { useUiStore } from '../stores/uiStore'

/** True when the text reached the clipboard. */
export async function writeClipboard(text) {
  // Browsers expose navigator.clipboard only in a secure context.
  if (typeof navigator !== 'undefined' && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch { /* fall through to the selection copy */ }
  }
  try {
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.top = '-1000px'
    document.body.appendChild(area)
    area.select()
    const ok = typeof document.execCommand === 'function' && document.execCommand('copy')
    area.remove()
    if (ok) return true
  } catch { /* nothing left but a manual copy */ }
  return false
}

/** Copy, or show the text for a manual copy. Resolves to true when it copied. */
export async function copyOrShow(text, label = 'Text') {
  if (await writeClipboard(text)) return true
  useUiStore().manualCopy = { label, text }
  return false
}
