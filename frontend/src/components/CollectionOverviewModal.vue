<template>
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="collection-overview-title" @close="modal.onClosed">
    <div class="modal-box max-w-lg">
      <h3 id="collection-overview-title" class="font-bold text-lg break-words">{{ target?.name }}</h3>

      <div v-if="loading" class="py-6 text-center"><span class="loading loading-spinner loading-sm"></span></div>
      <div v-else-if="error" class="alert alert-error text-sm mt-3">{{ error }}</div>
      <div v-else-if="stats" class="mt-3 space-y-5">
        <div>
          <dl class="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm">
            <template v-for="row in summary" :key="row.label">
              <dt class="text-base-content/60">{{ row.label }}</dt>
              <dd class="tabular-nums min-w-0 break-words">{{ row.value }}</dd>
            </template>
          </dl>
          <ul v-if="notes.length" class="text-xs text-base-content/60 mt-2 space-y-0.5">
            <li v-for="note in notes" :key="note">{{ note }}</li>
          </ul>
        </div>

        <section v-if="stats.by_extension.length" aria-labelledby="overview-types-title">
          <h4 id="overview-types-title" class="text-sm font-medium mb-1.5">File types</h4>
          <div class="max-h-44 overflow-y-auto">
            <table class="table table-xs">
              <thead>
                <tr>
                  <th scope="col" class="font-normal">Type</th>
                  <th scope="col" class="font-normal text-right">Sources</th>
                  <th scope="col" class="font-normal text-right">Size</th>
                  <th scope="col" class="font-normal text-right">Passages</th>
                </tr>
              </thead>
              <tbody class="tabular-nums">
                <tr v-for="row in stats.by_extension" :key="row.extension">
                  <td>{{ row.extension.replace(/^\./, '') }}</td>
                  <td class="text-right">{{ row.documents.toLocaleString() }}</td>
                  <td class="text-right">{{ formatBytes(row.bytes) }}</td>
                  <td class="text-right">{{ row.chunks.toLocaleString() }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-if="otherTypes > 0" class="text-xs text-base-content/60 mt-1">
            And {{ otherTypes }} less common type{{ otherTypes === 1 ? '' : 's' }}.
          </p>
        </section>

        <section v-if="stats.largest_documents.length" aria-labelledby="overview-largest-title">
          <h4 id="overview-largest-title" class="text-sm font-medium mb-1.5">Largest files</h4>
          <ul class="text-sm space-y-0.5">
            <li v-for="doc in stats.largest_documents" :key="doc.document_id" class="flex items-baseline justify-between gap-4">
              <span class="truncate min-w-0" :title="doc.filename">{{ doc.filename }}</span>
              <span class="tabular-nums text-base-content/60 flex-shrink-0">{{ formatBytes(doc.bytes) }}</span>
            </li>
          </ul>
        </section>

        <section v-if="stats.total_documents" aria-labelledby="overview-export-title">
          <h4 id="overview-export-title" class="text-sm font-medium mb-1.5">Export the file list</h4>
          <div class="flex flex-wrap gap-3">
            <label v-if="kindOptions.length > 1" class="flex-1 min-w-40">
              <span class="block text-xs text-base-content/60 mb-1">Include</span>
              <select v-model="fileType" class="select select-sm select-bordered w-full">
                <option value="">All sources ({{ stats.total_documents.toLocaleString() }})</option>
                <option v-for="kind in kindOptions" :key="kind.value" :value="kind.value">{{ kind.label }}</option>
              </select>
            </label>
            <label class="flex-1 min-w-40">
              <span class="block text-xs text-base-content/60 mb-1">Format</span>
              <select v-model="format" class="select select-sm select-bordered w-full">
                <option value="csv">Spreadsheet (CSV)</option>
                <option value="txt">File names only (TXT)</option>
              </select>
            </label>
          </div>
          <p class="text-xs text-base-content/60 mt-1.5">
            {{ format === 'csv'
              ? 'One row per source with its type, size, pages, passages, date added and label.'
              : 'One file name per line.' }}
          </p>
        </section>
      </div>

      <div class="modal-action">
        <button class="btn btn-ghost" @click="modal.close()">Close</button>
        <a v-if="stats?.total_documents" class="btn btn-primary" :href="exportHref" download>
          <Download :size="16" />
          Export files
        </a>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Download } from 'lucide-vue-next'
import { useModal } from '../composables/useModal'
import { fetchCollectionStats, fileListUrl, heldNote } from '../utils/collectionStats'
import { FAMILIES } from '../utils/fileTypes'
import { formatBytes } from '../utils/format'

const modal = useModal()
const target = ref(null)
const stats = ref(null)
const loading = ref(false)
const error = ref('')
const fileType = ref('')
const format = ref('csv')
let latestRequest = 0

const formatDate = (timestamp) => {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime())
    ? timestamp
    : date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

const summary = computed(() => {
  const s = stats.value
  const rows = [
    { label: 'Sources', value: s.total_documents.toLocaleString() },
    { label: 'Pages', value: s.total_pages.toLocaleString() },
    { label: 'Passages', value: s.total_chunks.toLocaleString() },
    { label: 'Size', value: formatBytes(s.total_bytes) },
  ]
  if (s.oldest_upload) {
    const oldest = formatDate(s.oldest_upload)
    const newest = formatDate(s.newest_upload)
    rows.push({ label: 'Added', value: oldest === newest ? oldest : `${oldest} to ${newest}` })
  }
  // Labels only earn a row once someone has actually applied one.
  const labels = Object.entries(s.by_sensitivity)
  if (labels.some(([label]) => label !== 'unlabeled')) {
    rows.push({
      label: 'Labels',
      value: labels.map(([label, n]) => `${label} ${n.toLocaleString()}`).join(', '),
    })
  }
  return rows
})

const notes = computed(() => {
  const s = stats.value
  const lines = []
  if (s.held_documents) lines.push(heldNote(s.held_documents))
  if (s.documents_without_chunks) {
    lines.push(`${s.documents_without_chunks.toLocaleString()} without text passages. Spreadsheets are queried as tables instead.`)
  }
  if (s.documents_without_recorded_size) {
    lines.push(`${s.documents_without_recorded_size.toLocaleString()} with no recorded size, not counted in the total.`)
  }
  return lines
})

const otherTypes = computed(() => stats.value.distinct_extensions - stats.value.by_extension.length)

const kindOptions = computed(() => Object.entries(stats.value?.by_kind || {}).map(([kind, n]) => ({
  value: kind,
  label: `${FAMILIES[kind]?.label || kind} (${n.toLocaleString()})`,
})))

const exportHref = computed(() => fileListUrl(target.value.id, { format: format.value, fileType: fileType.value }))

async function open(collection) {
  const request = ++latestRequest
  target.value = collection
  stats.value = null
  error.value = ''
  fileType.value = ''
  format.value = 'csv'
  loading.value = true
  modal.open()
  try {
    const data = await fetchCollectionStats(collection.id)
    if (request === latestRequest) stats.value = data
  } catch (err) {
    if (request === latestRequest) error.value = err?.message || 'Could not read this collection.'
  } finally {
    if (request === latestRequest) loading.value = false
  }
}

defineExpose({ open })
</script>
