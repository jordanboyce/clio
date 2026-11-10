import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import http from '../http'
import { useUiStore } from '../../stores/uiStore'

// The interceptor's rejection handler, called the way axios would call it.
const reject = (error) => http.interceptors.response.handlers[0].rejected(error)

const rateLimited = (config = {}) => ({
  config,
  response: {
    status: 429,
    statusText: 'Too Many Requests',
    data: { detail: 'Rate limit reached. Try again in 2s.', retry_after_seconds: 2 },
  },
})

describe('429 handling', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('tells the person when something they did was rate limited', async () => {
    await expect(reject(rateLimited())).rejects.toMatchObject({ isRateLimit: true, retryAfterSeconds: 2 })
    expect(useUiStore().toasts.map((t) => t.message)).toEqual(['Rate limit reached. Try again in 2s.'])
  })

  it('stays silent for a background request marked quiet', async () => {
    // Suggested questions and status reads degrade on their own; a toast on
    // page load alarmed people who had done nothing.
    await expect(reject(rateLimited({ quiet: true }))).rejects.toMatchObject({ isRateLimit: true })
    expect(useUiStore().toasts).toEqual([])
  })
})

describe('timeouts versus outages', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  // What axios hands the interceptor: no response in either case.
  const timedOut = (code) => ({ code, message: 'timeout of 30000ms exceeded', config: { quiet: true } })
  const networkDown = () => ({ code: 'ERR_NETWORK', message: 'Network Error', config: {} })

  it.each(['ECONNABORTED', 'ETIMEDOUT'])('a %s timeout does not claim the server is down', async (code) => {
    // A slow model writing starter questions ran past 30s on a healthy
    // backend and put up the offline banner.
    await expect(reject(timedOut(code))).rejects.toMatchObject({
      isTimeout: true,
      isNetwork: false,
      message: 'The server took too long to answer this request.',
    })
    expect(useUiStore().offline).toBe(false)
  })

  it('a timeout leaves an existing outage flag alone', async () => {
    const ui = useUiStore()
    ui.offline = true
    await expect(reject(timedOut('ECONNABORTED'))).rejects.toMatchObject({ isTimeout: true })
    expect(ui.offline).toBe(true)
  })

  it('a real network failure still shows the server as unreachable', async () => {
    await expect(reject(networkDown())).rejects.toMatchObject({ isNetwork: true, status: 0 })
    expect(useUiStore().offline).toBe(true)
  })

  it.each([502, 503, 504])('a %i from the gateway still shows the server as unreachable', async (status) => {
    await expect(reject({ config: {}, response: { status, statusText: 'Bad Gateway', data: {} } }))
      .rejects.toMatchObject({ isNetwork: true, status })
    expect(useUiStore().offline).toBe(true)
  })
})
