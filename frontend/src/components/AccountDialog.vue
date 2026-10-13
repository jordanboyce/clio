<template>
  <dialog ref="dialog" class="modal" @close="$emit('close')">
    <div class="modal-box max-w-sm">
      <h2 class="font-semibold text-lg">Account</h2>
      <p class="text-sm text-base-content/70 mt-1 truncate">{{ auth.user?.email }}</p>

      <form class="mt-4 space-y-3" @submit.prevent="submit" novalidate>
        <label class="form-control block">
          <span class="label-text text-sm font-medium">Current password</span>
          <input v-model="form.current" type="password" autocomplete="current-password" required class="input input-bordered w-full mt-1" />
        </label>
        <label class="form-control block">
          <span class="label-text text-sm font-medium">New password</span>
          <input v-model="form.next" type="password" autocomplete="new-password" minlength="10" required class="input input-bordered w-full mt-1" />
          <span class="text-xs text-base-content/60 mt-1 block">At least 10 characters. Your other devices are signed out.</span>
        </label>
        <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>
        <p v-if="done" class="text-sm text-success" role="status">Password changed.</p>
        <div class="modal-action mt-2">
          <button type="button" class="btn btn-ghost btn-sm" @click="dialog.close()">Close</button>
          <button type="submit" class="btn btn-primary btn-sm" :disabled="busy || !form.current || !form.next">
            <span v-if="busy" class="loading loading-spinner loading-xs"></span>
            Change password
          </button>
        </div>
      </form>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useAuthStore } from '../stores/authStore'

defineEmits(['close'])
const auth = useAuthStore()
const dialog = ref(null)
const form = reactive({ current: '', next: '' })
const busy = ref(false)
const error = ref('')
const done = ref(false)

function open() {
  form.current = ''
  form.next = ''
  error.value = ''
  done.value = false
  dialog.value?.showModal()
}

async function submit() {
  busy.value = true
  error.value = ''
  done.value = false
  try {
    await auth.changePassword(form.current, form.next)
    done.value = true
    form.current = ''
    form.next = ''
  } catch (err) {
    error.value = err?.message || 'Could not change the password.'
  } finally {
    busy.value = false
  }
}

defineExpose({ open })
</script>
