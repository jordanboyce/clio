import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { adoptBrowserUser } from '../utils/personalData'

// Who is signed in, for deployments that use the built-in accounts
// (IDENTITY_PROVIDER=local). Every other deployment shape answers
// "authenticated" immediately and this store stays out of the way.
//
// `ready` flips once the first status check has resolved, so the gate can
// avoid flashing the sign-in page at someone whose session is still valid.
export const useAuthStore = defineStore('auth', () => {
  const ready = ref(false)
  const provider = ref('')
  const setupRequired = ref(false)
  const authenticated = ref(false)
  const user = ref(null)

  // An invite or password-reset link opened in this browser: /#token=...
  // (a fragment, so it never reaches the server's access log).
  const linkToken = ref('')
  const linkInfo = ref(null) // { purpose: 'invite' | 'reset', email }
  const linkError = ref('')

  const usesAccounts = computed(() => provider.value === 'local')
  const needsSignIn = computed(
    () => usesAccounts.value && (!authenticated.value || !!linkToken.value),
  )

  // A different person than last time on this browser: their predecessor's
  // chats, cache and provider keys were just wiped from storage, but the
  // stores may already hold them in memory, so start the app fresh.
  function adopt(account) {
    if (adoptBrowserUser(account?.email)) window.location.reload()
  }

  function applyStatus(data) {
    provider.value = data.provider || ''
    setupRequired.value = !!data.setup_required
    authenticated.value = !!data.authenticated
    user.value = data.user || null
    if (authenticated.value) adopt(user.value)
  }

  async function refresh() {
    try {
      const response = await http.get('/api/auth/status')
      applyStatus(response.data)
    } catch (err) {
      // Not an accounts deployment (the endpoint is gated by something else,
      // which will have prompted already), or the server is unreachable. In
      // both cases fall through to the app and let its own calls report.
      if (err?.status === 401) {
        authenticated.value = false
      } else {
        provider.value = ''
        authenticated.value = true
      }
    } finally {
      ready.value = true
    }
  }

  async function init() {
    const params = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const token = params.get('token')
    if (token) linkToken.value = token
    await refresh()
    if (linkToken.value && usesAccounts.value) {
      try {
        const response = await http.get('/api/auth/link', { params: { token: linkToken.value } })
        linkInfo.value = response.data
      } catch (err) {
        linkError.value = err?.message || 'This link is invalid or has expired.'
      }
    }
  }

  function clearLink() {
    linkToken.value = ''
    linkInfo.value = null
    linkError.value = ''
    window.history.replaceState({}, '', window.location.pathname + window.location.search)
  }

  async function login(email, password) {
    const response = await http.post('/api/auth/login', { email, password })
    user.value = response.data.user
    authenticated.value = true
    setupRequired.value = false
    adopt(user.value)
  }

  async function setup({ setupCode, email, password, displayName }) {
    const response = await http.post('/api/auth/setup', {
      setup_code: setupCode, email, password, display_name: displayName || '',
    })
    user.value = response.data.user
    authenticated.value = true
    setupRequired.value = false
    adopt(user.value)
  }

  async function acceptLink({ password, displayName }) {
    const response = await http.post('/api/auth/accept', {
      token: linkToken.value, password, display_name: displayName || '',
    })
    user.value = response.data.user
    authenticated.value = true
    clearLink()
    adopt(user.value)
  }

  async function logout() {
    try {
      await http.post('/api/auth/logout')
    } finally {
      authenticated.value = false
      user.value = null
    }
  }

  // The server answered 401 to a normal call: the session ended (expired,
  // signed out elsewhere, account disabled). Show the sign-in page; the
  // person's open work lives in local storage and survives.
  function markSignedOut() {
    if (!usesAccounts.value) return
    authenticated.value = false
    user.value = null
  }

  async function changePassword(currentPassword, newPassword) {
    await http.post('/api/auth/password', {
      current_password: currentPassword, new_password: newPassword,
    })
  }

  return {
    ready, provider, setupRequired, authenticated, user,
    linkToken, linkInfo, linkError,
    usesAccounts, needsSignIn,
    init, refresh, login, setup, acceptLink, logout, markSignedOut, clearLink, changePassword,
  }
})
