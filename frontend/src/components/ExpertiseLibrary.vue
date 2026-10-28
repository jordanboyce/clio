<template>
  <div class="space-y-6">

    <!-- Header -->
    <div class="flex items-center justify-between flex-wrap gap-2">
      <div>
        <h2 class="text-xl font-bold">Saved instructions</h2>
        <p class="text-sm text-base-content/60 mt-0.5">
          Write guidance once (terminology, house style, what to check first) and apply it to any collection. Every answer in that collection follows it.
        </p>
      </div>
      <button v-if="canEdit" class="btn btn-primary" @click="startCreate">
        <Plus :size="18" />
        New instructions
      </button>
    </div>

    <!-- Packs are shared by every collection that uses them, so only an administrator may change them. -->
    <div v-if="!canEdit" class="alert py-2 text-sm" data-testid="expertise-readonly">
      Only an administrator can create or change saved instructions. You can still apply them to collections you own.
    </div>

    <!-- A pack was saved, but the scan found wording aimed at an AI assistant. -->
    <div v-if="savedWarning" class="alert alert-warning py-2 text-sm" role="alert" data-testid="expertise-flag-warning">
      <span>{{ savedWarning }}</span>
      <button class="btn btn-ghost btn-xs" @click="savedWarning = ''">Dismiss</button>
    </div>

    <!-- Editor panel (create / edit) -->
    <ExpertisePackEditor
      v-if="editing"
      :pack="editingPack"
      @save="onSave"
      @cancel="editing = false"
      @delete="onDelete"
    />

    <!-- Pack list -->
    <div v-if="!editing">
      <div v-if="store.loading" class="flex justify-center py-12">
        <span class="loading loading-spinner loading-md text-primary" />
      </div>

      <div v-else-if="store.error" class="alert alert-error">
        <span>{{ store.error }}</span>
      </div>

      <div v-else-if="store.packs.length === 0" class="card bg-base-200">
        <div class="card-body items-center text-center py-12">
          <BookOpen :size="40" class="text-base-content/30 mb-2" />
          <p class="text-base-content/60">No saved instructions yet.</p>
          <p class="text-sm text-base-content/40">For example: "Answer in plain English and define acronyms on first use."</p>
          <button v-if="canEdit" class="btn btn-primary btn-sm mt-4" @click="startCreate">
            <Plus :size="16" />
            New instructions
          </button>
        </div>
      </div>

      <div v-else class="space-y-3">
        <div
          v-for="pack in store.packs"
          :key="pack.id"
          class="card bg-base-200 transition-colors"
          :class="canEdit ? 'hover:bg-base-300 cursor-pointer' : ''"
          @click="canEdit && startEdit(pack)"
        >
          <div class="card-body py-4 px-5">
            <div class="flex items-start justify-between gap-3">
              <div class="flex-1 min-w-0">
                <div class="flex items-center gap-2">
                  <FileText :size="16" class="text-primary shrink-0" />
                  <h3 class="font-semibold truncate">{{ pack.name }}</h3>
                  <span
                    v-if="pack.flag"
                    class="badge badge-warning badge-sm shrink-0"
                    :title="`The scan found wording aimed at an AI assistant (${pack.flag.reason}). It is still applied, and agents are told to ignore any instructions in it.`"
                    data-testid="expertise-flagged"
                  >flagged</span>
                </div>
                <p v-if="pack.description" class="text-sm text-base-content/60 mt-1 line-clamp-2">
                  {{ pack.description }}
                </p>
                <p class="text-xs text-base-content/40 mt-1">
                  Updated {{ formatDate(pack.updated_at) }}
                </p>
              </div>
              <div v-if="canEdit" class="flex items-center gap-2 shrink-0">
                <button
                  class="btn btn-ghost btn-xs"
                  title="Edit"
                  @click.stop="startEdit(pack)"
                >
                  <Pencil :size="14" />
                </button>
                <button
                  class="btn btn-ghost btn-xs text-error"
                  title="Delete"
                  @click.stop="confirmDelete(pack)"
                >
                  <Trash2 :size="14" />
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Delete confirmation modal -->
    <dialog ref="deleteModal" class="modal">
      <div class="modal-box">
        <h3 class="font-bold text-lg">Delete these instructions?</h3>
        <p class="py-4 text-base-content/70">
          "<strong>{{ deletingPack?.name }}</strong>" will be removed from the library
          and detached from all collections. This cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn btn-ghost" @click="deleteModal.close()">Cancel</button>
          <button class="btn btn-error" :disabled="deleting" @click="doDelete">
            <span v-if="deleting" class="loading loading-spinner loading-xs" />
            Delete
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { Plus, FileText, Pencil, Trash2, BookOpen } from 'lucide-vue-next'
import { useExpertiseStore } from '../stores/expertiseStore'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'
import ExpertisePackEditor from './ExpertisePackEditor.vue'

const store = useExpertiseStore()
const userStore = useUserStore()
const ui = useUiStore()

// Same gate the server applies (require_admin): everyone on an open
// appliance, administrators only once private collections or ADMIN_EMAILS
// are on. The server still enforces it; this only hides what would be refused.
const canEdit = computed(() => userStore.adminConsole)
const savedWarning = ref('')

const editing = ref(false)
const editingPack = ref(null)   // null = creating new
const deletingPack = ref(null)
const deleting = ref(false)
const deleteModal = ref(null)

onMounted(() => store.fetchPacks())

function startCreate() {
  editingPack.value = null
  editing.value = true
}

function startEdit(pack) {
  editingPack.value = pack
  editing.value = true
}

function confirmDelete(pack) {
  deletingPack.value = pack
  deleteModal.value.showModal()
}

async function doDelete() {
  if (!deletingPack.value) return
  deleting.value = true
  try {
    await store.deletePack(deletingPack.value.id)
    deleteModal.value.close()
  } catch (err) {
    deleteModal.value.close()
    ui.toastError(describeError(err, 'Could not delete these instructions'))
  } finally {
    deleting.value = false
    deletingPack.value = null
  }
}

async function onSave(saved) {
  editing.value = false
  editingPack.value = null
  savedWarning.value = saved?.warning?.message
    ? `"${saved.name}" was saved. ${saved.warning.message}`
    : ''
}

function describeError(err, fallback) {
  if (err?.response?.status === 403) {
    return new Error(err.response.data?.detail || 'Only an administrator can change saved instructions.')
  }
  return new Error(err?.response?.data?.detail || err?.message || fallback)
}

function onDelete() {
  editing.value = false
  editingPack.value = null
}

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return iso
  }
}
</script>
