import { describe, it, expect } from 'vitest'
import { defineComponent, h, nextTick, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { useOverlayFocus } from '../useOverlayFocus'

// jsdom has no layout, so offsetParent is always null; make the filter see
// the buttons as visible.
Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
  configurable: true,
  get() { return this.parentNode },
})

const Overlay = defineComponent({
  props: { onEscape: { type: Function, default: undefined } },
  setup(props, { expose }) {
    const open = ref(false)
    const panel = ref(null)
    useOverlayFocus(open, panel, { onEscape: props.onEscape })
    expose({ open })
    return () => h('div', [
      h('button', { id: 'outside' }, 'outside'),
      open.value
        ? h('div', { role: 'dialog' }, [
            h('div', { ref: panel, tabindex: '-1', id: 'panel' }, [
              h('button', { id: 'first' }, 'first'),
              h('button', { id: 'last' }, 'last'),
            ]),
          ])
        : null,
    ])
  },
})

async function openOverlay(props = {}) {
  const wrapper = mount(Overlay, { props, attachTo: document.body })
  document.getElementById('outside').focus()
  wrapper.vm.open = true
  await nextTick()
  await nextTick()
  return wrapper
}

const key = (k, extra = {}) =>
  document.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra }))

describe('useOverlayFocus', () => {
  it('moves focus into the panel on open and back on close', async () => {
    const wrapper = await openOverlay()
    expect(document.activeElement.id).toBe('first')
    wrapper.vm.open = false
    await nextTick()
    expect(document.activeElement.id).toBe('outside')
    wrapper.unmount()
  })

  it('keeps Tab inside the panel', async () => {
    const wrapper = await openOverlay()
    document.getElementById('last').focus()
    key('Tab')
    expect(document.activeElement.id).toBe('first')
    key('Tab', { shiftKey: true })
    expect(document.activeElement.id).toBe('last')
    wrapper.unmount()
  })

  it('calls onEscape only when one is given', async () => {
    let closed = 0
    const wrapper = await openOverlay({ onEscape: () => { closed += 1 } })
    key('Escape')
    expect(closed).toBe(1)
    wrapper.unmount()

    const required = await openOverlay()
    key('Escape')
    expect(required.vm.open).toBe(true)
    required.unmount()
  })
})
