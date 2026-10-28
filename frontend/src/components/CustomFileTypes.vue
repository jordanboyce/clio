<template>
  <section aria-labelledby="cft-heading" class="space-y-6" data-testid="cft-panel">
    <div class="flex items-center justify-between gap-2 flex-wrap">
      <h2 id="cft-heading" class="text-sm font-semibold uppercase tracking-wider text-base-content/60">
        Custom file types
      </h2>
      <span v-if="!canManage || forbidden" class="badge badge-ghost badge-xs text-base-content/50">Admin only</span>
    </div>

    <p v-if="!canManage" class="text-sm text-base-content/55 leading-relaxed max-w-[60ch]" data-testid="cft-not-admin">
      Only administrators can register custom file types. Ask an admin to add the extension you need.
    </p>

    <p v-else-if="forbidden" class="text-sm text-warning leading-relaxed max-w-[60ch]" data-testid="cft-forbidden">
      The server says you may not manage custom file types. Only an administrator (ADMIN_EMAILS) can.
    </p>

    <div v-else class="space-y-6">
      <!-- List of registered types -->
      <div class="rounded-lg border border-base-300/60 bg-base-100">
        <div class="px-4 py-3 border-b border-base-300/60">
          <div class="flex items-center justify-between gap-2">
            <span class="text-sm font-medium">Registered types</span>
            <button class="btn btn-sm btn-primary" data-testid="cft-add" @click="openAddDialog">
              <Plus :size="12" class="mr-1" aria-hidden="true" />
              Add type
            </button>
          </div>
        </div>

        <div v-if="store.loading && !store.loaded" class="text-center py-8 text-sm text-base-content/50">
          <span class="loading loading-spinner loading-sm mr-2"></span>Loading…
        </div>

        <template v-else-if="store.types.length === 0">
          <div class="p-8 text-center text-sm text-base-content/50 border border-dashed border-base-300">
            <FileJson :size="24" class="mx-auto mb-2 text-base-content/30" aria-hidden="true" />
            <p class="font-medium">No custom file types registered</p>
            <p class="text-xs mt-1">Click <span class="font-semibold">Add type</span> to register a JSON-like extension (e.g. <code class="px-1">.acme</code>).</p>
          </div>
        </template>

        <template v-else>
          <ul class="divide-y divide-base-300/60" data-testid="cft-list">
            <li v-for="t in store.types" :key="t.extension" class="p-4 flex flex-wrap items-center gap-4" :data-testid="`cft-row-${t.extension}`">
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
                  data-testid="cft-toggle"
                  :checked="t.enabled"
                  @change="toggleEnabled(t, $event.target.checked)"
                  :disabled="saving === t.extension"
                  :title="saving === t.extension ? 'Saving…' : undefined"
                  aria-label="Enable type"
                />
                <button class="btn btn-xs btn-ghost" data-testid="cft-files" @click="openUsage(t)" :disabled="saving === t.extension">
                  Files
                </button>
                <button
                  class="btn btn-xs btn-ghost"
                  data-testid="cft-edit"
                  @click="openEditDialog(t)"
                  :disabled="saving === t.extension"
                >
                  <Pencil :size="12" aria-hidden="true" />
                  <span class="hidden sm:inline">Edit</span>
                </button>
                <button
                  class="btn btn-xs btn-ghost text-error"
                  data-testid="cft-delete"
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
            {{ existing ? `Edit ${existing.extension}` : 'Add custom file type' }}
          </h3>

          <div class="space-y-4 mt-4 max-h-[60vh] overflow-y-auto">

            <!-- Step 1: Pick sample file and extension -->
            <template v-if="step === 'pick'">
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
                <label class="label p-0 pb-1" for="cft-sample">
                  <span class="label-text font-medium">Sample file</span>
                </label>
                <input
                  type="file"
                  id="cft-sample"
                  ref="fileInput"
                  @change="onFileSelected"
                  class="file-input file-input-bordered w-full"
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
                    data-testid="cft-use-ai"
                    v-model="form.useAi"
                    :disabled="!store.aiAvailable"
                  />
                  <span class="label-text font-medium">Use AI model to refine the suggestion</span>
                </label>
                <p v-if="!store.aiAvailable" class="text-xs text-warning mt-1" data-testid="cft-no-ai">
                  No chat model is configured — AI suggestion is unavailable.
                </p>
                <p v-else class="text-xs text-base-content/50 mt-1">
                  An excerpt of the file (bounded, lists shortened to 3 items) will be sent to the configured
                  chat provider. The model's answer is checked against the sample and falls back to rules on any failure.
                </p>
              </div>

              <div v-if="suggestError" class="alert alert-error py-2 text-sm" data-testid="cft-suggest-error">{{ suggestError }}</div>

              <div class="flex justify-end gap-2 pt-2">
                <button class="btn btn-outline" @click="closeDialog">Cancel</button>
                <button
                  class="btn btn-primary"
                  data-testid="cft-suggest"
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
                    <label class="label p-0 pb-1" for="cft-labels">
                      <span class="label-text font-medium">Labels (rename fields for search display)</span>
                    </label>
                    <textarea
                      id="cft-labels"
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
                        <label class="label p-0 pb-1" for="cft-rps">
                          <span class="label-text font-medium">Records per section</span>
                        </label>
                        <input
                          id="cft-rps"
                          type="number"
                          min="1"
                          max="100"
                          v-model.number="profile.records_per_section"
                          class="input input-bordered input-sm w-full"
                        />
                      </div>
                      <div>
                        <label class="label p-0 pb-1" for="cft-mvc">
                          <span class="label-text font-medium">Max value chars</span>
                        </label>
                        <input
                          id="cft-mvc"
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
                <div v-if="aiFellBack" class="alert alert-warning py-2 text-sm flex items-start gap-2" data-testid="cft-ai-fallback">
                  <AlertTriangle :size="14" class="flex-shrink-0 mt-0.5" aria-hidden="true" />
                  <div>
                    <p class="font-medium">AI suggestion fell back to rules</p>
                    <p class="text-xs mt-0.5">{{ aiError }}</p>
                  </div>
                </div>

                <!-- Live Preview -->
                <div class="border-t border-base-300/60 pt-4">
                  <div class="flex items-center justify-between mb-2 gap-2">
                    <div class="text-sm font-medium">Live preview</div>
                    <span v-if="previewLoading" class="loading loading-spinner loading-xs"></span>
                  </div>
                  <div v-if="!selectedFile" class="form-control mb-2">
                    <label class="label p-0 pb-1" for="cft-preview-sample">
                      <span class="label-text text-xs">Pick a sample file to preview this profile</span>
                    </label>
                    <input
                      id="cft-preview-sample"
                      type="file"
                      class="file-input file-input-bordered file-input-sm w-full"
                      @change="onPreviewSampleSelected"
                    />
                  </div>
                  <div v-if="previewError" class="alert alert-error py-2 text-sm" data-testid="cft-preview-error">{{ previewError }}</div>
                  <div v-else-if="preview" class="space-y-2" data-testid="cft-preview">
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

                <div v-if="saveError" class="alert alert-error py-2 text-sm" data-testid="cft-save-error">{{ saveError }}</div>

                <div class="flex justify-end gap-2 pt-4 border-t border-base-300/60">
                  <button v-if="!existing" class="btn btn-outline" @click="backToSuggest">Back</button>
                  <button class="btn btn-ghost text-error" @click="closeDialog">Cancel</button>
                  <button
                    class="btn btn-primary"
                    data-testid="cft-save"
                    @click="saveProfile"
                    :disabled="!!saving"
                  >
                    <span v-if="saving" class="loading loading-spinner loading-xs mr-1"></span>
                    {{ existing ? 'Save changes' : 'Add type' }}
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
            Already-indexed files of this type will <strong>stay searchable</strong>; they are not removed from the index.
          </p>
          <p v-if="deleteUsageLoading" class="text-sm text-base-content/60">
            <span class="loading loading-spinner loading-xs mr-1"></span>Counting indexed files…
          </p>
          <div v-else-if="deleteUsage" data-testid="cft-delete-usage" class="text-sm text-base-content/75 space-y-1">
            <p v-if="deleteUsage.total_documents > 0">
              <strong>{{ deleteUsage.total_documents }}</strong> indexed
              {{ deleteUsage.total_documents === 1 ? 'file uses' : 'files use' }} {{ deleteTarget?.extension }}:
            </p>
            <p v-else>No indexed files use {{ deleteTarget?.extension }}.</p>
            <ul v-if="deleteUsage.collections.length" class="list-disc pl-5 text-xs">
              <li v-for="c in deleteUsage.collections" :key="c.collection_id">
                {{ c.name }}: {{ c.documents }} file{{ c.documents === 1 ? '' : 's' }}
              </li>
            </ul>
            <p v-if="deleteUsage.total_documents > 0" class="text-xs text-base-content/55">
              They cannot be re-indexed once the type is gone (a re-index would drop them, because
              nothing could read them any more). Register {{ deleteTarget?.extension }} again to re-index them.
            </p>
          </div>
          <p v-else-if="deleteUsageError" class="text-xs text-base-content/55">{{ deleteUsageError }}</p>
          <div v-if="deleteError" class="alert alert-error py-2 text-sm mt-3" data-testid="cft-delete-error">{{ deleteError }}</div>
          <div class="modal-action">
            <button class="btn" @click="closeDeleteDialog">Cancel</button>
            <button class="btn btn-error" data-testid="cft-confirm-delete" @click="deleteType" :disabled="deleting">
              <span v-if="deleting" class="loading loading-spinner loading-xs mr-1"></span>
              Delete
            </button>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop">
          <button @click="closeDeleteDialog">close</button>
        </form>
      </dialog>

      <!-- Indexed files of a type: counts per collection, and the re-index offer -->
      <dialog ref="usageDialog" class="modal" aria-labelledby="usage-title">
        <div class="modal-box max-w-xl">
          <h3 id="usage-title" class="font-bold text-lg">{{ usageExt }} files</h3>
          <p v-if="usageLoading" class="py-4 text-sm text-base-content/60">
            <span class="loading loading-spinner loading-xs mr-1"></span>Counting indexed files…
          </p>
          <div v-else-if="usage" class="py-3 space-y-3" data-testid="cft-usage">
            <p v-if="usage.total_documents === 0" class="text-sm">No indexed files use {{ usageExt }}.</p>
            <template v-else>
              <p class="text-sm" data-testid="cft-usage-summary">
                <strong>{{ usage.total_documents }}</strong> indexed
                {{ usage.total_documents === 1 ? 'file uses' : 'files use' }} {{ usageExt }}.
                <template v-if="usage.total_out_of_date > 0">
                  <strong>{{ usage.total_out_of_date }}</strong>
                  {{ usage.total_out_of_date === 1 ? 'was' : 'were' }} indexed with an older profile.
                </template>
              </p>
              <p v-if="usageReindexable" class="text-xs text-base-content/60">
                Re-indexing re-reads only these files with the current profile. Everything else in the collection is left as it is.
              </p>
              <p v-else class="text-xs text-base-content/60" data-testid="cft-usage-no-reindex">
                {{ usageNoReindexReason }}
              </p>
              <ul class="divide-y divide-base-300/60 border border-base-300/60 rounded">
                <li v-for="c in usage.collections" :key="c.collection_id" class="p-3 flex flex-wrap items-center gap-2" :data-testid="`cft-usage-row-${c.collection_id}`">
                  <div class="flex-1 min-w-0">
                    <div class="text-sm font-medium truncate">{{ c.name }}</div>
                    <div class="text-xs text-base-content/55">
                      {{ c.documents }} file{{ c.documents === 1 ? '' : 's' }}<template v-if="c.out_of_date > 0">, {{ c.out_of_date }} with an older profile</template>
                    </div>
                  </div>
                  <template v-if="usageReindexable">
                    <button
                      v-if="c.can_reindex"
                      class="btn btn-sm btn-outline"
                      data-testid="cft-reindex"
                      :disabled="!!reindexing"
                      @click="reindexCollection(c)"
                    >
                      <span v-if="reindexing === c.collection_id" class="loading loading-spinner loading-xs mr-1"></span>
                      Re-index {{ c.documents }} file{{ c.documents === 1 ? '' : 's' }}
                    </button>
                    <span v-else class="text-xs text-base-content/55" data-testid="cft-owner-only">
                      Only this collection's owner can re-index it.
                    </span>
                  </template>
                </li>
              </ul>
            </template>
            <p v-if="usage.unavailable?.length" class="text-xs text-warning">
              Could not count {{ usage.unavailable.length }} collection{{ usage.unavailable.length === 1 ? '' : 's' }}.
            </p>
          </div>
          <div v-if="usageError" class="alert alert-error py-2 text-sm mt-2" data-testid="cft-usage-error">{{ usageError }}</div>
          <div class="modal-action">
            <button class="btn" @click="closeUsageDialog">Close</button>
          </div>
        </div>
        <form method="dialog" class="modal-backdrop">
          <button @click="closeUsageDialog">close</button>
        </form>
      </dialog>
    </div>
  </section>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useCustomFileTypesStore } from '../stores/customFileTypesStore'
