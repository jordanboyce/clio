import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// axios is imported dynamically inside the module under test.
const post = vi.fn()
vi.mock('axios', () => ({ default: { post, get: vi.fn(() => Promise.resolve({ data: {} })) } }))

import {
  buildProviderHeaders,
  fetchProviderModels,
  invalidateModelCache,
  toPickerModel,
  upsertProviderConfig,
} from '../aiProviders'

const record = (over) => ({
  id: 'x', label: 'x', kinds: ['chat'], vision: null, tools: null, context_tokens: null,
  embedding_dimensions: null, max_input_tokens: null, source: 'provider', pattern_fields: [],
  recommended: false, roles: [], ...over,
})

describe('model lists come from the server', () => {
  beforeEach(() => {
    localStorage.clear()
    post.mockReset()
    invalidateModelCache()
  })

  it('shows the server’s records, recommendations first, with abilities', async () => {
    post.mockResolvedValue({ data: {
      models: ['best-model', 'other-model'],
      records: [
        record({ id: 'best-model', label: 'Best Model', recommended: true, roles: ['quality', 'vision'], vision: true, tools: true }),
        record({ id: 'other-model', label: 'Other (display name)', vision: false, tools: true }),
      ],
      defaults: { model: 'best-model', fast: 'best-model', vision: 'best-model' },
      suggested: { embedding: null, vision: null },
      listed: true,
      error: null,
    } })
    upsertProviderConfig('anthropic', { apiKey: 'sk-ant-test' })

    const { models, defaults, listed, error } = await fetchProviderModels('anthropic')

    expect(error).toBeNull()
    expect(listed).toBe(true)
    expect(defaults).toEqual({ model: 'best-model', fast: 'best-model', vision: 'best-model' })
    expect(models.map(m => [m.id, m.label, m.recommended, m.display])).toEqual([
      ['best-model', 'Best Model (quality)', true, 'Best Model (quality) · vision · tools'],
      ['other-model', 'other-model', false, 'other-model · tools'],
    ])
    expect(post).toHaveBeenCalledWith('/api/ai/models', null, {
      headers: { 'X-AI-Provider': 'anthropic', 'X-AI-Key': 'sk-ant-test' },
    })
  })

  it('asks the server to skip its cache when forced', async () => {
    post.mockResolvedValue({ data: { records: [], models: [], defaults: {}, listed: true, error: null } })
    await fetchProviderModels('openai', { force: true })
    expect(post.mock.calls[0][2].params).toEqual({ refresh: true })
  })

  it('still works against a server that sends bare ids', async () => {
    post.mockResolvedValue({ data: { models: ['a', 'b'], error: null } })
    const { models, defaults } = await fetchProviderModels('openai_compatible_x', { baseUrl: 'http://h/v1' })
    expect(models.map(m => [m.id, m.label, m.recommended])).toEqual([['a', 'a', false], ['b', 'b', false]])
    expect(defaults).toEqual({ model: null, fast: null, vision: null })
  })

  it('keeps the server’s fallback list when the provider could not be asked', async () => {
    post.mockResolvedValue({ data: {
      models: [],
      records: [record({ id: 'rec', label: 'Rec', recommended: true, roles: ['fast'], source: 'pattern' })],
      defaults: { model: 'rec', fast: 'rec', vision: null },
      listed: false,
      error: 'offline',
    } })
    const { models, error, listed } = await fetchProviderModels('openai')
    expect(listed).toBe(false)
    expect(error).toBe('offline')
    expect(models.map(m => m.label)).toEqual(['Rec (fast)'])
  })

  it('reports a server that cannot be reached, with nothing invented', async () => {
    post.mockRejectedValue(new Error('Network Error'))
    const { models, error } = await fetchProviderModels('openai')
    expect(models).toEqual([])
    expect(error).toBe('Network Error')
  })

  it('names no Ollama model when none was chosen, so the server picks', () => {
    upsertProviderConfig('ollama', { model: '', baseUrl: 'http://localhost:11434', available: true })
    expect(buildProviderHeaders('ollama')).toEqual({})
  })

  it('labels a lone vision role', () => {
    expect(toPickerModel(record({ id: 'v', label: 'V', recommended: true, roles: ['vision'] })).label).toBe('V (vision)')
  })
})

// The frontend names no models: they live in services/model_catalog.py.
// mcpClients.js is exempt: its strings are sample code for other agent SDKs.
const MODEL_NAME = /\b(gpt-[0-9o]|gpt-oss|o[134]-mini|claude-(\d|opus|sonnet|haiku|fable)|gemini-\d|gemma\d|grok-\d|llama\d|llava|qwen\d|mistral-\w|deepseek-(chat|v\d|r\d)|nomic-embed|text-embedding-|all-MiniLM|bge-|mxbai|minicpm|moondream|phi\d|kimi-k)/i

function sourceFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    if (statSync(path).isDirectory()) return name === '__tests__' ? [] : sourceFiles(path)
    return /\.(vue|js)$/.test(name) ? [path] : []
  })
}

describe('the frontend holds no model names', () => {
  it('finds none in src/', () => {
    const src = join(__dirname, '..', '..')
    const hits = []
    for (const file of sourceFiles(src)) {
      const rel = relative(src, file).replace(/\\/g, '/')
      if (rel === 'utils/mcpClients.js') continue
      readFileSync(file, 'utf8').split('\n').forEach((line, i) => {
        const m = line.match(MODEL_NAME)
        if (m) hits.push(`${rel}:${i + 1}: ${m[0]}`)
      })
    }
    expect(hits).toEqual([])
  })
})
