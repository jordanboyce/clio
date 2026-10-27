<template>
  <section aria-labelledby="cft-heading" class="space-y-6">
    <div class="flex items-center justify-between gap-2 flex-wrap">
      <h2 id="cft-heading" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">
        Custom file types
      </h2>
      <span v-if="!isAdmin" class="badge badge-ghost badge-xs text-base-content/50">Admin only</span>
    </div>

    <p v-if="!isAdmin" class="text-sm text-base-content/55 leading-relaxed max-w-[60ch]">
      This panel is only visible to administrators. Configure custom JSON-like file extensions
      in <code class="text-xs font-mono">Settings → Indexing</code> (for owners) or ask an admin to add them here.
    </p>

    <div v-else class="space-y-6">
      <!-- List of registered types -->
      <div class="rounded-lg border border-base-300/60 bg-base-100">
        <div class="px-4 py-3 border-b border-base-300/60">
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-medium">Registered types</span>
            <button class="btn btn-sm btn-primary" @click="openAddDialog">
              <Plus :size="12" class="mr-1" aria-hidden="true" />
              Add type
            </button>
          </div>
        </div>

        <div v-if="loading" class="text-center py-8 text-sm text-base-content/50">
          <span class="loading loading-spinner loading-sm mr-2"></span>Loading…
        </div>

        <template v-else-if="types.length === 0">
          <div class="p-8 text-center text-sm text-base-content/50 border border-dashed border-base-300">
            <FileJson :size="24" class="mx-auto mb-2 text-base-content/30" aria-hidden="true" />
            <p class="font-medium">No custom file types registered</p>
            <p class="text-xs mt-1">Click <span class="font-semibold">Add type</span> to register a JSON-like extension (e.g. <code class="px-1">.acme</code>).</p>
          </div>
        </template>

        <template v-else>
          <ul class="divide-y divide-base-300/60">
            <li v-for="t in types" :key="t.extension" class="p-4 flex flex-wrap items-center gap-4">
              <div class="min-w-0 flex-1 basis-64">
                <div class="flex items-center gap-2 flex-wrap">
                  <code class="font-mono bg-base-200 px-1.5 py-0.5 rounded text-sm">{{ t.extension }}</code>
                  <span v-if="t.label && t.label !== t.extension" class="font-medium truncate max-w-[30ch]">{{ t.label }}</span>
                  <span class="badge badge-xs badge-outline">{{ t.kind }}</span>
                  <span class="badge badge-xs" :class="sourceBadgeClass(t.source)">{{ t.source }}</span>
                  <span v-if="!t.enabled" class="badge badge-xs badge-ghost">Disabled</span>
                </div>
                <div class="text-[11px] text-base-content/50 mt-0.5 flex flex-wrap gap-2">
                  <span>Updated {{ formatDay(t.updated_at) }} by {{ t.updated_by || '—' }}</span>
                </div>
              </div>
              <div class="flex items-center gap-2 flex-wrap justify-end">
                <input
                  type="checkbox"
                  class="toggle toggle-primary toggle-sm"
                  :checked="t.enabled"
                  @change="toggleEnabled(t, $event.target.checked)"
                  :disabled="saving === t.extension"
                  :title="saving === t.extension ? 'Saving…' : undefined"
                  aria-label="Enable type"
                />
                <button
                  class="btn btn-xs btn-ghost"
                  @click="openEditDialog(t)"
                  :disabled="saving === t.extension"
                >
                  <Pencil :size="12" aria-hidden="true" />
                  <span class="hidden sm:inline">Edit</span>
                </button>
                <button
                  class="btn btn-xs btn-ghost text-error"
                  @click="confirmDelete(t)"
                  :disabled="saving === t.extension"
                >
                  <Trash2 :size="12" aria-hidden="true" />
                  <span class="hidden sm:inline">Delete</span>
                </button>
              </div>
            </li>
          </ul>
        </template>
      </div>

      <!-- Add/Edit Dialog -->
      <dialog ref="dialog" class="modal" aria-labelledby="dialog-title">
        <div class="modal-box max-w-2xl w-full">
          <h3 id="dialog-title" class="font-bold text-lg">
            {{ editing ? `Edit ${editing.extension}` : 'Add custom file type' }}
          </h3>

          <div class="space-y-4 mt-4 max-h-[60vh] overflow-y-auto">

            <!-- Step 1: Pick sample file and extension -->
            <template v-if="!hasProfile">
              <p class="text-sm text-base-content/55">
                Pick a sample file to analyze. We'll suggest a profile, then you can edit it before saving.
              </p>

              <div class="form-control">
                <label class="label p-0 pb-1" for="cft-extension">
                  <span class="label-text font-medium">Extension</span>
                </label>
                <input
                  id="cft-extension"
                  v-model="form.extension"
                  type="text"
                  placeholder=".acme"
                  class="input input-bordered input-sm w-full"
                  @input="form.extension = normalizeExt(form.extension)"
                />
              </div>

              <div class="form-control">
                <label class="label p-0 pb-1">
                  <span class="label-text font-medium">Sample file</span>
                </label>
                <input
                  type="file"
                  id="cft-sample"
                  ref="fileInput"
                  @change="onFileSelected"
                  class="file-input file-input-bordered w-full"
                  accept=".json,.jsonl,.txt,.acme,.cfgx"
                  aria-describedby="cft-sample-hint"
                />
                <p id="cft-sample-hint" class="text-xs text-base-content/50 mt-1">
                  Max 2 MB. The extension will default to the file's extension.
                </p>
              </div>

              <div class="form-control">
                <label class="label cursor-pointer justify-start gap-2">
                  <input
                    type="checkbox"
                    class="checkbox checkbox-sm"
                    v-model="form.useAi"
                    :disabled="!aiAvailable"
                  />
                  <span class="label-text font-medium">Use AI model to refine the suggestion</span>
                </label>
                <p v-if="!aiAvailable" class="text-xs text-warning mt-1">
                  No chat model is configured — AI suggestion is unavailable.
                </p>
                <p v-else class="text-xs text-base-content/50 mt-1">
                  An excerpt of the file (bounded, lists shortened to 3 items) will be sent to the configured
                  chat provider. The model's answer is checked against the sample and falls back to rules on any failure.
                </p>
              </div>

              <div v-if="suggestError" class="alert alert-error py-2 text-sm">{{ suggestError }}</div>

              <div class="flex justify-end gap-2 pt-2">
                <button class="btn btn-outline" @click="closeDialog">Cancel</button>
                <button
                  class="btn btn-primary"
                  @click="runSuggest"
                  :disabled="suggesting || !form.extension || !selectedFile"
                >
                  <span v-if="suggesting" class="loading loading-spinner loading-xs mr-1"></span>
                  {{ suggesting ? 'Analyzing…' : 'Suggest profile' }}
                </button>
              </div>
            </template>

            <!-- Step 2: Show/edit profile with live preview -->
            <template v-else>
              <div class="space-y-4">
                <div class="grid gap-3 sm:grid-cols-2">
                  <div class="form-control">
                    <label class="label p-0 pb-1" for="cft-label">
                      <span class="label-text font-medium">Label</span>
                    </label>
                    <input
                      id="cft-label"
                      v-model="profile.label"
                      type="text"
                      placeholder="Acme export"
                      class="input input-bordered input-sm w-full"
                    />
                  </div>
                  <div class="form-control">
                    <label class="label p-0 pb-1" for="cft-kind">
                      <span class="label-text font-medium">Kind</span>
                    </label>
                    <select
                      id="cft-kind"
                      v-model="profile.kind"
                      class="select select-bordered select-sm w-full"
                    >
                      <option value="data">Data</option>
                      <option value="docs">Docs</option>
                      <option value="code">Code</option>
                    </select>
                  </div>
                  <div class="form-control sm:col-span-2">
                    <label class="label p-0 pb-1" for="cft-desc">
                      <span class="label-text font-medium">Description</span>
                    </label>
                    <textarea
                      id="cft-desc"
                      v-model="profile.description"
                      rows="2"
                      class="textarea textarea-bordered textarea-sm w-full"
                      placeholder="What this file type holds"
                    ></textarea>
                  </div>
                </div>

                <div class="grid gap-3 sm:grid-cols-2">
                  <div class="form-control">
                    <label class="label p-0 pb-1" for="cft-format">
                      <span class="label-text font-medium">Format</span>
                    </label>
                    <select
                      id="cft-format"
                      v-model="profile.format"
                      class="select select-bordered select-sm w-full"
                    >
                      <option value="auto">Auto</option>
                      <option value="json">JSON</option>
                      <option value="jsonl">JSON Lines</option>
                      <option value="text">Plain text</option>
                    </select>
                  </div>
                  <div class="form-control">
                    <label class="label p-0 pb-1" for="cft-record-path">
                      <span class="label-text font-medium">Record path</span>
                    </label>
                    <input
                      id="cft-record-path"
                      v-model="profile.record_path"
                      type="text"
                      placeholder="pumps or payload.rows"
                      class="input input-bordered input-sm w-full"
                    />
                    <p class="text-xs text-base-content/50 mt-1">Dotted path to the list/object of records. Empty = whole file.</p>
                  </div>
                  <div class="form-control">
                    <label class="label p-0 pb-1" for="cft-title-field">
                      <span class="label-text font-medium">Title field</span>
                    </label>
                    <input
                      id="cft-title-field"
                      v-model="profile.title_field"
                      type="text"
                      placeholder="name, title, label…"
                      class="input input-bordered input-sm w-full"
                    />
                    <p class="text-xs text-base-content/50 mt-1">Field that names each record (becomes the section heading).</p>
                  </div>
                  <div class="form-control sm:col-span-2">
                    <label class="label p-0 pb-1" for="cft-include">
                      <span class="label-text font-medium">Include fields (comma-separated, * wildcard allowed)</span>
                    </label>
                    <input
                      id="cft-include"
                      v-model="includeText"
                      type="text"
                      placeholder="name, location.*, tags"
                      class="input input-bordered input-sm w-full"
                    />
                    <p class="text-xs text-base-content/50 mt-1">Only these dotted paths are kept. Empty = keep everything.</p>
                  </div>
                  <div class="form-control sm:col-span-2">
                    <label class="label p-0 pb-1" for="cft-exclude">
                      <span class="label-text font-medium">Exclude fields (comma-separated)</span>
                    </label>
                    <input
                      id="cft-exclude"
                      v-model="excludeText"
                      type="text"
                      placeholder="id, checksum, blob"
                      class="input input-bordered input-sm w-full"
                    />
                    <p class="text-xs text-base-content/50 mt-1">These dotted paths are dropped (noise: ids, hashes, blobs, timestamps).</p>
                  </div>
                  <div class="form-control sm:col-span-2">
                    <label class="label p-0 pb-1">
                      <span class="label-text font-medium">Labels (rename fields for search display)</span>
                    </label>
                    <textarea
                      v-model="labelsText"
                      rows="3"
                      class="textarea textarea-bordered textarea-sm w-full font-mono text-xs"
                      placeholder="psi: &quot;Pressure (psi)&quot;&#10;location.site: &quot;Site&quot;"
                    ></textarea>
                    <p class="text-xs text-base-content/50 mt-1">One per line: <code class="text-xs">field.path: Display Name</code></p>
                  </div>
                  <div class="form-control sm:col-span-2">
                    <div class="grid gap-3 grid-cols-1 sm:grid-cols-2">
                      <div>
                        <label class="label p-0 pb-1">
                          <span class="label-text font-medium">Records per section</span>
                        </label>
                        <input
                          type="number"
                          min="1"
                          max="100"
                          v-model.number="profile.records_per_section"
                          class="input input-bordered input-sm w-full"
                        />
                      </div>
                      <div>
                        <label class="label p-0 pb-1">
                          <span class="label-text font-medium">Max value chars</span>
                        </label>
                        <input
                          type="number"
                          min="50"
                          max="20000"
                          v-model.number="profile.max_value_chars"
                          class="input input-bordered input-sm w-full"
                        />
                      </div>
                    </div>
                  </div>
                </div>

                <!-- AI fallback notice -->
                <div v-if="aiFellBack" class="alert alert-warning py-2 text-sm flex items-start gap-2">
                  <AlertTriangle :size="14" class="flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <div>
                    <p class="font-medium">AI suggestion fell back to rules</p>
                    <p class="text-xs mt-0.5">{{ aiError }}</p>
                  </div>
                </div>

                <!-- Live Preview -->
                <div class="border-t border-base-300/60 pt-4">
                  <div class="flex items-center justify-between mb-2">
                    <div class="text-sm font-medium">Live preview</div>
                    <span v-if="previewLoading" class="loading loading-spinner loading-xs"></span>
                  </div>
                  <div v-if="previewError" class="alert alert-error py-2 text-sm">{{ previewError }}</div>
                  <div v-else-if="preview" class="space-y-2">
                    <div class="grid grid-cols-3 gap-2 text-xs">
                      <div class="bg-base-200 p-2 rounded"><span class="text-base-content/50">Records</span><br><span class="font-mono tabular-nums">{{ preview.records }}</span></div>
                      <div class="bg-base-200 p-2 rounded"><span class="text-base-content/50">Sections</span><br><span class="font-mono tabular-nums">{{ preview.sections }}</span></div>
                      <div class="bg-base-200 p-2 rounded"><span class="text-base-content/50">Chars</span><br><span class="font-mono tabular-nums">{{ preview.chars }}</span></div>
                    </div>
                    <div class="bg-base-200 p-3 rounded font-mono text-xs whitespace-pre-wrap max-h-48 overflow-auto border border-base-300/60">
                      {{ preview.text }}
                    </div>
                  </div>
                  <div v-else class="text-center py-8 text-sm text-base-content/50 border border-dashed border-base-300 rounded">
                    Preview will appear here as you edit the profile.
                  </div>
                </div>

                <div class="flex justify-end gap-2 pt-4 border-t border-base-300/60">
                  <button class="btn btn-outline" @click="backToSuggest">Back</button>
                  <button class="btn btn-ghost text-error" @click="closeDialog">Cancel</button>
                  <button
                    class="btn btn-primary"
                    @click="saveProfile"
                    :disabled="saving"
                  >
                    <span v-if="saving" class="loading loading-spinner loading-xs mr-1"></span>
                    {{ editing ? 'Save changes' : 'Add type' }}
                  </button>
                </div>
              </div>
            </template>

          </div>
        </div>
        <form method="dialog" class="modal-backdrop">
          <button @click="closeDialog">close</button>
        </form>
      </dialog>

      <!-- Delete Confirmation Dialog -->
      <dialog ref="deleteDialog" class="modal" aria-labelledby="delete-title">
        <div class="modal-box">
          <h3 id="delete-title" class="font-bold text-lg text-error">Delete {{ deleteTarget?.extension || 'this type' }}?</h3>
          <p class="py-4">
            This will stop accepting <strong>{{ deleteTarget?.extension }}</strong> files for new uploads and folder scans.
            Already-indexed files of this type will <strong>stay searchable</strong> (they are not removed from the index).
          </p>
          <p v-if="affectedCount > 0" class="text-sm text-base-content/70">
            <strong>{{ affectedCount }}</strong> document{{ affectedCount === 1 ? '' : 's' }} currently indexed with this extension.
            You can re-index them later if you change the profile for a new extension.
          </p>
          <div class="modal-action">
            <button class="btn" @click="closeDeleteDialog">Cancel</button>
            <button class="btn btn-error" @click="deleteType" :disabled="deleting">
              <span v-if="deleting" class="loading loading-spinner loading-xs mr-1"></span>
              Delete
            </button>
          </div>
          <div v-if="affectedCount > 0" class="mt-4 pt-4 border-t border-base-300/60">
            <button
              class="btn btn-outline btn-sm w-full"
              @click="reindexAffected"
              :disabled="reindexing"
            >
              <span v-if="reindexing" class="loading loading-spinner loading-xs mr-1"></span>
              Re-index {{ affectedCount }} file{{ affectedCount === 1 ? '' : 's' }}
            </button>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop">
          <button @click="closeDeleteDialog">close</button>
        </form>
      </dialog>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { Plus, Pencil, Trash2, AlertTriangle, FileJson } from 'lucide-vue-next'

