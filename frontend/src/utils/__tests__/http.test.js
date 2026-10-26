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