import { Plus, Pencil, Trash2, AlertTriangle, FileJson } from 'lucide-vue-next'

const userStore = useUserStore()
const ui = useUiStore()
const jobs = useBackgroundJobsStore()
const store = useCustomFileTypesStore()

// May this person manage types? `adminConsole` is the server's own answer:
// an admin under private collections, or anyone on an open appliance (where
// the API does not gate). The API stays the authority: a 403 on any call
// hides the controls too (see `forbidden`).
const canManage = computed(() => !!userStore.adminConsole)
const forbidden = ref(false)

// Errors from utils/http arrive normalized: { status, message, detail }, where
// `message` is the server's `detail` text (a 400 names the bad field).
function errorText(e, fallback) {
  return (typeof e?.detail === 'string' && e.detail) || e?.message || fallback
}
// Returns true when the error was a 403 and has been handled.
function handleForbidden(e) {
  if (e?.status !== 403) return false
  forbidden.value = true
  dialog.value?.close()
  deleteDialog.value?.close()
  usageDialog.value?.close()
  return true
}

// State
const saving = ref(null)
const suggesting = ref(false)
const previewLoading = ref(false)
const deleting = ref(false)
const dialog = ref(null)
const deleteDialog = ref(null)
const usageDialog = ref(null)

// Add/edit dialog: 'pick' (extension + sample) then 'edit' (profile + preview)
const step = ref('pick')
const existing = ref(null) // the registered type being edited; null when adding
const form = ref({ extension: '', useAi: true })
const selectedFile = ref(null)
const suggestError = ref('')
const saveError = ref('')

