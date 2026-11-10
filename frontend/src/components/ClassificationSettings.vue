<template>
  <section aria-labelledby="admin-classification" data-testid="classification-settings">
    <h2 id="admin-classification" class="text-sm font-semibold uppercase tracking-wider text-base-content/60 mb-3">Classification</h2>
    <div class="rounded-lg border border-base-300/60 bg-base-100 p-4 space-y-4">
      <p class="text-xs text-base-content/65 leading-relaxed max-w-[75ch]">
        Markings people can put on a collection or on a single source. One that is
        <span class="font-medium">local only</span> keeps the data on this server: no hosted AI model or
        embedding service, no MCP client or registered app, no catalogue lookup, email or webhook is given
        it. A collection that holds any local-only source is closed to those as a whole.
      </p>

      <ul class="space-y-2">
        <li v-for="(m, i) in markings" :key="i" class="flex flex-wrap items-center gap-2" data-testid="classification-row">
          <input v-model.trim="m.label" class="input input-bordered input-sm w-44" placeholder="Name" :aria-label="`Marking ${i + 1} name`" />
          <input v-model.trim="m.description" class="input input-bordered input-sm flex-1 min-w-40" placeholder="What it covers (optional)" :aria-label="`Marking ${i + 1} description`" />
          <label class="flex items-center gap-1.5 text-xs cursor-pointer">
            <input v-model="m.local_only" type="checkbox" class="checkbox checkbox-sm" />
            Local only
          </label>
          <button type="button" class="btn btn-ghost btn-xs" :aria-label="`Remove ${m.label || 'marking'}`" @click="markings.splice(i, 1)">
            <Trash2 :size="12" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <button type="button" class="btn btn-xs btn-outline" data-testid="classification-add" @click="markings.push({ label: '', description: '', local_only: true })">
        Add a marking
      </button>
      <p class="text-xs text-base-content/55 max-w-[75ch]">
        Removing a marking does not release what carries it: data marked with a name that is no longer here
        stays local only until someone changes its marking.
      </p>

      <div class="form-control max-w-xl">
        <label class="label pb-1" for="controlled-hosts"><span class="label-text font-medium">Servers that count as inside</span></label>
        <input id="controlled-hosts" v-model.trim="hosts" class="input input-bordered input-sm font-mono" placeholder="llm.corp.example, *.corp.example" data-testid="classification-hosts" />
        <p class="text-xs text-base-content/55 mt-1">
          A model or embedding server on your own network that is reached by name. Loopback, private
          addresses written as numbers and single-word names already count. A name is never looked up to
          decide.
        </p>
      </div>

      <p v-if="embeddingExternal" class="alert alert-warning py-2 text-xs" role="note">
        This server embeds with a provider outside the network. Collections that hold local-only data are
        indexed and searched by keyword only, and text indexed before it was marked has already been sent.
      </p>

      <div class="flex items-center justify-end gap-2">
        <span v-if="error" class="text-xs text-error" role="alert">{{ error }}</span>
        <span v-if="saved" class="text-xs text-success" role="status">Saved</span>
        <button class="btn btn-sm btn-primary" :disabled="saving" data-testid="classification-save" @click="save">
          <span v-if="saving" class="loading loading-spinner loading-xs"></span>
          Save classification settings
        </button>
      </div>
    </div>
  </section>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { Trash2 } from 'lucide-vue-next'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'

const userStore = useUserStore()
const markings = ref([])
const hosts = ref('')
const embeddingExternal = ref(false)
const saving = ref(false)
const saved = ref(false)
const error = ref('')

function apply(data) {
  markings.value = (data.data_classifications || []).map((m) => ({ ...m }))
  hosts.value = data.controlled_data_hosts || ''
}

async function load() {
  try {
    const { data } = await http.get('/api/config')
    apply(data || {})
    embeddingExternal.value = userStore.embeddingExternal
  } catch {
    error.value = 'Could not read the classification settings.'
  }
}

async function save() {
  saving.value = true
  saved.value = false
  error.value = ''
  try {
    await http.post('/api/config', {
      data_classifications: markings.value.filter((m) => m.label),
      controlled_data_hosts: hosts.value,
    })
    const { data } = await http.get('/api/config')
    apply(data || {})
    // Every picker and badge reads the list from the user store.
    await userStore.loadCurrentUser()
    embeddingExternal.value = userStore.embeddingExternal
    saved.value = true
    setTimeout(() => { saved.value = false }, 2500)
  } catch (err) {
    error.value = err?.message || 'Could not save.'
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
