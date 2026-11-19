import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../http', () => ({ default: { get: vi.fn() } }))
import http from '../http'
import { clearThisBrowser, identityHash, installStorageScope, prepareStorageScope, signedIn } from '../storageScope'

beforeEach(() => {
  installStorageScope(null)
  localStorage.clear()
  sessionStorage.clear()
})

describe('browser state per signed-in person', () => {
  it('keeps one person’s provider keys and history from the next', () => {
    installStorageScope('alice@example.com')
    localStorage.setItem('ai_providers_config', '[{"id":"openai","apiKey":"sk-alice"}]')
    localStorage.setItem('clio_chat_history_v2', '["alice asked"]')
    sessionStorage.setItem('clio_selection', 'a')

    installStorageScope('bob@example.com')
    expect(localStorage.getItem('ai_providers_config')).toBeNull()
    expect(localStorage.getItem('clio_chat_history_v2')).toBeNull()
    expect(sessionStorage.getItem('clio_selection')).toBeNull()

    installStorageScope('alice@example.com')
    expect(localStorage.getItem('clio_chat_history_v2')).toBe('["alice asked"]')
  })

  it('does not spell the identity out in key names', () => {
    installStorageScope('alice@example.com')
    localStorage.setItem('notes', 'x')

    const keys = Object.keys(localStorage)
    expect(keys).toEqual([`u:${identityHash('alice@example.com')}:notes`])
    expect(keys.join()).not.toContain('alice')
  })

  it('leaves storage as it was where nobody signs in', () => {
    localStorage.setItem('clio_chat_history_v2', '["mine"]')
    installStorageScope(null)

    expect(localStorage.getItem('clio_chat_history_v2')).toBe('["mine"]')
  })

  it('keeps the theme per device', () => {
    localStorage.setItem('theme', 'dark')
    installStorageScope('alice@example.com')

    expect(localStorage.getItem('theme')).toBe('dark')
  })

  it('scopes to who /api/me says is signed in', async () => {
    http.get.mockResolvedValue({ data: { identity: 'carol@example.com', sign_out_url: '/oauth2/sign_out' } })
    await prepareStorageScope()
    localStorage.setItem('k', 'v')

    expect(Object.keys(localStorage)[0]).toBe(`u:${identityHash('carol@example.com')}:k`)
    expect(signedIn.signOutUrl).toBe('/oauth2/sign_out')
  })

  it('clears every person’s state from this browser', () => {
    installStorageScope('alice@example.com')
    localStorage.setItem('a', '1')
    installStorageScope(null)
    localStorage.setItem('b', '2')
    sessionStorage.setItem('c', '3')

    clearThisBrowser()

    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
