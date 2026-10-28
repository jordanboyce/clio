<template>
  <details v-if="visible.length" class="border-b border-base-300 px-3 py-2 text-xs" data-testid="supersede-suggestions">
    <summary class="cursor-pointer text-base-content/70">
      {{ visible.length }} possible older {{ visible.length === 1 ? 'version' : 'versions' }}
    </summary>
    <ul class="mt-2 space-y-2">
      <li v-for="s in visible" :key="key(s)" class="rounded-box bg-base-200 p-2" data-testid="suggestion">
        <div class="break-words">
          <span class="font-medium">{{ s.filename }}</span>
          <span class="text-base-content/60"> may be replaced by </span>
          <span class="font-medium">{{ s.replaced_by_filename }}</span>
        </div>
        <div class="text-base-content/60 mt-0.5">{{ reason(s) }}</div>
        <div class="flex gap-1 mt-1.5">
          <button class="btn btn-xs btn-primary" :disabled="busy === key(s)" data-testid="accept" @click="accept(s)">Mark as replaced</button>
          <button class="btn btn-xs btn-ghost" data-testid="dismiss" @click="dismiss(s)">Dismiss</button>
        </div>
      </li>
    </ul>
    <p v-if="error" class="text-error mt-2" role="alert">{{ error }}</p>
  </details>
</template>

<script setup>
// Pairs the server thinks are old/new versions of one file (names that differ
// by a version or year, "(1)" copies, identical text). Nothing is applied
// until an owner accepts it; dismissals are remembered in this browser only.
import { computed, onMounted, ref, watch } from 'vue'
import http from '../utils/http'
import { suggestionKey, suggestionReason } from '../utils/supersede'

const props = defineProps({ collectionId: { type: String, required: true } })
const emit = defineEmits(['changed'])

const suggestions = ref([])
const dismissed = ref(new Set())
const busy = ref('')
const error = ref('')

const storageKey = () => `clio.supersede.dismissed.${props.collectionId}`
const key = suggestionKey
const reason = suggestionReason

function loadDismissed() {
  try {
    dismissed.value = new Set(JSON.parse(localStorage.getItem(storageKey()) || '[]'))
  } catch {
    dismissed.value = new Set()
  }
}

async function load() {
  loadDismissed()
  try {
    const { data } = await http.get('/documents/supersede-suggestions', {
      params: { collection_id: props.collectionId },
    })
    suggestions.value = data.suggestions || []
  } catch {
    suggestions.value = []   // a convenience; never an error banner
  }
}

const visible = computed(() => suggestions.value.filter(s => !dismissed.value.has(key(s))))

function dismiss(s) {
  dismissed.value = new Set([...dismissed.value, key(s)])
  try { localStorage.setItem(storageKey(), JSON.stringify([...dismissed.value])) } catch { /* private mode */ }
}

async function accept(s) {
  busy.value = key(s)
  error.value = ''
  try {
    await http.post(
      `/documents/${s.document_id}/supersede`,
      { replaced_by: s.replaced_by },
      { params: { collection_id: props.collectionId } },
    )
    suggestions.value = suggestions.value.filter(x => key(x) !== key(s))
    emit('changed')
  } catch (err) {
    error.value = err?.message || 'Could not record the replacement'
  } finally {
    busy.value = ''
  }
}

onMounted(load)
watch(() => props.collectionId, load)
defineExpose({ load })
</script>
