import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const rec = (id, kinds, extra = {}) => ({ id, label: id, kinds, recommended: false, roles: [], ...extra })
const RECORDS = [
  rec('chat-best', ['chat'], { label: 'Chat Best', recommended: true, roles: ['quality'], tools: true, vision: true, context_tokens: 200000 }),
  rec('chat-small', ['chat']),
  rec('embed-1', ['embedding']),
  rec('voice-1', ['speech']),
]
vi.mock('axios', () => ({ default: {
  get: vi.fn(() => Promise.resolve({ data: {} })),
  post: vi.fn(() => Promise.resolve({ data: { records: RECORDS, defaults: {}, listed: true, error: null } })),
} }))
vi.mock('../../utils/http', () => ({ default: { get: vi.fn(() => Promise.resolve({ data: { models: [] } })) } }))

import ModelPicker from '../ModelPicker.vue'
import AnswerModelSelect from '../AnswerModelSelect.vue'
import CollectionPicker from '../CollectionPicker.vue'
import { getProviderConfig, invalidateModelCache, setActiveProviderLS, upsertProviderConfig } from '../../utils/aiProviders'
import { useProviderStore } from '../../stores/providerStore'

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

describe('ModelPicker', () => {
  it('says nothing until the server has said what it provides', () => {
    // The deployment's own model is only known after loadServerProviders;
    // offering "Connect a model" before that flashed on every page load.
    expect(mount(ModelPicker).text()).not.toContain('Connect a model')
  })

  it('offers to connect a model when none is configured', async () => {
    useProviderStore().serverLoaded = true
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

describe('ModelPicker offers models that can answer', () => {
  it('lists chat models only, once opened by mouse or keyboard, and says what the chosen one can do', async () => {
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test', model: 'chat-best' })
    setActiveProviderLS('anthropic')
    const wrapper = mount(ModelPicker)
    await wrapper.get('label').trigger('focus')
    await flushPromises()

    const values = wrapper.findAll('#model-picker-model option').map(o => o.element.value)
    expect(values).toEqual(['', 'chat-best', 'chat-small'])
    expect(wrapper.get('[data-testid="model-badges"]').text()).toBe('Uses tools · Reads images · 200,000-token context')
  })
})

describe('AnswerModelSelect (Settings, per provider)', () => {
  it('counts the models it leaves out and keeps a saved model the provider no longer lists', () => {
    const models = RECORDS.map(r => ({ ...r, display: r.label }))
    const wrapper = mount(AnswerModelSelect, { props: { selectId: 's', models, modelValue: 'retired-model' } })
    expect(wrapper.findAll('option').map(o => o.element.value))
      .toEqual(['', 'retired-model', 'chat-best', 'chat-small', '__custom__'])
    expect(wrapper.get('[data-testid="model-other-jobs"]').text()).toContain('1 embedding, 1 speech')
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

describe('ModelPicker says why it cannot list models (B12)', () => {
  it('shows the reason with Try again, and a model can still be typed', async () => {
    invalidateModelCache()
    const axios = (await import('axios')).default
    axios.post.mockImplementationOnce(() => Promise.reject(Object.assign(new Error('Connection refused. Is the provider reachable?'), {})))
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })
    setActiveProviderLS('anthropic')
    const wrapper = mount(ModelPicker)
    await wrapper.get('label').trigger('focus')
    await flushPromises()
    const box = wrapper.get('[data-testid="model-list-error"]')
    expect(box.text()).toContain('Could not list models from')
    expect(box.text()).toContain('Connection refused')
    expect(box.text()).toContain('Try again')
    await box.get('input').setValue('llama3.1:8b')
    await box.get('form').trigger('submit')
    expect(getProviderConfig('anthropic').model).toBe('llama3.1:8b')
  })
})