const userStore = useUserStore()
const ui = useUiStore()

const isAdmin = computed(() => userStore.isAdmin)

// State
const loading = ref(false)
const types = ref([])
const saving = ref(null)
const suggesting = ref(false)
const previewLoading = ref(false)
const deleting = ref(false)
const dialog = ref(null)
const deleteDialog = ref(null)

// Form state
const form = ref({
  extension: '',
  useAi: true,
})
const selectedFile = ref(null)
const suggestError = ref('')

// Profile editing state
const editing = ref(null)
const profile = ref({})
const includeText = ref('')
const excludeText = ref('')
const labelsText = ref('')
const preview = ref(null)
const previewError = ref('')
const aiFellBack = ref(false)
const aiError = ref('')

// AI availability
const aiAvailable = ref(false)

const hasProfile = computed(() => editing.value !== null)

const sourceBadgeClass = (source) => {
  switch (source) {
    case 'manual': return 'badge-ghost'
    case 'ai': return 'badge-info'
    case 'heuristic': return 'badge-success'
    default: return 'badge-ghost'
  }
}

const formatDay = (iso) => {
  if (!iso) return '—'
  try { return new Date(iso).toLocaleDateString() } catch { return iso.slice(0, 10) }
}

const normalizeExt = (ext) => {
  if (!ext) return ''
  ext = ext.trim().toLowerCase()
  if (ext && !ext.startsWith('.')) ext = '.' + ext
  return ext
}

