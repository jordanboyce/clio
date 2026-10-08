import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
import http from '../../utils/http'
import EmbeddingSetup from '../EmbeddingSetup.vue'

const options = (over = {}) => ({
  current: { provider: 'local', model: 'all-MiniLM-L6-v2', backend: 'fastembed' },
  offline_mode: false,
  recommended: 'local',
  local: { available: true, backend: 'fastembed', model: 'all-MiniLM-L6-v2', model_label: 'MiniLM L6', size_mb: 90, cached: false, huggingface_reachable: true, install_hint: null },
  ollama: { reachable: false, base_url: 'http://localhost:11434', embedding_models: [], suggested: 'nomic-embed-text' },
  hosted: [{ id: 'openai', label: 'OpenAI', key_configured: true, default_model: 'text-embedding-3-small', blocked_offline: false }],
  ...over,
})

describe('EmbeddingSetup', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    http.get.mockReset()
    http.post.mockReset()
  })

  it('offers the three places and marks the recommendation', async () => {
    http.get.mockResolvedValueOnce({ data: options() })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    const cards = w.findAll('[role="radio"]')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('On this server')
    expect(cards[0].text()).toContain('Recommended')
    expect(cards[1].attributes('disabled')).toBeDefined() // Ollama not reachable
    expect(w.text()).toContain('90 MB')
  })

  it('selects local, warms, polls to ready and emits done', async () => {
    http.get.mockResolvedValueOnce({ data: options() })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    http.post.mockResolvedValueOnce({ data: { saved: true, needs_reindex: false, status: { status: 'downloading', model: 'all-MiniLM-L6-v2', progress: { percent: 10 } } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/embedding/select', expect.objectContaining({ provider: 'local', model: 'all-MiniLM-L6-v2', warm: true }))
    expect(w.text()).toContain('Downloading the search model')
    expect(w.text()).toContain('10%')
    http.get.mockResolvedValueOnce({ data: { status: 'ready', model: 'all-MiniLM-L6-v2' } })
    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(w.emitted('done')).toBeTruthy()
    expect(w.emitted('done')[0][0].status).toBe('ready')
  })

  it('pulls a missing Ollama model before selecting it', async () => {
    http.get.mockResolvedValueOnce({ data: options({ recommended: 'ollama', ollama: { reachable: true, base_url: 'http://localhost:11434', embedding_models: [], suggested: 'nomic-embed-text' } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    expect(w.find('button.btn-primary').text()).toContain('Pull and use')
    http.post.mockResolvedValueOnce({ data: { status: 'downloading' } })
    http.post.mockResolvedValueOnce({ data: { saved: true, status: { status: 'ready', model: 'nomic-embed-text' } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls[0][0]).toBe('/api/embedding/ollama/pull')
    expect(http.post.mock.calls[1][0]).toBe('/api/embedding/select')
    expect(http.post.mock.calls[1][1].provider).toBe('ollama')
    expect(w.emitted('done')).toBeTruthy()
  })

  it('asks for a key for a hosted provider without one and sends it', async () => {
    http.get.mockResolvedValueOnce({ data: options({ recommended: 'hosted', hosted: [{ id: 'voyage', label: 'Voyage', key_configured: false, default_model: 'voyage-3.5-lite', blocked_offline: false }] }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    expect(w.find('button.btn-primary').attributes('disabled')).toBeDefined()
    await w.find('input[type="password"]').setValue('pa-123')
    expect(w.find('button.btn-primary').attributes('disabled')).toBeUndefined()
    http.post.mockResolvedValueOnce({ data: { saved: true, status: { status: 'ready' } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/embedding/select', expect.objectContaining({ provider: 'voyage', api_key: 'pa-123' }))
  })

  it('skips through "Decide later"', async () => {
    http.get.mockResolvedValueOnce({ data: options() })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    await w.find('button.btn-ghost').trigger('click')
    expect(w.emitted('skip')).toBeTruthy()
  })
})
