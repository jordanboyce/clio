import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PastedTextEditor from '../PastedTextEditor.vue'

// The editor the Sources panel shows for "Text": a contenteditable box that
// cleans what is pasted into it and hands the parent a title and markup.

let wrapper
afterEach(() => { wrapper?.unmount(); wrapper = null })

const mountEditor = async (props = {}) => {
  wrapper = mount(PastedTextEditor, { props, attachTo: document.body })
  await wrapper.vm.$nextTick() // the editor takes focus once mounted
  return wrapper
}
const editor = () => wrapper.get('[role="textbox"]')
const typeHtml = async (html) => {
  editor().element.innerHTML = html
  await editor().trigger('input')
}
const paste = async (types) => {
  const event = new Event('paste', { bubbles: true, cancelable: true })
  event.clipboardData = { getData: (type) => types[type] || '' }
  editor().element.dispatchEvent(event)
  await wrapper.vm.$nextTick()
  return event
}

describe('PastedTextEditor', () => {
  it('cannot add nothing, and reports how much has been written', async () => {
    const w = await mountEditor()
    const add = w.get('button.btn-primary')
    expect(add.attributes('disabled')).toBeDefined()
    await typeHtml('<p><br></p>')
    expect(add.attributes('disabled')).toBeDefined()
    await typeHtml('<p>Hello <b>world</b></p>')
    expect(add.attributes('disabled')).toBeUndefined()
    expect(w.text()).toContain('11 characters')
  })

  it('cleans a rich paste and keeps a plain one as paragraphs', async () => {
    await mountEditor()
    const rich = await paste({
      'text/html': '<p style="color:red" onclick="x()">Hello <span class="y">there</span></p><script>bad()</script>',
      'text/plain': 'Hello there',
    })
    expect(rich.defaultPrevented).toBe(true)
    expect(editor().element.innerHTML).toBe('<p>Hello there</p>')

    await paste({ 'text/plain': 'One\ntwo\n\nThree <x>' })
    expect(editor().element.innerHTML).toBe('<p>Hello there</p><p>One<br>two</p><p>Three &lt;x&gt;</p>')

    // A single line goes in as text, not as a paragraph of its own.
    await paste({ 'text/plain': 'tail' })
    expect(editor().element.innerHTML).toBe('<p>Hello there</p><p>One<br>two</p><p>Three &lt;x&gt;</p>tail')
  })

  it('submits the title and the cleaned markup, and can be emptied by its parent', async () => {
    const w = await mountEditor()
    await w.get('input[aria-label="Title for the text"]').setValue('  Travel policy ')
    await typeHtml('<h2 style="x">Policy</h2><p>Two <b>weeks</b>.</p>')
    await w.get('button.btn-primary').trigger('click')
    expect(w.emitted('submit')).toEqual([[{ title: 'Travel policy', html: '<h2>Policy</h2><p>Two <b>weeks</b>.</p>', chars: 'Policy Two weeks.'.length }]])

    // Ctrl+Enter submits too.
    await editor().trigger('keydown', { key: 'Enter', ctrlKey: true })
    expect(w.emitted('submit')).toHaveLength(2)

    w.vm.clear()
    await w.vm.$nextTick()
    expect(editor().element.innerHTML).toBe('')
    expect(w.get('input').element.value).toBe('')
    expect(w.get('button.btn-primary').attributes('disabled')).toBeDefined()
  })

  it('holds still while the parent is sending', async () => {
    const w = await mountEditor({ busy: true })
    await typeHtml('<p>text</p>')
    expect(w.get('button.btn-primary').attributes('disabled')).toBeDefined()
    expect(w.get('button.btn-primary').text()).toContain('Adding')
    await w.get('button.btn-primary').trigger('click')
    expect(w.emitted('submit')).toBeUndefined()
  })
})
