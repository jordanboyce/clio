<template>
  <!-- Keyword-only search is a degraded state, not a broken one: one quiet
       line, the reason a click away. A mismatch blocks adding sources and
       keeps the loud treatment below. -->
  <!-- B8: the collection was indexed with a model this server does not
       have. Say so where the person is, and offer the fix that needs no
       terminal: a copy built with the server's model. -->
  <div
    v-if="health && !health.ok && health.fix === 'copy'"
    class="alert alert-warning items-start text-sm py-2.5 px-3"
    role="status"
    data-testid="embedding-notice"
  >
    <AlertTriangle :size="16" class="shrink-0 mt-0.5" aria-hidden="true" />
    <div class="min-w-0 space-y-1.5" data-testid="model-missing-notice">
      <p class="font-medium">This collection is searched by keyword</p>
      <p class="text-xs opacity-80">
        It was indexed with a model this server does not have
        (<span class="font-mono">{{ health.collection_model }}</span>). Copy it to the current model
        (<span class="font-mono">{{ health.server_model }}</span>) to search it by meaning again. The copy is
        built from the same sources; this collection keeps answering until you remove it.
      </p>
      <div class="flex flex-wrap items-center gap-2 pt-0.5">
        <button class="btn btn-warning btn-xs" :disabled="busy" data-testid="copy-to-current-model" @click="copyToCurrentModel">
          <span v-if="busy" class="loading loading-spinner loading-xs" />
          Copy to the current model
        </button>
        <template v-if="health.can_reindex">
          <button v-if="!confirming" class="btn btn-ghost btn-xs" :disabled="busy" @click="confirming = true">Re-index in place</button>
          <template v-else>
            <span class="text-xs">Rebuild it with {{ health.server_model }}? It is keyword-only until that finishes.</span>
            <button class="btn btn-warning btn-xs" :disabled="busy" data-testid="confirm-reindex" @click="startReindex(health.server_model)">Yes, re-index</button>
            <button class="btn btn-ghost btn-xs" :disabled="busy" @click="confirming = false">Cancel</button>
          </template>
        </template>
      </div>
      <p v-if="copied" class="text-xs" role="status">{{ copied }}</p>
      <p v-if="error" class="text-xs text-error" role="alert">{{ error }}</p>
    </div>
  </div>
  <details
    v-else-if="health && !health.ok && !['embedding_mismatch', 'embedding_model_mismatch'].includes(health.code)"
    class="group rounded-lg border border-warning/40 bg-warning/5 text-sm"
    data-testid="embedding-notice"
  >
    <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
      <AlertTriangle :size="14" class="shrink-0 text-warning" aria-hidden="true" />
      <span class="flex-1 min-w-0 font-medium">Search is using keywords only</span>
      <ChevronDown :size="14" class="shrink-0 text-base-content/50 transition-transform group-open:rotate-180" aria-hidden="true" />
    </summary>
    <div class="px-3 pb-2.5 space-y-1.5 text-xs text-base-content/70">
      <p>
        The search model isn't available. You can still add sources; they get search by
        meaning on their own once it is.
      </p>
      <p class="break-words select-text">{{ health.message }}</p>
    </div>
  </details>
  <div
    v-else-if="health && !health.ok"
    class="alert alert-warning items-start text-sm py-2.5 px-3"
    role="alert"
    data-testid="embedding-notice"
  >
    <AlertTriangle :size="16" class="shrink-0 mt-0.5" aria-hidden="true" />
    <div class="min-w-0 space-y-1.5">
      <template v-if="health.code === 'embedding_model_mismatch'">
        <p class="font-medium">This collection is searched by keyword until it is re-indexed</p>
        <p class="text-xs opacity-80" data-testid="model-mismatch-detail">
          Its vectors were made with <span class="font-mono">{{ health.stored_model }}</span>, but searching it would
          use <span class="font-mono">{{ health.model }}</span>. Re-indexing rebuilds every source with one model;
          your files are kept.
        </p>
        <div v-if="health.can_reindex" class="flex items-center gap-2 pt-0.5">
          <button v-if="!confirming" class="btn btn-warning btn-xs" :disabled="busy" @click="confirming = true">Re-index collection</button>
          <template v-else>
            <span class="text-xs">Rebuild every source now?</span>
            <button class="btn btn-warning btn-xs" :disabled="busy" data-testid="confirm-reindex" @click="startReindex()">Yes, re-index</button>
            <button class="btn btn-ghost btn-xs" :disabled="busy" @click="confirming = false">Cancel</button>
          </template>
        </div>
        <p v-else class="text-xs">Ask the collection's owner to re-index it.</p>
        <p v-if="error" class="text-xs text-error" role="alert">{{ error }}</p>
      </template>
      <template v-else-if="health.code === 'embedding_mismatch'">
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
            <button class="btn btn-warning btn-xs" :disabled="busy" data-testid="confirm-reindex" @click="startReindex()">
              <span v-if="busy" class="loading loading-spinner loading-xs" />
              Yes, re-index
            </button>
            <button class="btn btn-ghost btn-xs" :disabled="busy" @click="confirming = false">Cancel</button>
          </template>
        </div>
        <p v-else class="text-xs">Ask the collection's owner to re-index it.</p>
        <p v-if="error" class="text-xs text-error" role="alert">{{ error }}</p>
      </template>

    </div>
  </div>
  <!-- B2 upgrade: an older collection can be rebuilt to search better. Offered
       to whoever may re-index, once, and remembered when declined. -->
  <div
    v-else-if="health && health.ok && health.recipe_upgrade && health.can_reindex && !upgradeDeclined"
    class="rounded-lg border border-info/40 bg-info/5 text-sm px-3 py-2.5 space-y-1.5"
    role="status"
    data-testid="recipe-upgrade-notice"
  >
    <p class="font-medium">Search can find more in this collection</p>
    <p class="text-xs text-base-content/70">
      It was indexed before Clio read each passage together with the source it comes from and the
      heading above it. Re-indexing rebuilds it from your files that way. On Clio's own test set, the
      right passage was in the top ten for nearly every question instead of about a third. It takes
      about as long as adding these {{ (health.chunks || 0).toLocaleString() }} passages did, and
      search finds less until it finishes.
    </p>
    <div class="flex flex-wrap items-center gap-2 pt-0.5">
      <button class="btn btn-primary btn-xs" :disabled="busy" data-testid="recipe-upgrade-start" @click="startReindex()">
        <span v-if="busy" class="loading loading-spinner loading-xs" />
        Re-index now
      </button>
      <button class="btn btn-ghost btn-xs" :disabled="busy" data-testid="recipe-upgrade-later" @click="declineUpgrade">Not now</button>
    </div>
    <p v-if="error" class="text-xs text-error" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
