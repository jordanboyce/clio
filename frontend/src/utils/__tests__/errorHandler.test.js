import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createApp, defineComponent, h, nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { installErrorHandler, isExpected } from '../errorHandler'
import { useUiStore } from '../../stores/uiStore'

// C8: an error nobody expected says so, offers a reload, and stays on
// screen; the console gets the request id.
describe('the top-level error handler', () => {
  let uninstall = () => {}
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => uninstall())

  it('shows one message with Reload for a component that throws', async () => {
    const log = vi.fn()
    const reload = vi.fn()
    const Broken = defineComponent({ setup: () => () => { throw new Error('render failed') } })
    const app = createApp({ render: () => h(Broken) })
    uninstall = installErrorHandler(app, () => useUiStore(), { log, reload })
    app.mount(document.createElement('div'))
    await nextTick()

    const toasts = useUiStore().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0].message).toContain('Reloading it usually fixes this')
    toasts[0].action.run()
    expect(reload).toHaveBeenCalled()
    expect(log).toHaveBeenCalled()
  })

  it('does not pile up messages, and they do not time out', () => {
    vi.useFakeTimers()
    const app = createApp({ render: () => null })
    uninstall = installErrorHandler(app, () => useUiStore(), { log: () => {} })
    app.config.errorHandler(new Error('one'), null, 'x')
    app.config.errorHandler(new Error('two'), null, 'x')
    vi.advanceTimersByTime(60_000)
    expect(useUiStore().toasts).toHaveLength(1)
    vi.useRealTimers()
  })

  it('logs a request error a caller left unhandled with its request id', () => {
    const log = vi.fn()
    uninstall = installErrorHandler(createApp({ render: () => null }), () => useUiStore(), { log })
    window.dispatchEvent(Object.assign(new Event('unhandledrejection'), {
      reason: { status: 500, message: 'Internal error', requestId: 'req-42' },
    }))
    expect(log.mock.calls[0][1].requestId).toBe('req-42')
    expect(useUiStore().toasts).toHaveLength(0)
  })

  it('leaves alone what already has its own message', () => {
    expect(isExpected({ name: 'AbortError' })).toBe(true)
    expect(isExpected({ isNetwork: true })).toBe(true)
    expect(isExpected({ isAuth: true })).toBe(true)
    expect(isExpected(new Error('x'))).toBe(false)
  })
})
