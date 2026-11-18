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

  it('offers the four places and marks the recommendation', async () => {
    http.get.mockResolvedValueOnce({ data: options() })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    const cards = w.findAll('[role="radio"]')
    expect(cards).toHaveLength(4)
    expect(cards[2].text()).toContain('Our own embedding server')
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

  it('chooses a missing Ollama model in one call that pulls it, with progress to ready (B3)', async () => {
    http.get.mockResolvedValueOnce({ data: options({ recommended: 'ollama', ollama: { reachable: true, base_url: 'http://localhost:11434', embedding_models: [], suggested: 'nomic-embed-text' } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    expect(w.find('button.btn-primary').text()).toContain('Pull and use')
    http.post.mockResolvedValueOnce({ data: { saved: true, status: { status: 'downloading', provider: 'ollama', model: 'nomic-embed-text', progress: { percent: 40 } } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledTimes(1)
    expect(http.post.mock.calls[0][0]).toBe('/api/embedding/select')
    expect(http.post.mock.calls[0][1]).toMatchObject({ provider: 'ollama', model: 'nomic-embed-text', pull: true })
    expect(w.text()).toContain('Pulling the model into Ollama')
    expect(w.text()).toContain('40%')
    http.get.mockResolvedValueOnce({ data: { status: 'ready', provider: 'ollama', model: 'nomic-embed-text' } })
    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(w.emitted('done')).toBeTruthy()
  })

  it('never offers an installed Ollama model again as one to pull (B2)', async () => {
    http.get.mockResolvedValueOnce({ data: options({ recommended: 'ollama', ollama: { reachable: true, base_url: 'x', embedding_models: ['nomic-embed-text:latest'], suggested: 'nomic-embed-text' } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    const choices = w.get('[data-testid="ollama-model"]').findAll('option').map(o => o.text())
    expect(choices).toEqual(['nomic-embed-text:latest'])
    expect(w.find('button.btn-primary').text()).toBe('Use this')
  })

  it('says why On this server is unavailable without a click, with Check again (B1)', async () => {
    http.get.mockResolvedValueOnce({ data: options({ recommended: 'ollama', local: { ...options().local, huggingface_reachable: false, model_source: 'huggingface.co' } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    expect(w.get('[data-testid="embedding-card-local"]').attributes('disabled')).toBeDefined()
    const reason = w.get('[data-testid="local-unavailable-reason"]')
    expect(reason.text()).toContain('cannot reach huggingface.co')
    // The operator's detail is behind "How to fix this", not in the first line.
    expect(reason.find('p').text()).not.toContain('HF_ENDPOINT')
    http.get.mockResolvedValueOnce({ data: options() })
    await reason.findAll('button').find(b => b.text() === 'Check again').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="local-unavailable-reason"]').exists()).toBe(false)
  })

  it('says so when the model is missing instead of going quiet (B4)', async () => {
    http.get.mockResolvedValueOnce({ data: options({ offline_mode: true, local: { ...options().local, huggingface_reachable: null } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    http.post.mockResolvedValueOnce({ data: { saved: true, status: { status: 'loading' } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    http.get.mockResolvedValueOnce({ data: { status: 'missing', model: 'all-MiniLM-L6-v2', can_download: false } })
    vi.advanceTimersByTime(1000)
    await flushPromises()
    expect(w.get('[data-testid="embedding-missing"]').text()).toContain('this network cannot download it')
  })

  it('tries the own embedding server of the site before saving it (B5)', async () => {
    http.get.mockResolvedValueOnce({ data: options({ offline_mode: true, custom: { configured: false, model: '', base_url: '' } }) })
    const w = mount(EmbeddingSetup)
    await flushPromises()
    await w.get('[data-testid="embedding-card-custom"]').trigger('click')
    const inputs = w.get('[data-testid="custom-embedding"]').findAll('input')
    await inputs[0].setValue('http://tei.internal:8080/v1')
    await inputs[1].setValue('BAAI/bge-small-en-v1.5')
    expect(w.find('button.btn-primary').attributes('disabled')).toBeDefined()
    http.post.mockResolvedValueOnce({ data: { ok: true, model: 'BAAI/bge-small-en-v1.5', dimensions: 384, seconds: 0.4 } })
    await w.get('[data-testid="custom-test"]').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls[0]).toEqual(['/api/embedding/test', expect.objectContaining({
      embedding_provider: 'openai_compatible', embedding_base_url: 'http://tei.internal:8080/v1', remote_embedding_model: 'BAAI/bge-small-en-v1.5' })])
    expect(w.get('[data-testid="custom-result"]').text()).toContain('384-dimension')
    http.post.mockResolvedValueOnce({ data: { saved: true, status: { status: 'ready' } } })
    await w.find('button.btn-primary').trigger('click')
    await flushPromises()
    expect(http.post.mock.calls[1]).toEqual(['/api/embedding/select', expect.objectContaining({
      provider: 'openai_compatible', model: 'BAAI/bge-small-en-v1.5', base_url: 'http://tei.internal:8080/v1' })])
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