// Tells a person when search is limited to keywords because the model is out of
// reach, or nothing new can be indexed because it no longer fits the index, and
// then offers the one action that fixes it. Used in
// the background-jobs drawer (a failed job points here) and above the sources
// list (so the state is visible before anyone tries to add something).
import { ref, watch, onMounted } from 'vue'
import { AlertTriangle, ChevronDown } from 'lucide-vue-next'
import http from '../utils/http'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useCollectionStore } from '../stores/collectionStore'

const props = defineProps({
  collectionId: { type: String, required: true },
})
const emit = defineEmits(['reindex-started', 'copy-started'])

const jobs = useBackgroundJobsStore()
const health = ref(null)
// "Not now" on the upgrade offer, remembered per collection in this browser.
const upgradeKey = () => `clio_recipe_offer_declined:${props.collectionId}`
const readDeclined = () => {
  try { return localStorage.getItem(upgradeKey()) === '1' } catch { return false }
}
const upgradeDeclined = ref(readDeclined())
const declineUpgrade = () => {
  upgradeDeclined.value = true
  try { localStorage.setItem(upgradeKey(), '1') } catch { /* storage is a convenience */ }
}
const copied = ref('')
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

// B8: a copy built with the server's model; the original keeps answering.
async function copyToCurrentModel() {
  busy.value = true
  error.value = ''
  try {
    const model = health.value.server_model
    const { data } = await http.post(`/api/collections/${props.collectionId}/clone`, { embedding_model: model })
    // The copy appears in the collection list; its job in Background work.
    useCollectionStore().loadCollections?.()
    copied.value = `A copy named “${data?.collection?.name || data?.name || 'the copy'}” is being built with ${model}. Its progress is under Background work.`
    emit('copy-started', data)
  } catch (e) {
    error.value = e?.message || 'Could not start the copy'
  } finally {
    busy.value = false
  }
}

// `model`: re-index with another model (B8); the collection's record changes
// only once the new vectors are saved.
async function startReindex(model = null) {
  busy.value = true
  error.value = ''
  try {
    const url = `/api/collections/${props.collectionId}/reindex`
    const { data } = model ? await http.post(url, { embedding_model: model }) : await http.post(url)
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
    error.value = e?.message || 'Could not start re-indexing'
  } finally {
    busy.value = false
  }
}

onMounted(refresh)
watch(() => props.collectionId, () => { confirming.value = false; upgradeDeclined.value = readDeclined(); refresh() })
// A finished re-index (or a finished upload) is the moment the answer can change.
watch(() => jobs.reindexJob?.status, (status) => {
  if (status === 'completed' || status === 'failed') refresh()
})

defineExpose({ refresh })
</script>
