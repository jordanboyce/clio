import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { copyOrShow } from '../clipboard'
import { useUiStore } from '../../stores/uiStore'

describe('copying on a plain-HTTP host (D6)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('shows the text for a manual copy when the browser will not copy', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    document.execCommand = vi.fn(() => false)
    const copied = await copyOrShow('clio_tok_123', 'MCP token')
    expect(copied).toBe(false)
    expect(useUiStore().manualCopy).toEqual({ label: 'MCP token', text: 'clio_tok_123' })
  })

  it('uses the selection copy where the Clipboard API is missing', async () => {
    Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
    document.execCommand = vi.fn(() => true)
    expect(await copyOrShow('hello', 'Answer')).toBe(true)
    expect(useUiStore().manualCopy).toBeNull()
  })
})
