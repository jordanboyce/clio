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

describe('errors that say what happened (D2, D3)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  const answered = (status, data, statusText = '') => ({ config: {}, response: { status, statusText, data } })

  it('turns a 422 field list into "field: message"', async () => {
    const err = answered(422, { detail: [
      { loc: ['body', 'chunk_size'], msg: 'Input should be greater than 0' },
      { loc: ['query', 'collection_id'], msg: 'Field required' },
    ] }, 'Unprocessable Entity')
    await expect(reject(err)).rejects.toMatchObject({
      status: 422, message: 'chunk size: Input should be greater than 0; collection id: Field required',
    })
  })

  it("keeps Clio's own 502 sentence and does not call it an outage", async () => {
    const err = answered(502, { detail: 'A service this needed did not answer as expected. Retrying is safe.', code: 'upstream_error' })
    await expect(reject(err)).rejects.toMatchObject({ status: 502, message: expect.stringContaining('Retrying is safe') })
    expect(useUiStore().offline).toBe(false)
  })

  it("calls a proxy's HTML 502 an outage, in a sentence, not \"HTTP 502\"", async () => {
    const err = answered(502, '<html><body>Bad Gateway</body></html>', 'Bad Gateway')
    await expect(reject(err)).rejects.toMatchObject({ isNetwork: true, message: expect.stringContaining('Retrying is safe') })
    expect(useUiStore().offline).toBe(true)
  })

  it('a 401 asks once for a sign-in', async () => {
    await expect(reject(answered(401, { detail: 'Not authenticated' }))).rejects.toMatchObject({ isAuth: true })
    expect(useUiStore().authRequired).toBe(true)
  })
})
