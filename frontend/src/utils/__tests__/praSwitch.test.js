import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { isPraDoc, praTargetFor, setPraEnabled } from '../praFormat'
import { useUiStore } from '../../stores/uiStore'

// PRA models are off unless the server says ENABLE_PRA (GET /api/config):
// then nothing offers to open a model, and the viewer does not open.
const MODEL = { document_id: 'd1', filename: 'pumps.xml', source_format: 'pra-mef', collection_id: 'c1' }

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => setPraEnabled(true))

describe('the PRA switch', () => {
  it('on, a model is a model and opens', () => {
    setPraEnabled(true)
    const ui = useUiStore()

    expect(isPraDoc(MODEL)).toBe(true)
    ui.openPra(praTargetFor(MODEL))
    expect(ui.praViewer?.documentId).toBe('d1')
  })

  it('off, nothing offers it and the viewer stays shut', () => {
    setPraEnabled(false)
    const ui = useUiStore()

    expect(isPraDoc(MODEL)).toBe(false)
    expect(praTargetFor(MODEL)).toBeNull()
    ui.openPra({ documentId: 'd1', collectionId: 'c1' })
    expect(ui.praViewer).toBeNull()
  })
})
