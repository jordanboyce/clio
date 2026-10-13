import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import http from '../../utils/http'
import AuthGate from '../AuthGate.vue'
import { useAuthStore } from '../../stores/authStore'

const mountGate = () =>
  mount(AuthGate, { slots: { default: '<div data-testid="app">the app</div>' } })

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  window.history.replaceState({}, '', '/')
})

describe('auth gate', () => {
  it('passes straight through when the deployment does not use built-in accounts', async () => {
    http.get.mockResolvedValue({ data: { provider: 'oidc', setup_required: false, authenticated: true } })
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(false)
  })

  it('shows the first-run setup form, with the setup code field, when no account exists', async () => {
    http.get.mockResolvedValue({ data: { provider: 'local', setup_required: true, authenticated: false } })
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Set up Clio')
    expect(wrapper.text()).toContain('Setup code')
  })

  it('shows sign-in when accounts exist and there is no session', async () => {
    http.get.mockResolvedValue({ data: { provider: 'local', setup_required: false, authenticated: false } })
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.text()).toContain('Sign in to Clio')
    expect(wrapper.text()).not.toContain('Setup code')
  })

  it('reveals the app after a successful sign-in', async () => {
    http.get.mockResolvedValue({ data: { provider: 'local', setup_required: false, authenticated: false } })
    http.post.mockResolvedValue({ data: { user: { email: 'a@example.com' } } })
    const wrapper = mountGate()
    await flushPromises()
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('a@example.com')
    await inputs[1].setValue('a long enough password')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(http.post).toHaveBeenCalledWith('/api/auth/login', { email: 'a@example.com', password: 'a long enough password' })
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(true)
  })

  it('keeps the form and shows the server message when sign-in fails', async () => {
    http.get.mockResolvedValue({ data: { provider: 'local', setup_required: false, authenticated: false } })
    http.post.mockRejectedValue({ status: 401, message: 'Email or password is incorrect.' })
    const wrapper = mountGate()
    await flushPromises()
    const inputs = wrapper.findAll('input')
    await inputs[0].setValue('a@example.com')
    await inputs[1].setValue('wrong password!!')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.find('[role="alert"]').text()).toContain('Email or password is incorrect.')
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(false)
  })

  it('opens an invite link on the account-creation form for that address', async () => {
    window.history.replaceState({}, '', '/#token=abc123')
    http.get.mockImplementation((url) =>
      url === '/api/auth/status'
        ? Promise.resolve({ data: { provider: 'local', setup_required: false, authenticated: false } })
        : Promise.resolve({ data: { purpose: 'invite', email: 'new@example.com' } }),
    )
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.text()).toContain('Join Clio')
    const email = wrapper.find('input[type="email"]')
    expect(email.element.value).toBe('new@example.com')
    expect(email.attributes('readonly')).toBeDefined()
  })

  it('says so when an invite link is invalid', async () => {
    window.history.replaceState({}, '', '/#token=dead')
    http.get.mockImplementation((url) =>
      url === '/api/auth/status'
        ? Promise.resolve({ data: { provider: 'local', setup_required: false, authenticated: false } })
        : Promise.reject({ status: 404, message: 'This link is invalid or has expired. Ask an admin for a new one.' }),
    )
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.text()).toContain('invalid or has expired')
  })

  it('returns to sign-in when the session ends mid-use', async () => {
    http.get.mockResolvedValue({ data: { provider: 'local', setup_required: false, authenticated: true, user: { email: 'a@example.com' } } })
    const wrapper = mountGate()
    await flushPromises()
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(true)
    useAuthStore().markSignedOut()
    await flushPromises()
    expect(wrapper.find('[data-testid="app"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('Sign in to Clio')
  })
})
