import { beforeEach, describe, expect, it } from 'vitest'
import { adoptBrowserUser, wipePersonalBrowserData } from '../personalData'

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

const seed = () => {
  localStorage.setItem('clio_chat_history_v2', '[{"q":"secret"}]')
  localStorage.setItem('clio_search_cache_v2', '{}')
  localStorage.setItem('ai_providers_config', '[{"apiKey":"sk-abc"}]')
  localStorage.setItem('analysis_notes:default', 'private note')
  localStorage.setItem('clio_provider_override_chat', 'openai')
  localStorage.setItem('theme', 'dracula')
  localStorage.setItem('sidebar_width', '320')
}

describe('personal browser data', () => {
  it('wipes chats, caches, notes and provider keys but keeps device preferences', () => {
    seed()
    wipePersonalBrowserData()
    expect(localStorage.getItem('clio_chat_history_v2')).toBeNull()
    expect(localStorage.getItem('clio_search_cache_v2')).toBeNull()
    expect(localStorage.getItem('ai_providers_config')).toBeNull()
    expect(localStorage.getItem('analysis_notes:default')).toBeNull()
    expect(localStorage.getItem('clio_provider_override_chat')).toBeNull()
    expect(localStorage.getItem('theme')).toBe('dracula')
    expect(localStorage.getItem('sidebar_width')).toBe('320')
  })

  it('keeps everything for the same person and for the first person to sign in', () => {
    seed()
    expect(adoptBrowserUser('a@example.com')).toBe(false)
    expect(adoptBrowserUser('a@example.com')).toBe(false)
    expect(localStorage.getItem('clio_chat_history_v2')).not.toBeNull()
  })

  it('wipes when a different person signs in on the same browser', () => {
    adoptBrowserUser('a@example.com')
    seed()
    expect(adoptBrowserUser('b@example.com')).toBe(true)
    expect(localStorage.getItem('clio_chat_history_v2')).toBeNull()
    expect(localStorage.getItem('ai_providers_config')).toBeNull()
    expect(localStorage.getItem('theme')).toBe('dracula')
    // And the new person now owns the browser's data.
    expect(adoptBrowserUser('b@example.com')).toBe(false)
  })
})
