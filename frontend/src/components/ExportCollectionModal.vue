<template>
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="export-collection-title" @close="modal.onClosed">
    <div class="modal-box max-w-md">
      <h3 id="export-collection-title" class="font-bold text-lg">Export {{ target?.name }}</h3>
      <p class="text-sm text-base-content/70 mt-1 mb-4">
        One <code>.clio.zip</code> another Clio can import. Everything already read and indexed travels with it,
        so nothing is scanned or OCR'd again.
      </p>

      <div v-if="loading" class="py-6 text-center"><span class="loading loading-spinner loading-sm"></span></div>
      <div v-else-if="error" class="alert alert-error text-sm">{{ error }}</div>
      <div v-else-if="preview" class="space-y-3">
        <ModelFact :embedding="preview.embedding">
          <div class="text-xs text-base-content/60 mt-1.5">
            {{ preview.documents }} source{{ preview.documents === 1 ? '' : 's' }} ·
            {{ preview.chunks.toLocaleString() }} passages
            <span v-if="preview.quarantined_excluded"> · {{ preview.quarantined_excluded }} held source{{ preview.quarantined_excluded === 1 ? '' : 's' }} left out</span>
          </div>
        </ModelFact>

        <p class="text-xs text-base-content/65">
          <template v-if="preview.vectors_included">
            A Clio that embeds with the same model reuses the vectors as they are. Any other Clio embeds the
            text again with its own model.
          </template>
          <template v-else>
            The vectors can't be vouched for, so they are left out and the importer embeds the text with its own model.
          </template>
        </p>

        <label class="flex items-start gap-3 cursor-pointer">
          <input v-model="includeSources" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
          <span class="text-sm">
            Include the original files<span v-if="preview.sources_bytes" class="text-base-content/60"> (about {{ formatBytes(preview.sources_bytes) }})</span>
            <span class="block text-xs text-base-content/60">
              Needed to open originals or re-chunk later. Only share files you have the right to redistribute.
            </span>
          </span>
        </label>
        <p class="text-xs text-base-content/60">
          The extracted text is readable by anyone you give the file to.
        </p>
      </div>

      <div class="modal-action">
        <button class="btn btn-ghost" @click="modal.close()">Cancel</button>
        <a class="btn btn-primary" :class="{ 'btn-disabled': !preview }" :href="href" download @click="modal.close()">
          <Download :size="16" />
          Download
        </a>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Download } from 'lucide-vue-next'
import http from '../utils/http'
import { useModal } from '../composables/useModal'
import { formatBytes } from '../utils/format'
import ModelFact from './ModelFact.vue'

const modal = useModal()
const target = ref(null)
const preview = ref(null)
const loading = ref(false)
const error = ref('')
const includeSources = ref(false)

const href = computed(() => preview.value && target.value
  ? `/api/collections/${encodeURIComponent(target.value.id)}/export?include_sources=${includeSources.value}`
  : '#')

async function open(collection) {
  target.value = collection
  includeSources.value = false
  preview.value = null
  error.value = ''
  loading.value = true
  modal.open()
  try {
    preview.value = (await http.get(`/api/collections/${encodeURIComponent(collection.id)}/export/preview`)).data
  } catch (err) {
    error.value = err.response?.data?.detail || err.message || 'Could not read this collection.'
  } finally {
    loading.value = false
  }
}

defineExpose({ open })
</script>
