import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() } }))
import http from '../../utils/http'
import SupersedeNote from '../SupersedeNote.vue'
import SupersedeSuggestions from '../SupersedeSuggestions.vue'
import SupersedeDialog from '../SupersedeDialog.vue'
import { replacedBadge, supersedeNotes, suggestionKey } from '../../utils/supersede'

const suggestion = (over = {}) => ({
  document_id: 'old1', filename: 'Policy v2.pdf', replaced_by: 'new1',
  replaced_by_filename: 'Policy v3.pdf', reason: 'version_in_name', confidence: 'high', ...over,
})

describe('supersede presentation helpers', () => {
  it('badges only documents that have a replacement', () => {
    expect(replacedBadge({ filename: 'a' })).toBeNull()
    const b = replacedBadge({ superseded_by: 'n', superseded_by_filename: 'Policy v3.pdf', superseded_note: 'new limits' })
    expect(b.text).toBe('Replaced')
    expect(b.name).toBe('Policy v3.pdf')
    expect(b.title).toContain('Policy v3.pdf')
    expect(b.title).toContain('new limits')
  })

  it('words older-version and duplicate notes', () => {
    const n = supersedeNotes({
      superseded_by: { document_id: 'n', filename: 'Policy v3.pdf' },
      duplicates: [{ filename: 'a.pdf' }, { filename: 'b.pdf' }], duplicate_count: 4,
    })
    expect(n.older).toBe('Older version — replaced by Policy v3.pdf')
    expect(n.also).toBe('Also in 4 other files')
    expect(n.alsoTitle).toBe('a.pdf, b.pdf and 2 more')
    expect(supersedeNotes({ duplicates: [{ filename: 'a.pdf' }] }).also).toBe('Also in 1 other file')
    const none = supersedeNotes({})
    expect(none.older).toBeNull()
    expect(none.also).toBeNull()
  })
})

describe('SupersedeNote', () => {
  it('renders nothing for an ordinary passage', () => {
    const w = mount(SupersedeNote, { props: { source: { filename: 'a.pdf' } } })
    expect(w.find('[data-testid="supersede-note"]').exists()).toBe(false)
  })

  it('says it is an older version and where else the text is', () => {
    const w = mount(SupersedeNote, {
      props: { source: { superseded_by: { document_id: 'n', filename: 'v3.pdf' }, duplicates: [{ filename: 'x.pdf' }], duplicate_count: 1 } },
    })
    expect(w.get('[data-testid="older-version"]').text()).toContain('replaced by v3.pdf')
    expect(w.get('[data-testid="also-in"]').text()).toContain('Also in 1 other file')
  })
})

describe('SupersedeSuggestions', () => {
  beforeEach(() => {
    http.get.mockReset()
    http.post.mockReset()
    localStorage.clear()
  })

  async function mountList(suggestions) {
    http.get.mockResolvedValueOnce({ data: { suggestions } })
    const w = mount(SupersedeSuggestions, { props: { collectionId: 'docs' } })
    await flushPromises()
    return w
  }

  it('renders nothing when there are no suggestions', async () => {
    const w = await mountList([])
    expect(w.find('[data-testid="supersede-suggestions"]').exists()).toBe(false)
    expect(http.get).toHaveBeenCalledWith('/documents/supersede-suggestions', { params: { collection_id: 'docs' } })
  })

  it('stays quiet when the request fails', async () => {
    http.get.mockRejectedValueOnce(new Error('network'))
    const w = mount(SupersedeSuggestions, { props: { collectionId: 'docs' } })
    await flushPromises()
    expect(w.find('[data-testid="supersede-suggestions"]').exists()).toBe(false)
  })

  it('applies nothing until the owner accepts, then posts the pair', async () => {
    const w = await mountList([suggestion()])
    expect(w.findAll('[data-testid="suggestion"]')).toHaveLength(1)
    expect(http.post).not.toHaveBeenCalled()
    http.post.mockResolvedValueOnce({ data: {} })
    await w.get('[data-testid="accept"]').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith(
      '/documents/old1/supersede', { replaced_by: 'new1' }, { params: { collection_id: 'docs' } },
    )
    expect(w.emitted('changed')).toHaveLength(1)
    expect(w.findAll('[data-testid="suggestion"]')).toHaveLength(0)
  })

  it('remembers a dismissal without calling the server', async () => {
    const w = await mountList([suggestion(), suggestion({ document_id: 'old2', replaced_by: 'new2' })])
    await w.findAll('[data-testid="dismiss"]')[0].trigger('click')
    expect(w.findAll('[data-testid="suggestion"]')).toHaveLength(1)
    expect(http.post).not.toHaveBeenCalled()
    expect(JSON.parse(localStorage.getItem('clio.supersede.dismissed.docs'))).toEqual([suggestionKey(suggestion())])
    // a fresh mount (page reload) keeps it hidden
    const again = await mountList([suggestion(), suggestion({ document_id: 'old2', replaced_by: 'new2' })])
    expect(again.findAll('[data-testid="suggestion"]')).toHaveLength(1)
  })

  it('shows the server message when accepting fails', async () => {
    const w = await mountList([suggestion()])
    http.post.mockRejectedValueOnce({ message: 'That would make documents replace each other in a loop' })
    await w.get('[data-testid="accept"]').trigger('click')
    await flushPromises()
    expect(w.text()).toContain('replace each other in a loop')
    expect(w.findAll('[data-testid="suggestion"]')).toHaveLength(1)
  })
})