// Profile editing state
const profile = ref({})
const includeText = ref('')
const excludeText = ref('')
const labelsText = ref('')
const preview = ref(null)
const previewError = ref('')
const aiFellBack = ref(false)
const aiError = ref('')
// Where the profile came from, and what it looked like then: the saved
// `source` is the origin only while the admin has not changed it.
const originSource = ref('manual')
const originSnapshot = ref('')

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

// The profile as the API takes it (everything except `source`). PUT replaces
// the whole profile, so every field the admin can see is always sent.
function profilePayload() {
  const p = profile.value
  const out = {
    label: p.label,
    description: p.description,
    format: p.format,
    kind: p.kind,
    record_path: p.record_path,
    title_field: p.title_field,
    include: parseFieldList(includeText.value),
    exclude: parseFieldList(excludeText.value),
    labels: parseLabels(labelsText.value),
    records_per_section: p.records_per_section,
    max_value_chars: p.max_value_chars,
    enabled: p.enabled !== false,
  }
  for (const k of Object.keys(out)) if (out[k] === undefined || out[k] === null) delete out[k]
  return out
}

function applyProfile(p, { source, fellBack = false, error = '' } = {}) {
  profile.value = { ...p }
  includeText.value = serializeFieldList(p.include)
  excludeText.value = serializeFieldList(p.exclude)
  labelsText.value = serializeLabels(p.labels)
  aiFellBack.value = fellBack
  aiError.value = error
  originSource.value = source || p.source || 'manual'
  originSnapshot.value = JSON.stringify(profilePayload())
}

