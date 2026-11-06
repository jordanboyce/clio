<template>
  <section aria-labelledby="admin-apps">
    <div class="flex items-center justify-between mb-3 gap-2 flex-wrap">
      <h2 id="admin-apps" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">Registered apps</h2>
      <div class="flex items-center gap-2">
        <a class="btn btn-ghost btn-xs" href="/api/v1/docs" target="_blank" rel="noopener">API reference</a>
        <button v-if="!showForm" class="btn btn-xs btn-outline" data-testid="apps-new" @click="openForm">Register an app</button>
      </div>
    </div>
    <p class="text-[11px] text-base-content/50 mb-3">
      Applications that call this deployment over <code class="font-mono">/api/v1</code>. Each one sees only the
      collections you grant, and its credential opens nothing else.
    </p>

    <!-- A key is shown once. It stays until dismissed, so a stray click elsewhere cannot lose it. -->
    <div v-if="issued" class="rounded-lg border border-warning/50 bg-warning/10 p-4 mb-3 space-y-2" role="status" data-testid="apps-issued">
      <div class="text-xs font-medium">Key for {{ issued.name }}. Copy it now: it will not be shown again.</div>
      <div class="flex items-center gap-2">
        <code class="font-mono text-[11px] break-all flex-1 select-all" data-testid="apps-key">{{ issued.key }}</code>
        <button class="btn btn-xs btn-outline" @click="copyKey">{{ copied ? 'Copied' : 'Copy' }}</button>
        <button class="btn btn-xs btn-ghost" data-testid="apps-dismiss" @click="dismissKey">Done</button>
      </div>
    </div>

    <form v-if="showForm" class="rounded-lg border border-base-300/60 bg-base-100 p-4 mb-3 space-y-3" data-testid="apps-form" @submit.prevent="register">
      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <label class="block">
          <span class="text-xs font-medium">Name</span>
          <input v-model="form.name" class="input input-bordered input-xs w-full mt-1 font-mono" required maxlength="64" placeholder="invoice-portal" />
          <span class="text-[11px] text-base-content/50">Appears in the audit trail and access log. Cannot be changed later.</span>
        </label>
        <label class="block">
          <span class="text-xs font-medium">Expires after (days)</span>
          <input v-model.number="form.expires_in_days" type="number" min="1" max="3650" class="input input-bordered input-xs w-full mt-1" placeholder="Never" />
        </label>
      </div>

      <fieldset>
        <legend class="text-xs font-medium">Collections it can reach</legend>
        <div v-if="collections.length === 0" class="text-xs text-base-content/45 mt-1">No collections yet.</div>
        <div v-else class="mt-1 max-h-36 overflow-y-auto space-y-1">
          <label v-for="c in collections" :key="c.id" class="flex items-center gap-2 text-xs">
            <input v-model="form.collections" type="checkbox" class="checkbox checkbox-xs" :value="c.id" />
            <span class="truncate">{{ c.name }}</span>
            <span v-if="c.sensitivity === 'restricted'" class="badge badge-xs badge-warning">restricted</span>
          </label>
        </div>
      </fieldset>

      <label class="flex items-center gap-2 text-xs">
        <input v-model="form.can_write" type="checkbox" class="checkbox checkbox-xs" />
        Allow it to add, replace and remove documents
      </label>

      <details class="text-xs">
        <summary class="cursor-pointer font-medium">Identity provider and budgets</summary>
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-2">
          <label v-for="b in BUDGETS" :key="b.field" class="block">
            <span class="text-[11px]">{{ b.label }} per minute</span>
            <input v-model.number="form[b.field]" type="number" min="0" class="input input-bordered input-xs w-full mt-1" placeholder="Deployment default" />
          </label>
        </div>
        <p class="text-[11px] text-base-content/50 mt-1">0 means unlimited.</p>
        <label class="block mt-2">
          <span class="text-[11px]">IdP subject (the <code class="font-mono">sub</code> of its client-credentials tokens)</span>
          <input v-model="form.oidc_subject" class="input input-bordered input-xs w-full mt-1 font-mono" maxlength="255" placeholder="Optional" />
        </label>
        <label v-if="form.oidc_subject.trim()" class="flex items-center gap-2 mt-2">
          <input v-model="form.no_key" type="checkbox" class="checkbox checkbox-xs" />
          It only uses IdP tokens: do not issue a key
        </label>
      </details>

      <div v-if="formError" class="text-xs text-error" role="alert">{{ formError }}</div>
      <div class="flex justify-end gap-2">
        <button type="button" class="btn btn-xs btn-ghost" @click="showForm = false">Cancel</button>
        <button type="submit" class="btn btn-xs btn-primary" data-testid="apps-submit" :disabled="saving || !form.name.trim() || form.collections.length === 0">Register</button>
      </div>
    </form>

    <div v-if="loadError" class="text-xs text-error" role="alert">{{ loadError }}</div>
    <div v-else-if="apps.length === 0" class="text-xs text-base-content/45">No apps registered.</div>
    <div v-else class="overflow-x-auto rounded-lg border border-base-300/60 bg-base-100">
      <table class="table table-xs">
        <thead>
          <tr>
            <th>App</th>
            <th>Can reach</th>
            <th>Access</th>
            <th>Last used</th>
            <th><span class="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="app in apps" :key="app.id" :class="{ 'opacity-50': app.status !== 'active' }" data-testid="apps-row">
            <td>
              <div class="font-mono text-[11px]">{{ app.name }}</div>
              <div class="text-[11px] text-base-content/50">
                <span v-if="app.key_prefix">{{ app.key_prefix }}…</span>
                <span v-if="app.key_prefix && app.oidc_subject"> · </span>
                <span v-if="app.oidc_subject">IdP</span>
              </div>
            </td>
            <td class="text-[11px]">{{ collectionNames(app) }}</td>
            <td class="text-[11px]">
              {{ app.can_write ? 'Read and write' : 'Read only' }}
              <span v-if="app.status !== 'active'" class="badge badge-xs ml-1">{{ app.status }}</span>
              <div v-else-if="app.expires_at" class="text-base-content/50">Until {{ day(app.expires_at) }}</div>
              <div v-if="app.previous_key_expires_at" class="text-base-content/50">Old key until {{ day(app.previous_key_expires_at) }}</div>
            </td>
            <td class="text-[11px] whitespace-nowrap">{{ app.last_used_at ? day(app.last_used_at) : 'Never' }}</td>
            <td class="whitespace-nowrap text-right">
              <template v-if="app.status === 'active'">
                <template v-if="confirming?.id === app.id">
                  <span class="text-[11px] mr-1">{{ confirming.prompt }}</span>
                  <button class="btn btn-xs btn-error btn-outline" data-testid="apps-confirm" @click="confirming.run">Yes</button>
                  <button class="btn btn-xs btn-ghost" @click="confirming = null">No</button>
                </template>
                <template v-else>
                  <button class="btn btn-xs btn-ghost" data-testid="apps-rotate" @click="askRotate(app)">New key</button>
                  <button class="btn btn-xs btn-ghost text-error" data-testid="apps-revoke" @click="askRevoke(app)">Revoke</button>
                </template>
              </template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import http from '../utils/http'
