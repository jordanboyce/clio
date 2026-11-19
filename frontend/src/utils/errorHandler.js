// C8: what the app does with an error nobody expected. Without this a
// component that throws while rendering, or a promise nobody awaited, failed
// silently: the screen stopped updating and nothing said why.
//
// One message at a time, with a reload offer, kept on screen until it is
// dismissed (it asks for a decision). The error goes to the console with the
// request id when the failure came from a server call, which is what an
// administrator needs to find it in the server log.

const MESSAGE = 'Something went wrong on this page. Reloading it usually fixes this.'

// Rejections that are someone's ordinary business: a cancelled request, the
// server being unreachable or a sign-in ending (each has its own banner),
// a rate limit (its own toast).
export function isExpected(reason) {
  if (!reason) return true
  if (reason.name === 'AbortError' || reason.code === 'ERR_CANCELED' || reason.__CANCEL__) return true
  if (reason.isNetwork || reason.isAuth || reason.isRateLimit || reason.isTimeout) return true
  return false
}

export function installErrorHandler(app, getUi, { log = console.error, reload = () => window.location.reload() } = {}) {
  let showing = null

  const report = (err, where) => {
    log('[clio] Unexpected error', { where, requestId: err?.requestId || null, error: err })
    let ui
    try { ui = getUi() } catch { return }
    if (showing && ui.toasts.some(t => t.id === showing)) return
    showing = ui.notify(MESSAGE, 'error', { duration: 0, action: { label: 'Reload', run: reload } })
  }

  app.config.errorHandler = (err, _instance, info) => report(err, info)

  const onRejection = (event) => {
    const reason = event.reason
    if (isExpected(reason)) return
    // A server error a caller did not handle: logged, but its message was
    // already the caller's to show, so only real exceptions get the toast.
    if (!(reason instanceof Error)) {
      log('[clio] Unhandled request error', { requestId: reason?.requestId || null, error: reason })
      return
    }
    report(reason, 'unhandled promise')
  }
  window.addEventListener('unhandledrejection', onRejection)
  return () => window.removeEventListener('unhandledrejection', onRejection)
}
