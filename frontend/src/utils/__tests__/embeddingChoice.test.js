import { describe, expect, it } from 'vitest'
import { ollamaBaseName, sameOllamaModel } from '../embeddingChoice'
import {
  approxTokens,
  chunkFit,
  describeMinutes,
  embeddingModelFacts,
  estimateEmbedMinutes,
  switchCostSentence,
} from '../embeddingChoice'

describe('do passages fit the model', () => {
  it('compares characters with tokens at about four characters a token', () => {
    expect(approxTokens(600)).toBe(150)
    expect(chunkFit(600, 256)).toEqual({ fits: true, passageTokens: 150, maxInputTokens: 256 })
    expect(chunkFit(1200, 256).fits).toBe(false)
    expect(chunkFit(600, 128).fits).toBe(false)
  })

  it('never warns when the limit is not known', () => {
    expect(chunkFit(4000, null).fits).toBe(true)
    expect(chunkFit(4000, 0).fits).toBe(true)
  })
})

describe('what a model is', () => {
  it('states dimensions, input limit, language and where it runs', () => {
    const local = { id: 'local', label: 'Built in', hosted: false }
    const model = { dimensions: 384, max_input_tokens: 256, language: 'English', adds_prefixes: true }
    expect(embeddingModelFacts(model, local)).toEqual([
      '384 dimensions', 'reads up to 256 tokens', 'English', 'on this server', 'search prefixes added for you',
    ])
    expect(embeddingModelFacts({ dimensions: 3072 }, { id: 'google', label: 'Google Gemini', hosted: true }))
      .toEqual(['3,072 dimensions', 'hosted by Google Gemini'])
    expect(embeddingModelFacts(null, local)).toEqual([])
  })
})

describe('what a switch costs', () => {
  it('estimates from the real passage count and the kind of model', () => {
    expect(estimateEmbedMinutes(0)).toBe(0)
    expect(estimateEmbedMinutes(100, { kind: 'hosted' })).toBe(1)
    expect(estimateEmbedMinutes(38200, { kind: 'local', dimensions: 384 })).toBe(16)
    expect(estimateEmbedMinutes(38200, { kind: 'local', dimensions: 1024 })).toBe(160)
    expect(describeMinutes(16)).toBe('about 16 minutes')
    expect(describeMinutes(160)).toBe('about 3 hours')
    expect(describeMinutes(0)).toBe('under a minute')
  })

  it('says it plainly, and says so when nothing is indexed', () => {
    expect(switchCostSentence(null)).toBe('')
    expect(switchCostSentence({ collections: 0, sources: 0, passages: 0 }))
      .toBe('Nothing is indexed yet, so this change costs nothing.')
    const s = switchCostSentence({ collections: 3, sources: 1240, passages: 38200 }, { kind: 'local', dimensions: 384 })
    expect(s).toContain('re-indexing 1,240 sources in 3 collections (38,200 passages)')
    expect(s).toContain('about 16 minutes')
    const one = switchCostSentence({ collections: 1, sources: 1, passages: 4 }, { kind: 'hosted' })
    expect(one).toContain('re-indexing 1 source (4 passages)')
  })
})