import { useUiStore } from '../stores/uiStore'

const ROTATION_GRACE_HOURS = 24
const BUDGETS = [
  { field: 'search_per_minute', label: 'Searches' },
  { field: 'ask_per_minute', label: 'Answers' },
  { field: 'other_per_minute', label: 'Other requests' },
]

const ui = useUiStore()
const apps = ref([])
const collections = ref([])
const loadError = ref('')
const showForm = ref(false)
const saving = ref(false)
const formError = ref('')
const issued = ref(null)
const copied = ref(false)
const confirming = ref(null)

const emptyForm = () => ({
  name: '', collections: [], can_write: false, expires_in_days: '', oidc_subject: '', no_key: false,
  search_per_minute: '', ask_per_minute: '', other_per_minute: '',
})
const form = ref(emptyForm())

const day = (iso) => new Date(`${iso}Z`).toLocaleDateString()
const collectionNames = (app) => app.collections
  .map((id) => collections.value.find((c) => c.id === id)?.name || id)
  .join(', ')

const load = async () => {
  loadError.value = ''
  try {
    const [listed, known] = await Promise.all([http.get('/api/admin/apps'), http.get('/api/collections')])
    apps.value = listed.data.apps || []
    collections.value = known.data.collections || []
  } catch (err) {
    loadError.value = err.response?.data?.detail || 'Could not load registered apps.'
  }
}

const openForm = () => {
  form.value = emptyForm()
  formError.value = ''
  showForm.value = true
}

const register = async () => {
  saving.value = true
  formError.value = ''
  const { no_key: noKey, ...fields } = form.value
  const subject = fields.oidc_subject.trim()
  const body = { name: fields.name.trim(), collections: fields.collections, can_write: fields.can_write, issue_key: !(noKey && subject) }
  if (subject) body.oidc_subject = subject
  if (fields.expires_in_days !== '') body.expires_in_days = fields.expires_in_days
  for (const { field } of BUDGETS) {
    if (fields[field] !== '') body[field] = fields[field]
  }
  try {
    const { data } = await http.post('/api/admin/apps', body)
    if (data.key) issued.value = { name: data.name, key: data.key }
    showForm.value = false
    copied.value = false
    await load()
  } catch (err) {
    const detail = err.response?.data?.detail
    formError.value = typeof detail === 'string' ? detail : 'Could not register the app. Check the fields and try again.'
  } finally {
    saving.value = false
  }
}

const copyKey = async () => {
  try {
    await navigator.clipboard.writeText(issued.value.key)
    copied.value = true
  } catch {
    ui.notify('Could not reach the clipboard. Select the key and copy it by hand.', 'warning')
  }
}
const dismissKey = () => { issued.value = null }

const askRotate = (app) => {
  confirming.value = {
    id: app.id,
    prompt: `New key? The old one keeps working for ${ROTATION_GRACE_HOURS} hours.`,
    run: () => rotate(app),
  }
}
const askRevoke = (app) => {
  confirming.value = { id: app.id, prompt: 'Revoke for good?', run: () => revoke(app) }
}

const rotate = async (app) => {
  confirming.value = null
  try {
    const { data } = await http.post(`/api/admin/apps/${app.id}/rotate-key`, { grace_hours: ROTATION_GRACE_HOURS })
    issued.value = { name: data.name, key: data.key }
    copied.value = false
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not issue a new key')
  }
}

const revoke = async (app) => {
  confirming.value = null
  try {
    await http.delete(`/api/admin/apps/${app.id}`)
    ui.notify(`${app.name} can no longer connect.`, 'success')
    await load()
  } catch (err) {
    ui.toastError(err, 'Could not revoke the app')
  }
}

onMounted(load)
</script>