// 'ai' / 'heuristic' only while the profile is still exactly what was
// suggested (or what was saved before); a hand edit makes it 'manual'.
const sourceToSave = () =>
  JSON.stringify(profilePayload()) === originSnapshot.value ? originSource.value : 'manual'

onMounted(() => { store.load({ force: true }) })

// Dialog management
function openAddDialog() {
  resetForm()
  nextTick(() => dialog.value?.showModal())
}

function openEditDialog(type) {
  resetForm()
  existing.value = { ...type }
  form.value.extension = type.extension
  applyProfile(type, { source: type.source })
  step.value = 'edit'
  nextTick(() => dialog.value?.showModal())
}

function closeDialog() {
  dialog.value?.close()
  resetForm()
}

function resetForm() {
  clearTimeout(previewTimer)
  previewSeq++
  step.value = 'pick'
  existing.value = null
  form.value = { extension: '', useAi: true }
  selectedFile.value = null
  suggestError.value = ''
  saveError.value = ''
  profile.value = {}
  includeText.value = ''
  excludeText.value = ''
  labelsText.value = ''
  preview.value = null
  previewError.value = ''
  previewLoading.value = false
  aiFellBack.value = false
  aiError.value = ''
  originSource.value = 'manual'
  originSnapshot.value = ''
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
  const dot = file.name.lastIndexOf('.')
  if (dot > 0 && !form.value.extension) {
    form.value.extension = normalizeExt(file.name.slice(dot))
  }
}

