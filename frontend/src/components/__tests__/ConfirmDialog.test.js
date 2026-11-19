import { beforeEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ConfirmDialog from '../ConfirmDialog.vue'
import { useUiStore } from '../../stores/uiStore'

describe('one confirm dialog in place of the native one (D5)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    HTMLDialogElement.prototype.showModal = function () { this.open = true }
    HTMLDialogElement.prototype.close = function () { this.open = false; this.dispatchEvent(new Event('close')) }
  })

  it('resolves true on confirm and false on cancel or Escape', async () => {
    const ui = useUiStore()
    const w = mount(ConfirmDialog, { attachTo: document.body })
    const first = ui.confirm({ title: 'Revoke laptop?', message: 'Agents lose access.', confirmLabel: 'Revoke', danger: true })
    await flushPromises()
    expect(w.get('[data-testid="confirm-dialog"]').element.open).toBe(true)
    expect(w.text()).toContain('Revoke laptop?')
    expect(w.get('[data-testid="confirm-ok"]').classes()).toContain('btn-error')
    await w.get('[data-testid="confirm-ok"]').trigger('click')
    await expect(first).resolves.toBe(true)

    const second = ui.confirm({ title: 'Clear this chat?' })
    await flushPromises()
    w.get('[data-testid="confirm-dialog"]').element.close()   // Escape closes a native dialog
    await expect(second).resolves.toBe(false)
    w.unmount()
  })
})
