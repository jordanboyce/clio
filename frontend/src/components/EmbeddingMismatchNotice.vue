<template>
  <div
    v-if="health && !health.ok"
    class="alert alert-warning items-start text-sm py-2.5 px-3"
    role="alert"
    data-testid="embedding-notice"
  >
    <AlertTriangle :size="16" class="shrink-0 mt-0.5" aria-hidden="true" />
    <div class="min-w-0 space-y-1.5">
      <template v-if="health.code === 'embedding_mismatch'">
        <p class="font-medium">New sources can't be added until this collection is re-indexed</p>
        <p class="text-xs opacity-80" data-testid="mismatch-detail">
          Its index holds {{ health.index_dim.toLocaleString() }}-dimensional vectors, but the
          embedding model now in use (<span class="font-mono">{{ health.model }}</span>) produces
          {{ health.model_dim.toLocaleString() }}-dimensional ones. Re-indexing rebuilds every
          source with the current model; your files are kept. Search still works on the
          existing index meanwhile.
        </p>
        <div v-if="health.can_reindex" class="flex items-center gap-2 pt-0.5">
          <template v-if="!confirming">
            <button class="btn btn-warning btn-xs" :disabled="busy" @click="confirming = true">
              Re-index collection
            </button>
          </template>
          <template v-else>
            <span class="text-xs">Rebuild every source now?</span>
            <button class="btn btn-warning btn-xs" :disabled="busy" data-testid="confirm-reindex" @click="startReindex">
              <span v-if="busy" class="loading loading-spinner loading-xs" />
              Yes, re-index
            </button>
            <button class="btn btn-ghost btn-xs" :disabled="busy" @click="confirming = false">Cancel</button>
          </template>
        </div>
        <p v-else class="text-xs">Ask the collection's owner to re-index it.</p>
        <p v-if="error" class="text-xs text-error" role="alert">{{ error }}</p>
      </template>

      <template v-else>
        <p class="font-medium">The embedding model isn't available</p>
        <p class="text-xs opacity-80 break-words">{{ health.message }}</p>
      </template>
    </div>
  </div>
</template>

<script setup>
// Tells a person why nothing new can be indexed and, when the cause is a model
// that no longer fits the index, offers the one action that fixes it. Used in
// the background-jobs drawer (a failed job points here) and above the sources
// list (so the state is visible before anyone tries to add something).
import { ref, watch, onMounted } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import http from '../utils/http'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'

const props = defineProps({
  collectionId: { type: String, required: true },
})
const emit = defineEmits(['reindex-started'])

const jobs = useBackgroundJobsStore()
const health = ref(null)
const confirming = ref(false)
const busy = ref(false)
const error = ref('')

async function refresh() {
  if (!props.collectionId) return
  try {
    const { data } = await http.get(`/api/collections/${props.collectionId}/embedding-health`)
    health.value = data
  } catch {
    // The notice is advisory: if the probe itself fails, say nothing rather
    // than show a warning about a warning.
    health.value = null
  }
}

async function startReindex() {
  busy.value = true
  error.value = ''
  try {
    const { data } = await http.post(`/api/collections/${props.collectionId}/reindex`)
    jobs.setReindexJob({
      id: data.job_id,
      collection_id: props.collectionId,
      status: 'pending',
      total_documents: 0,
      processed_documents: 0,
      current_file: null,
      started_at: new Date().toISOString(),
    })
    jobs.startPolling()
    confirming.value = false
    emit('reindex-started', data.job_id)
  } catch (e) {
    error.value = e.response?.data?.detail || 'Could not start re-indexing'
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
watch(() => props.collectionId, () => { confirming.value = false; refresh() })
// A finished re-index (or a finished upload) is the moment the answer can change.
watch(() => jobs.reindexJob?.status, (status) => {
  if (status === 'completed' || status === 'failed') refresh()
})

defineExpose({ refresh })
</script>
