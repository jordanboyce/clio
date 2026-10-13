<template>
  <div class="space-y-4">
    <!-- Invite -->
    <form class="flex flex-wrap items-end gap-2" @submit.prevent="invite">
      <label class="form-control flex-1 min-w-56">
        <span class="label-text text-xs font-medium">Invite by email</span>
        <input
          v-model.trim="inviteEmail" type="email" required placeholder="name@example.com"
          class="input input-bordered input-sm w-full mt-1" autocomplete="off"
        />
      </label>
      <label class="label cursor-pointer gap-2 py-1">
        <input v-model="inviteAdmin" type="checkbox" class="checkbox checkbox-sm" />
        <span class="label-text text-xs">Administrator</span>
      </label>
      <button type="submit" class="btn btn-primary btn-sm" :disabled="busy || !inviteEmail">
        <UserPlus :size="14" aria-hidden="true" /> Create invite link
      </button>
    </form>

    <!-- A link to hand over: shown once, with the address it belongs to. -->
    <div v-if="link" class="rounded-lg border border-base-300 bg-base-200/60 p-3 space-y-2" role="status">
      <p class="text-sm">
        {{ link.kind === 'reset' ? 'Password reset link for' : 'Invite link for' }}
        <span class="font-medium">{{ link.email }}</span>
        <span class="text-base-content/60"> · {{ link.expires }}</span>
      </p>
      <div class="flex gap-2">
        <input ref="linkInput" :value="link.url" readonly class="input input-bordered input-sm flex-1 font-mono text-xs" @focus="$event.target.select()" />
        <button type="button" class="btn btn-sm btn-outline" @click="copyLink">
          <Check v-if="copied" :size="14" aria-hidden="true" /><Copy v-else :size="14" aria-hidden="true" />
          {{ copied ? 'Copied' : 'Copy' }}
        </button>
      </div>
      <p class="text-xs text-base-content/60">Send it to them yourself. It works once and is not shown again.</p>
    </div>

    <p v-if="error" class="text-sm text-error" role="alert">{{ error }}</p>

    <!-- People -->
    <div class="overflow-x-auto">
      <table class="table table-sm">
        <thead>
          <tr class="text-xs text-base-content/60">
            <th>Person</th><th>Role</th><th class="hidden sm:table-cell">Last sign-in</th><th class="text-right">Actions</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in users" :key="u.email" :class="{ 'opacity-60': u.disabled }">
            <td>
              <div class="font-medium truncate max-w-[16rem]">{{ u.display_name }}</div>
              <div v-if="u.display_name !== u.email" class="text-xs text-base-content/60 truncate max-w-[16rem]">{{ u.email }}</div>
            </td>
            <td>
              <span class="text-xs">{{ u.disabled ? 'Disabled' : u.is_admin ? 'Administrator' : 'Member' }}</span>
            </td>
            <td class="hidden sm:table-cell text-xs text-base-content/60">{{ when(u.last_login_at) }}</td>
            <td class="text-right">
              <div class="dropdown dropdown-end">
                <button tabindex="0" type="button" class="btn btn-ghost btn-xs" :aria-label="`Actions for ${u.email}`">
                  <EllipsisVertical :size="14" aria-hidden="true" />
                </button>
                <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-50 w-52 p-1 shadow border border-base-300 text-sm">
                  <li><button type="button" @click="reset(u)">Create reset link</button></li>
                  <li v-if="!u.disabled">
                    <button type="button" :disabled="isSelf(u)" @click="update(u, { is_admin: !u.is_admin })">
                      {{ u.is_admin ? 'Remove administrator' : 'Make administrator' }}
                    </button>
                  </li>
                  <li>
                    <button type="button" :disabled="isSelf(u)" @click="update(u, { disabled: !u.disabled })">
                      {{ u.disabled ? 'Enable account' : 'Disable account' }}
                    </button>
                  </li>
                  <li><button type="button" class="text-error" :disabled="isSelf(u)" @click="remove(u)">Delete account</button></li>
                </ul>
              </div>
            </td>
          </tr>
          <tr v-if="!loading && users.length === 0"><td colspan="4" class="text-sm text-base-content/60">No accounts yet.</td></tr>
        </tbody>
      </table>
    </div>
    <p class="text-xs text-base-content/60 leading-relaxed">
      Deleting or disabling an account ends its sessions at once. Their documents and collections stay where they are.
    </p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Check, Copy, EllipsisVertical, UserPlus } from 'lucide-vue-next'
import http from '../utils/http'
import { useAuthStore } from '../stores/authStore'

const auth = useAuthStore()
const users = ref([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const inviteEmail = ref('')
const inviteAdmin = ref(false)
const link = ref(null)
const copied = ref(false)
const linkInput = ref(null)

const isSelf = (u) => u.email === auth.user?.email
const when = (iso) => (iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : 'Never')

async function load() {
  loading.value = true
  try {
    users.value = (await http.get('/api/auth/users')).data.users || []
  } catch (err) {
    error.value = err.message || 'Could not load accounts.'
  } finally {
    loading.value = false
  }
}

async function run(fn) {
  busy.value = true
  error.value = ''
  try {
    await fn()
  } catch (err) {
    error.value = err.message || 'That did not work.'
  } finally {
    busy.value = false
    if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  }
}

const invite = () => run(async () => {
  const { data } = await http.post('/api/auth/users/invite', { email: inviteEmail.value, is_admin: inviteAdmin.value })
  link.value = { kind: 'invite', email: data.email, url: data.url, expires: `valid ${data.expires_in_days} days` }
  copied.value = false
  inviteEmail.value = ''
  inviteAdmin.value = false
})

const reset = (u) => run(async () => {
  const { data } = await http.post(`/api/auth/users/${encodeURIComponent(u.email)}/reset`)
  link.value = { kind: 'reset', email: data.email, url: data.url, expires: `valid ${data.expires_in_hours} hours` }
  copied.value = false
})

const update = (u, patch) => run(async () => {
  await http.patch(`/api/auth/users/${encodeURIComponent(u.email)}`, patch)
  await load()
})

const remove = (u) => run(async () => {
  if (!confirm(`Delete the account for ${u.email}? Their sessions end immediately.`)) return
  await http.delete(`/api/auth/users/${encodeURIComponent(u.email)}`)
  await load()
})

async function copyLink() {
  try {
    await navigator.clipboard.writeText(link.value.url)
    copied.value = true
  } catch {
    linkInput.value?.select()
  }
}

onMounted(load)
</script>