// Parse comma-separated include/exclude text to array
const parseFieldList = (text) =>
  (text || '').split(',').map(s => s.trim()).filter(Boolean)

const serializeFieldList = (arr) => (arr || []).join(', ')

const parseLabels = (text) => {
  const out = {}
  for (const line of (text || '').split('\n')) {
    const i = line.indexOf(':')
    if (i > 0) {
      const k = line.slice(0, i).trim()
      const v = line.slice(i + 1).trim().replace(/^["']|["']$/g, '')
      if (k && v) out[k] = v
    }
  }
  return out
}

const serializeLabels = (obj) =>
  Object.entries(obj || {}).map(([k, v]) => `${k}: "${v}"`).join('\n')

// Load types on mount
async function loadTypes() {
  loading.value = true
  try {
    const resp = await http.get('/api/custom-file-types')
    types.value = resp.data?.types || []
  } catch (e) {
    console.error('Failed to load custom file types:', e)
  } finally {
    loading.value = false
  }
  await checkAiAvailable()
}

async function checkAiAvailable() {
  try {
    const resp = await http.get('/api/ai/models')
    aiAvailable.value = resp.data?.models?.length > 0
  } catch {
    aiAvailable.value = false
  }
}

onMounted(loadTypes)

// Dialog management
function openAddDialog() {
  resetForm()
  editing.value = null
  nextTick(() => dialog.value?.showModal())
}

function openEditDialog(type) {
  editing.value = { ...type }
  profile.value = { ...type }
  includeText.value = serializeFieldList(type.include)
  excludeText.value = serializeFieldList(type.exclude)
  labelsText.value = serializeLabels(type.labels)
  preview.value = null
  previewError.value = ''
  aiFellBack.value = false
  aiError.value = ''
  nextTick(() => {
    dialog.value?.showModal()
    debouncedPreview()
  })
}

function closeDialog() {
  dialog.value?.close()
  resetForm()
}

function resetForm() {
  form.value = { extension: '', useAi: true }
  selectedFile.value = null
  suggestError.value = ''
  editing.value = null
  profile.value = {}
  includeText.value = ''
  excludeText.value = ''
  labelsText.value = ''
  preview.value = null
  previewError.value = ''
  aiFellBack.value = false
  aiError.value = ''
}

// File selection
function onFileSelected(e) {
  const file = e.target.files?.[0]
  if (!file) return
  if (file.size > 2 * 1024 * 1024) {
    suggestError.value = 'File is larger than 2 MB; pick a smaller excerpt'
    return
  }
  selectedFile.value = file
  suggestError.value = ''
  // Default extension from filename
  const ext = '.' + file.name.split('.').pop()?.toLowerCase()
  if (ext && ext.length > 1 && !form.value.extension) {
    form.value.extension = ext
  }
}

// Run suggest
async function runSuggest() {
  if (!selectedFile.value || !form.value.extension) return
  suggesting.value = true
  suggestError.value = ''
  try {
    const formData = new FormData()
    formData.append('extension', form.value.extension)
    formData.append('use_ai', String(form.value.useAi))
    formData.append('file', selectedFile.value)

    const resp = await http.post('/api/custom-file-types/suggest', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })

    const result = resp.data
    profile.value = { ...result.profile, extension: result.extension }
    includeText.value = serializeFieldList(profile.value.include)
    excludeText.value = serializeFieldList(profile.value.exclude)
    labelsText.value = serializeLabels(profile.value.labels)
    aiFellBack.value = result.ai?.requested && !result.ai?.used
    aiError.value = result.ai?.error || ''
    preview.value = result.preview
    editing.value = { extension: result.extension } // mark as editing mode
    await nextTick()
    debouncedPreview()
  } catch (e) {
    suggestError.value = e.response?.data?.detail || 'Failed to suggest profile'
    console.error('Suggest failed:', e)
  } finally {
    suggesting.value = false
  }
}

function backToSuggest() {
  editing.value = null
  profile.value = {}
  includeText.value = ''
  excludeText.value = ''
  labelsText.value = ''
  preview.value = null
}

// Debounced preview
let previewTimer = null
function debouncedPreview() {
  if (previewTimer) clearTimeout(previewTimer)
  previewTimer = setTimeout(async () => {
    if (!editing.value || !form.value.extension) return
    previewLoading.value = true
    previewError.value = ''
    try {
      const currentProfile = {
        label: profile.value.label,
        description: profile.value.description,
        format: profile.value.format,
        kind: profile.value.kind,
        record_path: profile.value.record_path,
        title_field: profile.value.title_field,
        include: parseFieldList(includeText.value),
        exclude: parseFieldList(excludeText.value),
        labels: parseLabels(labelsText.value),
        records_per_section: profile.value.records_per_section,
        max_value_chars: profile.value.max_value_chars,
        enabled: profile.value.enabled,
      }
      const resp = await http.post('/api/custom-file-types/preview', {
        extension: form.value.extension,
        sample: await selectedFile.value?.text(),
        profile: currentProfile,
      })
      preview.value = resp.data.preview
    } catch (e) {
      previewError.value = e.response?.data?.detail || 'Preview failed'
      preview.value = null
    } finally {
      previewLoading.value = false
    }
  }, 300)
}

// Watch profile fields to update preview
watch(
  () => [profile.value.label, profile.value.format, profile.value.kind, profile.value.record_path, profile.value.title_field, includeText.value, excludeText.value, labelsText.value, profile.value.records_per_section, profile.value.max_value_chars],
  debouncedPreview,
  { deep: true }
)

// Save profile
async function saveProfile() {
  const ext = form.value.extension || profile.value.extension
  if (!ext) return
  saving.value = ext
  try {
    const payload = {
      label: profile.value.label,
      description: profile.value.description,
      format: profile.value.format,
      kind: profile.value.kind,
      record_path: profile.value.record_path,
      title_field: profile.value.title_field,
      include: parseFieldList(includeText.value),
      exclude: parseFieldList(excludeText.value),
      labels: parseLabels(labelsText.value),
      records_per_section: profile.value.records_per_section,
      max_value_chars: profile.value.max_value_chars,
      enabled: profile.value.enabled,
      source: editing.value?.source === 'ai' ? 'ai' : (editing.value?.source || 'manual'),
    }
    await http.put(`/api/custom-file-types/${ext}`, payload)
    ui.notify(`${editing.value ? 'Updated' : 'Added'} ${ext}`, 'success')
    closeDialog()
    await loadTypes()
  } catch (e) {
    ui.toastError(e, `Failed to ${editing.value ? 'update' : 'add'} type`)
  } finally {
    saving.value = null
  }
}

// Toggle enabled
async function toggleEnabled(type, enabled) {
  saving.value = type.extension
  try {
    await http.put(`/api/custom-file-types/${type.extension}`, { enabled })
    ui.notify(`${type.extension} ${enabled ? 'enabled' : 'disabled'}`, 'success')
    await loadTypes()
  } catch (e) {
    ui.toastError(e, 'Failed to update type')
  } finally {
    saving.value = null
  }
}

// Delete confirmation
async function confirmDelete(type) {
  deleteTarget.value = type
  // Use affected_documents from the API response
  deleteAffectedCount.value = type.affected_documents || 0
  nextTick(() => deleteDialog.value?.showModal())
}

const deleteTarget = ref(null)
const deleteAffectedCount = ref(0)
const reindexing = ref(false)

function closeDeleteDialog() {
  deleteDialog.value?.close()
  deleteTarget.value = null
  deleteAffectedCount.value = 0
  reindexing.value = false
}

async function deleteType() {
  if (!deleteTarget.value) return
  deleting.value = true
  try {
    const resp = await http.delete(`/api/custom-file-types/${deleteTarget.value.extension}`)
    // Update affected count from response for the re-index button
    if (resp.data?.affected_documents !== undefined) {
      deleteAffectedCount.value = resp.data.affected_documents
    }
    ui.notify(`Deleted ${deleteTarget.value.extension}`, 'success')
    closeDeleteDialog()
    await loadTypes()
  } catch (e) {
    ui.toastError(e, 'Failed to delete type')
  } finally {
    deleting.value = false
  }
}

async function reindexAffected() {
  if (!deleteTarget.value || reindexing.value) return
  reindexing.value = true
  try {
    const collectionStore = useCollectionStore()
    const collectionId = collectionStore.currentCollectionId
    if (!collectionId) {
      ui.notify('No collection selected', 'error')
      return
    }
    const resp = await http.post(`/api/collections/${collectionId}/reindex-extensions`, [deleteTarget.value.extension])
    ui.notify(`Re-indexing started for ${deleteTarget.value.extension} (job ${resp.data.job_id})`, 'success')
    closeDeleteDialog()
    // The background jobs store will pick up the new job
    const bg = useBackgroundJobsStore()
    bg.refreshJobs()
  } catch (e) {
    ui.toastError(e, 'Failed to start re-indexing')
  } finally {
    reindexing.value = false
  }
}
</script>