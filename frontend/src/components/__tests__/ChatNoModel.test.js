import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => {
  const http = { get: vi.fn(() => new Promise(() => {})), post: vi.fn() }
  return { default: http, http }
})

import http from '../../utils/http'
import ChatTab from '../ChatTab.vue'
import { useCollectionStore } from '../../stores/collectionStore'
import { useStatsStore } from '../../stores/statsStore'
import { useProviderStore } from '../../stores/providerStore'
import { useSearchStore } from '../../stores/searchStore'

// F10: with no model connected, a typed question must still go somewhere on
// every device. The button and Enter both run it as a Find search; on touch
// keyboards (Enter adds a line there) the button is the only way, so it must
// not be disabled.
describe('Asking with no model connected', () => {
  let coarse = false

  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    setActivePinia(createPinia())
    useCollectionStore().collections = [{ id: 'default', name: 'Operations' }]
    useStatsStore().documents = 3
    const providers = useProviderStore()
    providers.loadServerProviders = vi.fn(async () => { providers.serverLoaded = true })
    Element.prototype.scrollIntoView = vi.fn()
    coarse = false
    window.matchMedia = vi.fn((query) => ({ matches: query === '(pointer: coarse)' && coarse }))
    http.post.mockReset()
    http.post.mockResolvedValue({ data: { questions: [] } })
  })

  const mountChat = async () => {
    const wrapper = mount(ChatTab, { global: { stubs: { AISettingsDrawer: true, SlashCommandPicker: true } } })
    await flushPromises()
    return wrapper
  }

  it('offers an enabled Find passages button instead of a disabled Send', async () => {
    const wrapper = await mountChat()
    await wrapper.get('textarea').setValue('Who signs off a laptop return?')
    const send = wrapper.get('[data-testid="chat-send"]')
    expect(send.text()).toBe('Find passages')
    expect(send.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('[aria-label="Answer depth"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('to find passages')
  })

  it('runs the question as a Find search from the button on a touch screen', async () => {
    coarse = true
    const wrapper = await mountChat()
    const input = wrapper.get('textarea')
    await input.setValue('Who signs off a laptop return?')

    // Enter adds a line on a phone keyboard; it does not send.
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('switch-tab')).toBeUndefined()

    await wrapper.get('[data-testid="chat-send"]').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('switch-tab')).toEqual([['search']])
    expect(useSearchStore().query).toBe('Who signs off a laptop return?')
    expect(useSearchStore().runOnOpen).toBe(true)
  })

  it('does the same from Enter on a physical keyboard', async () => {
    const wrapper = await mountChat()
    const input = wrapper.get('textarea')
    await input.setValue('Who signs off a laptop return?')
    await input.trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(wrapper.emitted('switch-tab')).toEqual([['search']])
    expect(useSearchStore().query).toBe('Who signs off a laptop return?')
  })

  it('keeps the plain Send arrow once a model is connected', async () => {
    localStorage.setItem('ai_providers_config', JSON.stringify([{ id: 'anthropic', apiKey: 'k' }]))
    const wrapper = await mountChat()
    await wrapper.get('textarea').setValue('Who signs off a laptop return?')
    const send = wrapper.get('[data-testid="chat-send"]')
    expect(send.attributes('aria-label')).toBe('Send message')
    expect(send.text()).toBe('')
    expect(wrapper.find('[aria-label="Answer depth"]').exists()).toBe(true)
  })

  it('still refuses to send into a collection with no sources', async () => {
    useStatsStore().documents = 0
    const wrapper = await mountChat()
    await wrapper.get('textarea').setValue('Anything?')
    expect(wrapper.get('[data-testid="chat-send"]').attributes('disabled')).toBeDefined()
  })
})