describe('SupersedeDialog', () => {
  beforeEach(() => {
    http.get.mockReset()
    http.post.mockReset()
    http.delete.mockReset()
    HTMLDialogElement.prototype.showModal = vi.fn()
    HTMLDialogElement.prototype.close = vi.fn()
  })

  const doc = { document_id: 'old1', filename: 'Policy v2.pdf' }

  async function open(over = {}) {
    http.get.mockResolvedValue({ data: { documents: [doc, { document_id: 'new1', filename: 'Policy v3.pdf' }] } })
    const w = mount(SupersedeDialog, { props: { doc: null, collectionId: 'docs' } })
    await w.setProps({ doc: { ...doc, ...over } })
    await flushPromises()
    return w
  }

  it('offers the other documents but not the document itself', async () => {
    const w = await open()
    const options = w.findAll('[role="option"]').map(o => o.text())
    expect(options).toEqual(['Policy v3.pdf'])
  })

  it('cannot save before a replacement is picked, then posts it with the note', async () => {
    const w = await open()
    const save = w.get('[data-testid="confirm-replacement"]')
    expect(save.attributes('disabled')).toBeDefined()
    await w.get('[role="option"]').trigger('click')
    await w.get('input[aria-label="Note"]').setValue('new limits')
    http.post.mockResolvedValueOnce({ data: {} })
    await w.get('[data-testid="confirm-replacement"]').trigger('click')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith(
      '/documents/old1/supersede', { replaced_by: 'new1', note: 'new limits' }, { params: { collection_id: 'docs' } },
    )
    expect(w.emitted('changed')).toHaveLength(1)
    expect(w.emitted('close')).toBeTruthy()
  })

  it('clears an existing replacement', async () => {
    const w = await open({ superseded_by: 'new1', superseded_by_filename: 'Policy v3.pdf' })
    expect(w.get('[data-testid="current-replacement"]').text()).toContain('Policy v3.pdf')
    http.delete.mockResolvedValueOnce({ data: { cleared: true } })
    await w.get('[data-testid="clear-replacement"]').trigger('click')
    await flushPromises()
    expect(http.delete).toHaveBeenCalledWith('/documents/old1/supersede', { params: { collection_id: 'docs' } })
    expect(w.emitted('changed')).toHaveLength(1)
  })

  it('shows the server refusal inline', async () => {
    const w = await open()
    await w.get('[role="option"]').trigger('click')
    http.post.mockRejectedValueOnce({ message: 'A document cannot replace itself' })
    await w.get('[data-testid="confirm-replacement"]').trigger('click')
    await flushPromises()
    expect(w.get('[role="alert"]').text()).toContain('cannot replace itself')
    expect(w.emitted('changed')).toBeUndefined()
  })
})
