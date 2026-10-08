import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('axios', () => ({ default: { get: vi.fn(() => Promise.resolve({ data: {} })) } }))
vi.mock('../../utils/http', () => ({ default: { get: vi.fn(() => Promise.resolve({ data: { models: [] } })) } }))

import ModelPicker from '../ModelPicker.vue'
import CollectionPicker from '../CollectionPicker.vue'
import { getProviderConfig, setActiveProviderLS, upsertProviderConfig } from '../../utils/aiProviders'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('ModelPicker', () => {
  it('offers to connect a model when none is configured', async () => {
    const wrapper = mount(ModelPicker)
    expect(wrapper.text()).toContain('Connect a model')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('connect')).toHaveLength(1)
  })

  it('switching provider changes the one global choice', async () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test', model: 'claude-x' })
    upsertProviderConfig('openai', { apiKey: 'sk-test' })
    setActiveProviderLS('anthropic')
    const wrapper = mount(ModelPicker)

    expect(wrapper.get('label').text()).toContain('Anthropic')
    expect(wrapper.get('label').text()).toContain('claude-x')

    const radios = wrapper.findAll('[role="radio"]')
    expect(radios).toHaveLength(2)
    await radios.find(r => r.text().includes('OpenAI')).trigger('click')
    expect(wrapper.get('label').text()).toContain('OpenAI')
    expect(JSON.parse(localStorage.getItem('ai_settings')).provider).toBe('openai')
  })

  it('changing the model writes it to the provider config', async () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test', model: 'claude-x' })
    setActiveProviderLS('anthropic')
    const wrapper = mount(ModelPicker)
    const select = wrapper.get('select')
    // The saved model stays selectable even when the live list lacks it.
    expect(select.element.value).toBe('claude-x')
    await select.setValue('')
    expect(getProviderConfig('anthropic').model).toBe('')
    expect(wrapper.get('label').text()).not.toContain('claude-x')
  })
})

describe('CollectionPicker', () => {
  const collections = [
    { id: 'a', name: 'Alpha', color: '#111', document_count: 3 },
    { id: 'b', name: 'Beta', color: '#222', document_count: 0, shared: true },
  ]

  it('switches collection and reports overview and create requests', async () => {
    const wrapper = mount(CollectionPicker, { props: { collections, current: collections[0] } })
    expect(wrapper.get('label').text()).toContain('Alpha')
    await wrapper.findAll('[role="option"]')[1].trigger('click')
    expect(wrapper.emitted('select')[0]).toEqual(['b'])
    const footer = wrapper.findAll('button').filter(b => ['All collections', 'New'].includes(b.text()))
    await footer[0].trigger('click')
    await footer[1].trigger('click')
    expect(wrapper.emitted('overview')).toHaveLength(1)
    expect(wrapper.emitted('create')).toHaveLength(1)
  })

  it('only shows a filter once the list is long enough to need one', async () => {
    expect(mount(CollectionPicker, { props: { collections } }).find('input').exists()).toBe(false)
    const many = Array.from({ length: 9 }, (_, i) => ({ id: `c${i}`, name: i === 4 ? 'Needle' : `Col ${i}`, color: '#000' }))
    const wrapper = mount(CollectionPicker, { props: { collections: many } })
    await wrapper.get('input').setValue('needle')
    const shown = wrapper.findAll('[role="option"]')
    expect(shown).toHaveLength(1)
    expect(shown[0].text()).toContain('Needle')
  })
})