// A sample picked while editing, only used for the live preview.
function onPreviewSampleSelected(e) {
  const file = e.target.files?.[0]
  if (!file) return
  if (file.size > 2 * 1024 * 1024) {
    previewError.value = 'File is larger than 2 MB; pick a smaller excerpt'
    return
  }
  selectedFile.value = file
  previewError.value = ''
  debouncedPreview()
}

// Run suggest
async function runSuggest() {
  if (!selectedFile.value || !form.value.extension) return
  suggesting.value = true
  suggestError.value = ''
  try {
    const formData = new FormData()
    formData.append('extension', form.value.extension)
    formData.append('use_ai', String(form.value.useAi && store.aiAvailable))
    formData.append('file', selectedFile.value)

    const resp = await http.post('/api/custom-file-types/suggest', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
    const result = resp.data
    const fellBack = !!(result.ai?.requested && !result.ai?.used)
    form.value.extension = result.extension || form.value.extension
    applyProfile({ ...result.profile, extension: result.extension }, {
      source: result.source,
      fellBack,
      error: fellBack ? (result.ai?.error || 'The model did not return a usable profile.') : '',
    })
    preview.value = result.preview || null
    step.value = 'edit'
  } catch (e) {
    if (handleForbidden(e)) return
    suggestError.value = errorText(e, 'Failed to suggest profile')
  } finally {
    suggesting.value = false
  }
}

function backToSuggest() {
  clearTimeout(previewTimer)
  previewSeq++
  step.value = 'pick'
  profile.value = {}
  includeText.value = ''
  excludeText.value = ''
  labelsText.value = ''
  preview.value = null
  previewError.value = ''
  saveError.value = ''
}

// Debounced preview: the newest response wins, older ones are dropped.
let previewTimer = null
let previewSeq = 0
function debouncedPreview() {
  clearTimeout(previewTimer)
  previewTimer = setTimeout(runPreview, 300)
}

async function runPreview() {
  if (step.value !== 'edit' || !form.value.extension || !selectedFile.value) return
  const seq = ++previewSeq
  previewLoading.value = true
  previewError.value = ''
  try {
    const sample = await selectedFile.value.text()
    const resp = await http.post('/api/custom-file-types/preview', {
      extension: form.value.extension,
      sample,
      profile: profilePayload(),
    })
    if (seq !== previewSeq) return
    preview.value = resp.data.preview
  } catch (e) {
    if (seq !== previewSeq) return
    if (handleForbidden(e)) return
    previewError.value = errorText(e, 'Preview failed')
    preview.value = null
  } finally {
    if (seq === previewSeq) previewLoading.value = false
  }
}

watch(
  () => [
    profile.value.label, profile.value.format, profile.value.kind, profile.value.record_path,
    profile.value.title_field, includeText.value, excludeText.value, labelsText.value,
    profile.value.records_per_section, profile.value.max_value_chars, profile.value.description,
  ],
  () => { if (step.value === 'edit') debouncedPreview() },
)

onBeforeUnmount(() => { clearTimeout(previewTimer); previewSeq++ })

// Save profile
async function saveProfile() {
  const ext = form.value.extension || profile.value.extension
  if (!ext) return
  saving.value = ext
  saveError.value = ''
  try {
    const payload = { ...profilePayload(), source: sourceToSave() }
    const resp = await http.put(`/api/custom-file-types/${ext}`, payload)
    const { reading_changed: readingChanged, usage: savedUsage } = resp.data || {}
    ui.notify(`${existing.value ? 'Updated' : 'Added'} ${ext}`, 'success')
    closeDialog()
    await store.load({ force: true })
    // Files indexed under the old profile only change when re-read.
    if (readingChanged && savedUsage?.total_documents > 0) showUsage(ext, savedUsage)
  } catch (e) {
    if (handleForbidden(e)) return
    // A 400 carries the reason ("record_path ... not found"): keep it in the dialog.
    saveError.value = errorText(e, 'Failed to save the type')
  } finally {
    saving.value = null
  }
}

// Switch a type on or off. PUT replaces the whole profile, so the stored
// profile is sent back with only `enabled` changed.
async function toggleEnabled(type, enabled) {
  saving.value = type.extension
  try {
    const { extension, updated_at, updated_by, reading_updated_at, ...rest } = type
    await http.put(`/api/custom-file-types/${extension}`, { ...rest, enabled })
    ui.notify(`${extension} ${enabled ? 'enabled' : 'disabled'}`, 'success')
  } catch (e) {
    if (handleForbidden(e)) return
    ui.toastError(e, 'Failed to update type')
  } finally {
    saving.value = null
    await store.load({ force: true })
  }
}

// Delete confirmation: count what is indexed first, per collection.
const deleteTarget = ref(null)
const deleteUsage = ref(null)
const deleteUsageLoading = ref(false)
const deleteUsageError = ref('')
const deleteError = ref('')

async function confirmDelete(type) {
  deleteTarget.value = type
  deleteUsage.value = null
  deleteUsageError.value = ''
  deleteError.value = ''
  deleteUsageLoading.value = true
  nextTick(() => deleteDialog.value?.showModal())
  try {
    const resp = await http.get(`/api/custom-file-types/${type.extension}/usage`)
    deleteUsage.value = resp.data
  } catch (e) {
    if (handleForbidden(e)) return
    deleteUsageError.value = 'Could not count the indexed files of this type.'
  } finally {
    deleteUsageLoading.value = false
  }
}

function closeDeleteDialog() {
  deleteDialog.value?.close()
  deleteTarget.value = null
  deleteUsage.value = null
  deleteError.value = ''
}

async function deleteType() {
  if (!deleteTarget.value) return
  const ext = deleteTarget.value.extension
  deleting.value = true
  deleteError.value = ''
  try {
    const resp = await http.delete(`/api/custom-file-types/${ext}`)
    const n = resp.data?.affected_documents ?? deleteUsage.value?.total_documents ?? 0
    ui.notify(
      n > 0
        ? `Deleted ${ext}. ${n} indexed file${n === 1 ? ' uses' : 's use'} ${ext} and stay${n === 1 ? 's' : ''} searchable.`
        : `Deleted ${ext}`,
      'success',
    )
    closeDeleteDialog()
    await store.load({ force: true })
  } catch (e) {
    if (handleForbidden(e)) return
    deleteError.value = errorText(e, 'Failed to delete the type')
  } finally {
    deleting.value = false
  }
}

// Indexed files of a type, per collection, with the re-index offer.
const usageExt = ref('')
const usage = ref(null)
const usageLoading = ref(false)
const usageError = ref('')
const reindexing = ref('') // collection id being started

// Re-indexing is only possible while the server can still read the type.
const usageReindexable = computed(() => !!usage.value?.registered && !!usage.value?.enabled)
const usageNoReindexReason = computed(() =>
  usage.value?.registered
    ? `${usageExt.value} is switched off, so its files cannot be re-indexed. Switch it on first.`
    : `${usageExt.value} is no longer registered, so its files cannot be re-indexed (that would drop them). They stay searchable.`)

function showUsage(ext, data) {
  usageExt.value = ext
  usage.value = data
  usageError.value = ''
  usageLoading.value = false
  nextTick(() => usageDialog.value?.showModal())
}

async function openUsage(type) {
  usageExt.value = type.extension
  usage.value = null
  usageError.value = ''
  usageLoading.value = true
  nextTick(() => usageDialog.value?.showModal())
  try {
    const resp = await http.get(`/api/custom-file-types/${type.extension}/usage`)
    usage.value = resp.data
  } catch (e) {
    if (handleForbidden(e)) return
    usageError.value = errorText(e, 'Could not count the indexed files')
  } finally {
    usageLoading.value = false
  }
}

function closeUsageDialog() {
  usageDialog.value?.close()
  usage.value = null
  usageError.value = ''
}

async function reindexCollection(c) {
  if (reindexing.value) return
  reindexing.value = c.collection_id
  usageError.value = ''
  try {
    const { data } = await http.post(`/api/collections/${c.collection_id}/reindex-extensions`, {
      extensions: [usageExt.value],
    })
    jobs.setReindexJob({
      id: data.job_id,
      collection_id: c.collection_id,
      status: 'pending',
      total_documents: 0,
      processed_documents: 0,
      current_file: null,
      started_at: new Date().toISOString(),
    })
    jobs.startPolling()
    ui.notify(`Re-indexing ${usageExt.value} files in ${c.name}`, 'success')
    closeUsageDialog()
  } catch (e) {
    // 409: another re-index is running; 400/403: the reason is in the text.
    usageError.value = errorText(e, 'Could not start re-indexing')
  } finally {
    reindexing.value = ''
  }
}
</script>
