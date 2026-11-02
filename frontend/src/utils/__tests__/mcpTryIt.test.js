import { describe, expect, it } from 'vitest'
import { firstTaskPrompt } from '../mcpTryIt'
import { SAMPLE_COLLECTION_NAME } from '../sampleSources'

const sample = { id: 's1', name: SAMPLE_COLLECTION_NAME }

describe('firstTaskPrompt', () => {
  it('audits the sample register when the sample collection exists', () => {
    const p = firstTaskPrompt({ sampleCollection: sample, collection: null, codingClient: false })
    expect(p.text).toContain(`"${SAMPLE_COLLECTION_NAME}"`)
    expect(p.text).toContain('equipment register')
    expect(p.text).not.toContain('audit.md')
    expect(p.why).toBeTruthy()
  })

  it('asks a coding agent to write the result into the workspace', () => {
    const p = firstTaskPrompt({ sampleCollection: sample, collection: null, codingClient: true })
    expect(p.text).toMatch(/Write the result to audit\.md\.$/)
  })

  it('falls back to a tour of the collection the recipes point at', () => {
    const p = firstTaskPrompt({ sampleCollection: null, collection: { id: 'ops', name: 'Operations' }, codingClient: false })
    expect(p.text).toContain('"Operations"')
    expect(p.text).not.toContain('register')
  })
})
