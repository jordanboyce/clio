<template>
  <dialog ref="dialog" class="modal" aria-labelledby="supersede-title" @close="$emit('close')">
    <div class="modal-box max-w-lg">
      <h3 id="supersede-title" class="font-bold text-lg flex items-center gap-2">
        <Replace :size="18" aria-hidden="true" />
        Replaced by…
      </h3>
      <template v-if="doc">
        <p class="text-sm text-base-content/70 mt-1 break-all">{{ doc.filename }}</p>
        <p class="text-sm mt-3">
          Pick the document that is the current version. Search will rank
          <strong>{{ doc.filename }}</strong> below it and label it as an older version. Nothing is deleted, and the
          older text is still found when it is the only match.
        </p>

        <div v-if="doc.superseded_by" class="alert alert-info text-sm mt-3 py-2" data-testid="current-replacement">
          <span>Currently replaced by <strong>{{ doc.superseded_by_filename || doc.superseded_by }}</strong>.</span>
          <button class="btn btn-ghost btn-xs" :disabled="busy" data-testid="clear-replacement" @click="clear">Clear replacement</button>
        </div>

        <label class="input input-bordered input-sm flex items-center gap-2 mt-3">
          <Search :size="14" aria-hidden="true" />
          <input v-model="query" type="search" class="grow" placeholder="Find the newer document" aria-label="Find the newer document" />
        </label>
        <ul class="menu menu-sm bg-base-200 rounded-box mt-2 max-h-56 overflow-y-auto flex-nowrap" role="listbox" aria-label="Documents in this collection">
          <li v-for="c in candidates" :key="c.document_id">
            <button type="button" role="option" :aria-selected="picked === c.document_id"
              :class="{ 'menu-active': picked === c.document_id }" @click="picked = c.document_id">
              <span class="truncate">{{ c.filename }}</span>
            </button>
          </li>
          <li v-if="!loading && candidates.length === 0" class="px-3 py-2 text-xs text-base-content/50">No other documents match.</li>
        </ul>

        <input v-model="note" type="text" maxlength="500" class="input input-bordered input-sm w-full mt-3"
          placeholder="Note (optional) — e.g. new expense limits" aria-label="Note" />
        <p v-if="error" class="text-xs text-error mt-2" role="alert">{{ error }}</p>
      </template>
      <div class="modal-action">
        <button class="btn" :disabled="busy" @click="close">Cancel</button>
        <button class="btn btn-primary" :disabled="busy || !picked" data-testid="confirm-replacement" @click="save">
          <span v-if="busy" class="loading loading-spinner loading-xs"></span>
          Mark as replaced
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button @click="close">close</button></form>
  </dialog>
</template>

<script setup>
// Owner-facing picker: say that another document in this collection is the
// current version of `doc`. The server validates both documents, rejects
// self-reference and loops, and audits the change.
import { ref, watch, nextTick } from 'vue'
import { Replace, Search } from 'lucide-vue-next'
import http from '../utils/http'

const props = defineProps({
  doc: { type: Object, default: null },
  collectionId: { type: String, required: true },
})
const emit = defineEmits(['close', 'changed'])

const dialog = ref(null)
const query = ref('')
const note = ref('')
const picked = ref('')
const candidates = ref([])
const loading = ref(false)
const busy = ref(false)
const error = ref('')
let timer = null

async function loadCandidates() {
  if (!props.doc) return
  loading.value = true
  try {
    const { data } = await http.get('/documents', {
      params: { collection_id: props.collectionId, limit: 30, ...(query.value ? { q: query.value } : {}) },
    })
    candidates.value = (data.documents || []).filter(d => d.document_id !== props.doc.document_id)
  } catch {
    candidates.value = []
  } finally {
    loading.value = false
  }
}

watch(() => props.doc, async (doc) => {
  error.value = ''
  query.value = ''
  note.value = ''
  picked.value = ''
  if (doc) {
    await nextTick()
    dialog.value?.showModal?.()
    loadCandidates()
  } else {
    dialog.value?.close?.()
  }
})

watch(query, () => {
  clearTimeout(timer)
  timer = setTimeout(loadCandidates, 250)
})

function close() { emit('close') }

async function save() {
  busy.value = true
  error.value = ''
  try {
    await http.post(
      `/documents/${props.doc.document_id}/supersede`,
      { replaced_by: picked.value, ...(note.value.trim() ? { note: note.value.trim() } : {}) },
      { params: { collection_id: props.collectionId } },
    )
    emit('changed')
    close()
  } catch (err) {
    error.value = err?.message || 'Could not record the replacement'
  } finally {
    busy.value = false
  }
}

async function clear() {
  busy.value = true
  error.value = ''
  try {
    await http.delete(`/documents/${props.doc.document_id}/supersede`, {
      params: { collection_id: props.collectionId },
    })
    emit('changed')
    close()
  } catch (err) {
    error.value = err?.message || 'Could not clear the replacement'
  } finally {
    busy.value = false
  }
}
</script>
