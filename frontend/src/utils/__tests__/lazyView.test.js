import { describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { lazyView } from '../lazyView.js'

// F13: a lazily loaded tab says what it is opening while its chunk loads,
// and a chunk that fails to load offers Try again rather than a blank pane.
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const settle = async () => {
  await flushPromises()
  await wait(0)
  await flushPromises()
}
const Loaded = defineComponent({
  props: { label: { type: String, default: '' } },
  emits: ['ping'],
  setup(props, { emit }) {
    return () => h('button', { 'data-testid': 'loaded', onClick: () => emit('ping') }, props.label)
  },
})

describe('lazyView', () => {
  it('shows a named loading state while the chunk is on its way', async () => {
    let resolve
    const View = lazyView(() => new Promise((r) => { resolve = r }), { name: 'Research' })
    const wrapper = mount(View)
    await wait(200)
    expect(wrapper.get('[data-testid="lazy-view-fallback"]').text()).toBe('Opening Research…')

    resolve(Loaded)
    await settle()
    expect(wrapper.find('[data-testid="lazy-view-fallback"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="loaded"]').exists()).toBe(true)
  })

  it('passes props and listeners through to the loaded view', async () => {
    const View = lazyView(() => Promise.resolve(Loaded))
    const pings = []
    const wrapper = mount(View, { props: { label: 'hello', onPing: () => pings.push(1) } })
    await settle()
    const button = wrapper.get('[data-testid="loaded"]')
    expect(button.text()).toBe('hello')
    await button.trigger('click')
    expect(pings).toEqual([1])
  })

  it('offers Try again after the chunk fails, and loads on the retry', async () => {
    let calls = 0
    const View = lazyView(() => {
      calls++
      return calls <= 2
        ? Promise.reject(new Error('Failed to fetch dynamically imported module'))
        : Promise.resolve(Loaded)
    }, { name: 'Research' })
    const wrapper = mount(View)
    await settle()

    expect(calls).toBe(2) // one silent retry first
    const fallback = wrapper.get('[data-testid="lazy-view-fallback"]')
    expect(fallback.text()).toContain('Research could not be loaded')
    expect(fallback.text()).toContain('Reload Clio')

    await wrapper.get('[data-testid="lazy-view-retry"]').trigger('click')
    await settle()
    expect(calls).toBe(3)
    expect(wrapper.find('[data-testid="loaded"]').exists()).toBe(true)
  })
})
