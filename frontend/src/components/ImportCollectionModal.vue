<template>
  <input ref="fileInput" type="file" accept=".zip,application/zip" class="hidden" @change="onFile" />

  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="import-collection-title" @close="onClosed">
    <div class="modal-box max-w-md">
      <h3 id="import-collection-title" class="font-bold text-lg">Import a collection</h3>

      <div v-if="checking" class="py-8 text-center space-y-2" role="status">
        <span class="loading loading-spinner loading-sm"></span>
        <p class="text-sm text-base-content/70">Reading {{ fileName }}…</p>
      </div>

      <div v-else-if="error" class="mt-3 space-y-3">
        <div class="alert alert-error text-sm">{{ error }}</div>
        <div class="modal-action mt-2">
          <button class="btn btn-ghost" @click="modal.close()">Close</button>
          <button class="btn btn-primary" @click="choose">Choose another file</button>
        </div>
      </div>

      <div v-else-if="summary" class="mt-3 space-y-3">
        <p class="text-sm text-base-content/70">
          <strong>{{ summary.name }}</strong>
          <span v-if="summary.description"> — {{ summary.description }}</span>
        </p>
        <p class="text-xs text-base-content/60">
          {{ summary.documents }} source{{ summary.documents === 1 ? '' : 's' }} ·
          {{ summary.chunks.toLocaleString() }} passages ·
          {{ formatBytes(summary.bytes) }}<span v-if="summary.sources_included"> · original files included</span>
          <span v-if="summary.exported_by"> · from {{ summary.exported_by }}</span>
        </p>

        <ModelFact :embedding="summary.embedding" title="Indexed with">
          <div class="mt-2 pt-2 border-t border-base-300 text-xs flex items-start gap-2" :class="summary.action === 'reuse' ? 'text-success' : 'text-base-content/75'">
            <component :is="summary.action === 'reuse' ? CheckCircle : RefreshCw" :size="14" class="mt-0.5 flex-shrink-0" aria-hidden="true" />
            <span>
              <strong>{{ summary.action === 'reuse' ? 'Ready almost at once.' : 'Will be embedded again here.' }}</strong>
              {{ summary.reason }}
              <template v-if="summary.action !== 'reuse'">
                {{ summary.chunks.toLocaleString() }} passages, in the background.
              </template>
            </span>
          </div>
        </ModelFact>

        <p v-if="summary.blocked_here" class="text-xs text-warning">
          {{ summary.blocked_here }} source{{ summary.blocked_here === 1 ? ' is' : 's are' }} on this server's blocklist and will be left out.
        </p>
        <p v-if="summary.quarantined_excluded" class="text-xs text-base-content/60">
          The sender held back {{ summary.quarantined_excluded }} source{{ summary.quarantined_excluded === 1 ? '' : 's' }} pending review.
        </p>

        <label class="form-control w-full">
          <span class="label-text text-sm mb-1">Name</span>
          <input v-model="name" class="input input-bordered input-sm w-full" @keyup.enter="confirm" />
          <span v-if="nameTaken" class="text-xs text-warning mt-1">You already have a collection with this name. It will be a separate copy.</span>
        </label>

        <p class="text-xs text-base-content/55">
          Imported text is checked against this server's blocklist and content policy settings like any other source.
        </p>

        <div class="modal-action mt-2">
          <button class="btn btn-ghost" :disabled="importing" @click="modal.close()">Cancel</button>
          <button class="btn btn-primary" :disabled="importing || !name.trim()" @click="confirm">
            <span v-if="importing" class="loading loading-spinner loading-xs"></span>
            Import
          </button>
        </div>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { CheckCircle, RefreshCw } from 'lucide-vue-next'
import http from '../utils/http'
import { useModal } from '../composables/useModal'
import { useCollectionStore } from '../stores/collectionStore'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'
import { formatBytes } from '../utils/format'
import ModelFact from './ModelFact.vue'

const emit = defineEmits(['imported'])
const collectionStore = useCollectionStore()
const userStore = useUserStore()
const ui = useUiStore()

const modal = useModal()
const fileInput = ref(null)
const fileName = ref('')
const checking = ref(false)
const importing = ref(false)
const error = ref('')
const summary = ref(null)
const name = ref('')
let confirmed = false

const nameTaken = computed(() => {
  const n = name.value.trim().toLowerCase()
  return !!n && collectionStore.sortedCollections.some(c => (c.name || '').toLowerCase() === n)
})

function choose() {
  fileInput.value?.click()
}

async function onFile(event) {
  const file = event.target.files?.[0]
  event.target.value = '' // let the same file be picked again after an error
  if (!file) return
  discard()
  fileName.value = file.name
  summary.value = null
  error.value = ''
  checking.value = true
  confirmed = false
  modal.open()
  try {
    const form = new FormData()
    form.append('file', file)
    // Bundles with originals can be large: no client timeout.
    const { data } = await http.post('/api/collections/import/preview', form, { timeout: 0 })
    summary.value = data
    name.value = data.name
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Could not read that file.'
  } finally {
    checking.value = false
  }
}

async function confirm() {
  if (!summary.value || importing.value || !name.value.trim()) return
  importing.value = true
  try {
    const form = new FormData()
    form.append('upload_id', summary.value.upload_id)
    form.append('name', name.value.trim())
    if (userStore.privateCollections) form.append('visibility', 'private')
    const { data } = await http.post('/api/collections/import', form, { timeout: 0 })
    confirmed = true
    modal.close()
    emit('imported', data)
  } catch (err) {
    ui.toastError(err, 'Could not import that file')
  } finally {
    importing.value = false
  }
}

// A previewed bundle that was never imported is discarded, not left for the sweep.
function discard() {
  const id = summary.value?.upload_id
  if (id && !confirmed) http.delete(`/api/collections/import/preview/${id}`).catch(() => {})
}

function onClosed() {
  modal.onClosed()
  discard()
  summary.value = null
}

defineExpose({ choose })
</script>
