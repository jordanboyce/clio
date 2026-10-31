import { nextTick, onBeforeUnmount, watch } from 'vue'

// Focus handling for overlays that are plain elements rather than a native
// <dialog> (the drawers and the acceptable-use takeover). They already say
// role="dialog" aria-modal="true"; this supplies what that promises and the
// browser does not do for a div: move focus into the panel when it opens,
// keep Tab inside it, close on Escape, and put focus back where it was.
// Dialogs that can use <dialog>.showModal() should use useModal() instead.
//
// Usage:
//   const panelRef = ref(null)
//   useOverlayFocus(isOpen, panelRef, { onEscape: close })
//   <div v-if="isOpen" ...><div ref="panelRef" tabindex="-1">…</div></div>
//
// `onEscape` may be omitted for an overlay the person must not dismiss.

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
  'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables(panel) {
  return Array.from(panel.querySelectorAll(FOCUSABLE)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  )
}

export function useOverlayFocus(isOpen, panelRef, { onEscape } = {}) {
  let previouslyFocused = null
  let listening = false

  function onKeydown(event) {
    const panel = panelRef.value
    if (!panel) return
    if (event.key === 'Escape') {
      if (onEscape) {
        event.preventDefault()
        onEscape()
      }
      return
    }
    if (event.key !== 'Tab') return
    const items = focusables(panel)
    if (items.length === 0) {
      event.preventDefault()
      panel.focus()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (event.shiftKey && (active === first || !panel.contains(active))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (active === last || !panel.contains(active))) {
      event.preventDefault()
      first.focus()
    }
  }

  function listen(on) {
    if (on === listening) return
    listening = on
    if (on) document.addEventListener('keydown', onKeydown, true)
    else document.removeEventListener('keydown', onKeydown, true)
  }

  async function opened() {
    previouslyFocused = document.activeElement
    listen(true)
    await nextTick()
    const panel = panelRef.value
    if (!panel) return
    const first = focusables(panel)[0]
    ;(first || panel).focus()
  }

  function closed() {
    listen(false)
    const target = previouslyFocused
    previouslyFocused = null
    if (target && typeof target.focus === 'function' && document.contains(target)) target.focus()
  }

  watch(isOpen, (open, was) => {
    if (open && !was) opened()
    else if (!open && was) closed()
  }, { immediate: true })

  onBeforeUnmount(() => listen(false))

  return { onKeydown }
}
