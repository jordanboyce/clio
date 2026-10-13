<template>
  <main class="min-h-screen grid place-items-center bg-base-200 px-4 py-10">
    <div class="w-full max-w-sm">
      <div class="text-center mb-6">
        <span class="brand-art brand-art-mark block w-14 h-14 mx-auto mb-3" role="img" aria-label="Clio"></span>
        <h1 class="text-xl font-semibold">{{ heading }}</h1>
        <p v-if="intro" class="text-sm text-base-content/70 mt-1 leading-relaxed">{{ intro }}</p>
      </div>

      <!-- A link that no longer works is its own state: say so, then offer sign-in. -->
      <div v-if="auth.linkToken && !auth.linkInfo" class="rounded-box bg-base-100 border border-base-300 p-5 space-y-3">
        <p class="text-sm" role="alert">{{ auth.linkError || 'Checking the link…' }}</p>
        <button v-if="auth.linkError" type="button" class="btn btn-primary btn-sm w-full" @click="auth.clearLink()">
          Go to sign in
        </button>
      </div>

      <form v-else class="rounded-box bg-base-100 border border-base-300 p-5 space-y-4" @submit.prevent="submit" novalidate>
        <label v-if="mode === 'setup'" class="form-control block">
          <span class="label-text text-sm font-medium">Setup code</span>
          <input
            v-model.trim="form.setupCode" type="text" autocomplete="off" spellcheck="false" required
            class="input input-bordered w-full mt-1 font-mono" placeholder="xxxx-xxxx-xxxx"
          />
          <span class="text-xs text-base-content/60 mt-1 block">
            Printed in the server log when Clio starts. With Docker: <code>docker logs clio</code>.
          </span>
        </label>

        <label v-if="mode === 'setup' || mode === 'invite'" class="form-control block">
          <span class="label-text text-sm font-medium">Your name <span class="text-base-content/50 font-normal">(optional)</span></span>
          <input v-model.trim="form.displayName" type="text" autocomplete="name" class="input input-bordered w-full mt-1" />
        </label>

        <label class="form-control block">
          <span class="label-text text-sm font-medium">Email</span>
          <input
            v-model.trim="form.email" type="email" autocomplete="username" required
            :readonly="mode === 'invite' || mode === 'reset'"
            class="input input-bordered w-full mt-1" :class="{ 'bg-base-200': mode === 'invite' || mode === 'reset' }"
          />
        </label>

        <label class="form-control block">
          <span class="label-text text-sm font-medium">{{ mode === 'login' ? 'Password' : 'Choose a password' }}</span>
          <input
            v-model="form.password" type="password" required
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            :minlength="mode === 'login' ? undefined : 10"
            class="input input-bordered w-full mt-1"
          />
          <span v-if="mode !== 'login'" class="text-xs text-base-content/60 mt-1 block">
            At least 10 characters. A few ordinary words strung together works well.
          </span>
        </label>

        <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>

        <button type="submit" class="btn btn-primary w-full" :disabled="busy || !canSubmit">
          <span v-if="busy" class="loading loading-spinner loading-sm"></span>
          {{ submitLabel }}
        </button>

        <p v-if="mode === 'login'" class="text-xs text-base-content/60 text-center leading-relaxed">
          Forgot your password? Ask an administrator for a reset link.
        </p>
        <button v-if="auth.linkToken && auth.linkInfo" type="button" class="btn btn-ghost btn-xs w-full" @click="auth.clearLink()">
          Cancel and go to sign in
        </button>
      </form>
    </div>
  </main>
</template>

<script setup>
import { computed, reactive, ref, watchEffect } from 'vue'
import { useAuthStore } from '../stores/authStore'

const auth = useAuthStore()

const form = reactive({ setupCode: '', displayName: '', email: '', password: '' })
const busy = ref(false)
const error = ref('')

const mode = computed(() => {
  if (auth.linkInfo) return auth.linkInfo.purpose === 'reset' ? 'reset' : 'invite'
  if (auth.setupRequired) return 'setup'
  return 'login'
})

watchEffect(() => {
  if (auth.linkInfo?.email) form.email = auth.linkInfo.email
})

const heading = computed(() => ({
  setup: 'Set up Clio',
  invite: 'Join Clio',
  reset: 'Choose a new password',
  login: 'Sign in to Clio',
}[mode.value]))

const intro = computed(() => ({
  setup: 'Create the first administrator account. Your documents stay on this machine.',
  invite: 'You have been invited. Choose a password to create your account.',
  reset: 'An administrator sent you this link to reset your password.',
  login: '',
}[mode.value]))

const submitLabel = computed(() => ({
  setup: 'Create account',
  invite: 'Create account',
  reset: 'Save password',
  login: 'Sign in',
}[mode.value]))

const canSubmit = computed(() => {
  if (!form.email || !form.password) return false
  return mode.value !== 'setup' || !!form.setupCode
})

async function submit() {
  if (busy.value || !canSubmit.value) return
  busy.value = true
  error.value = ''
  try {
    if (mode.value === 'setup') {
      await auth.setup({ setupCode: form.setupCode, email: form.email, password: form.password, displayName: form.displayName })
    } else if (mode.value === 'login') {
      await auth.login(form.email, form.password)
    } else {
      await auth.acceptLink({ password: form.password, displayName: form.displayName })
    }
    form.password = ''
  } catch (err) {
    error.value = err?.message || 'Something went wrong. Try again.'
  } finally {
    busy.value = false
  }
}
</script>
