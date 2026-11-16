import { describe, expect, it } from 'vitest'
import { tabAfterCollectionSwitch } from '../collectionSwitch.js'

// F11: switching collection used to drop you back to Ask from Research (and
// every tab but four). Now only the collections overview moves you.
describe('the tab after switching collection', () => {
  it('stays on the tab you are working in', () => {
    for (const tab of ['chat', 'search', 'research', 'generate', 'expertise', 'mcp', 'admin', 'settings']) {
      expect(tabAfterCollectionSwitch(tab)).toBe(tab)
    }
  })

  it('opens the picked collection from the overview, on Find when Ask is off', () => {
    expect(tabAfterCollectionSwitch('collections')).toBe('chat')
    expect(tabAfterCollectionSwitch('collections', { chatEnabled: false })).toBe('search')
  })
})
