<template>
  <dialog :ref="modal.dialogRef" class="modal" @close="modal.onClosed" aria-labelledby="share-modal-title">
    <div class="modal-box max-w-lg">
      <h3 id="share-modal-title" class="font-bold text-lg flex items-center gap-2">
        <Share2 :size="18" class="text-primary" aria-hidden="true" />
        {{ collectionId ? 'Share Collection' : 'Join a Shared Collection' }}
      </h3>
      <p v-if="collectionId" class="text-sm text-base-content/70 mt-1">{{ collectionName }}</p>
      <p v-else class="text-sm text-base-content/70 mt-1">
        Paste a share token someone sent you to get access to their collection.
      </p>

      <!-- Create new share -->
      <div v-if="collectionId" class="mt-4 space-y-3">
        <div class="flex flex-wrap gap-2 items-end">
          <div class="form-control flex-1 min-w-[8rem]">
            <label class="label py-1" for="share-permission"><span class="label-text text-xs">Permission</span></label>
            <select id="share-permission" v-model="newPermission" class="select select-bordered select-sm w-full">
              <option value="read">Read only</option>
              <option value="readwrite">Read + Write</option>
            </select>
          </div>
          <div class="form-control w-24">
            <label class="label py-1" for="share-expires"><span class="label-text text-xs">Expires</span></label>
            <select id="share-expires" v-model="newExpiresDays" class="select select-bordered select-sm w-full">
              <option :value="null">Never</option>
              <option :value="7">7 days</option>
              <option :value="30">30 days</option>
              <option :value="90">90 days</option>
            </select>
          </div>
          <button class="btn btn-primary btn-sm" @click="createNewShare" :disabled="creating">
            <span v-if="creating" class="loading loading-spinner loading-xs"></span>
            <Plus v-else :size="14" />
            Share
          </button>
        </div>

        <!-- Copied link notification -->
        <div v-if="copiedLink" class="alert alert-success py-2 text-sm" role="status">
          <CheckCircle :size="16" />
          Share link copied — anyone opening it lands on the join dialog.
        </div>

        <!-- Clipboard-unavailable fallback (non-HTTPS): manual copy field -->
        <div v-if="manualCopyUrl" class="flex gap-2 items-center">
          <input
            type="text"
            readonly
            class="input input-bordered input-sm flex-1 font-mono text-xs"
            :value="manualCopyUrl"
            aria-label="Share link — copy manually"
            @focus="$event.target.select()"
          />
          <button class="btn btn-ghost btn-xs" @click="manualCopyUrl = ''" aria-label="Dismiss">✕</button>
        </div>

        <!-- Inline error for share operations in this modal -->
        <div v-if="shareError" class="alert alert-error py-2 text-sm" role="alert">
          <span class="flex-1">{{ shareError }}</span>
          <button class="btn btn-xs btn-ghost" @click="shareError = ''" aria-label="Dismiss error">✕</button>
        </div>

        <!-- Email invitations (uses the permission/expiry selected above).
             Accepts several addresses at once — comma, space, or newline
             separated; the backend reports per address. -->
        <div class="flex gap-2 items-start">
          <textarea
            v-model="inviteEmail"
            rows="1"
            placeholder="Or email invitations to... (separate multiple with commas)"
            class="textarea textarea-bordered textarea-sm flex-1 min-h-8 leading-snug"
            aria-label="Recipient emails for share invitations"
            @keydown.enter.exact.prevent="emailInvite"
          ></textarea>
          <button class="btn btn-sm btn-outline gap-1" @click="emailInvite" :disabled="!inviteEmail.trim() || sendingInvite">
            <span v-if="sendingInvite" class="loading loading-spinner loading-xs"></span>
            <Mail v-else :size="14" />
            Send
          </button>
        </div>
        <div v-if="inviteSent" class="alert alert-success py-2 text-sm">
          <CheckCircle :size="16" />
          <span>
            Invitation emailed to {{ inviteSent }}.
          </span>
        </div>
        <!-- Per-address results for a batch invite -->
        <ul v-if="bulkResults.length" class="space-y-1 text-xs rounded border border-base-300 bg-base-200 p-2">
          <li v-for="r in bulkResults" :key="r.email" class="flex items-center gap-1.5">
            <CheckCircle v-if="r.email_sent" :size="12" class="text-success flex-shrink-0" />
            <AlertTriangle v-else :size="12" class="text-warning flex-shrink-0" />
            <span class="font-medium truncate max-w-[18ch]">{{ r.email }}</span>
            <span class="text-base-content/55 truncate">
              {{ r.error || r.email_error || (r.email_sent ? 'invited' : 'failed') }}
            </span>
          </li>
        </ul>
        <div v-if="inviteError" class="alert alert-error py-2 text-sm">{{ inviteError }}</div>

      </div>

      <!-- Active shares list -->
      <div v-if="collectionId" class="mt-4">
        <div class="text-xs font-semibold text-base-content/60 mb-2">Active Share Links</div>
        <div v-if="loadingShares" class="flex justify-center py-4">
          <span class="loading loading-spinner loading-sm"></span>
        </div>
        <div v-else-if="shares.length === 0" class="text-xs text-base-content/40 py-4 text-center">
          No active shares yet
        </div>
        <div v-else class="space-y-2 max-h-48 overflow-y-auto">
          <div
            v-for="share in shares"
            :key="share.id"
            class="flex items-center gap-2 p-2 rounded bg-base-200 text-xs"
          >
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-1.5 flex-wrap">
                <span class="badge badge-xs" :class="share.permission === 'readwrite' ? 'badge-warning' : 'badge-info'">
                  {{ share.permission === 'readwrite' ? 'R/W' : 'Read' }}
                </span>
                <span class="font-medium truncate max-w-[18ch]" :title="share.invited_email || 'Link share'">
                  {{ share.invited_email || 'Link share' }}
                </span>
                <span class="text-base-content/50">
                  · {{ share.accepted_users?.length || 0 }} joined
                </span>
                <span v-if="share.expires_at" class="text-base-content/40">
                  · expires {{ formatDate(share.expires_at) }}
                </span>
              </div>
              <div class="text-base-content/40 mt-0.5">
                created {{ formatDate(share.created_at) }}
              </div>
            </div>
            <button
              class="btn btn-ghost btn-xs"
              @click="copyShareLink(share.id)"
              title="Copy invite link"
              aria-label="Copy invite link to clipboard"
            >
              <Copy :size="12" aria-hidden="true" />
            </button>
            <!-- Two-step revoke: one click arms it, a second within 3s fires.
                 Revoking cuts off everyone who joined through this share. -->
            <button
              class="btn btn-xs"
              :class="confirmingRevokeId === share.id ? 'btn-error' : 'btn-ghost text-error'"
              @click="revokeShareLink(share.id)"
              :title="confirmingRevokeId === share.id ? 'Click again to revoke for everyone who joined' : 'Revoke share'"
              :aria-label="confirmingRevokeId === share.id ? 'Confirm revoke share link' : 'Revoke share link'"
            >
              <template v-if="confirmingRevokeId === share.id">Revoke?</template>
              <Trash2 v-else :size="12" aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>

      <!-- Accept share section -->
      <div class="mt-4" :class="collectionId ? 'border-t border-base-300 pt-4' : ''">
        <label for="accept-share-token" class="text-xs font-semibold text-base-content/60 mb-2 block">Accept a Share Link</label>
        <div class="flex gap-2">
          <input
            id="accept-share-token"
            v-model="acceptToken"
            type="text"
            placeholder="Paste share token here..."
            class="input input-bordered input-sm flex-1"
            @keyup.enter="acceptShareLink"
          />
          <button class="btn btn-sm btn-outline" @click="acceptShareLink" :disabled="!acceptToken || accepting">
            <span v-if="accepting" class="loading loading-spinner loading-xs"></span>
            Accept
          </button>
        </div>
        <div v-if="acceptResult" class="alert alert-success py-2 text-sm mt-2">
          Joined "{{ acceptResult.collection_name }}" ({{ acceptResult.permission }})
        </div>
        <div v-if="acceptError" class="alert alert-error py-2 text-sm mt-2">{{ acceptError }}</div>
      </div>

      <div class="modal-action">
        <button class="btn" @click="close">Close</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button>close</button></form>
  </dialog>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Share2, Plus, Copy, Trash2, CheckCircle, Mail, AlertTriangle } from 'lucide-vue-next'
import { createShare, createSharesBulk, listShares, revokeShare, acceptShare } from '../utils/sharingApi'
import { useUserStore } from '../stores/userStore'
import { useModal } from '../composables/useModal'

const userStore = useUserStore()

const props = defineProps({
  visible: Boolean,
  collectionId: String,
  collectionName: String,
  // Prefills the accept box — set by the ?share_token= deep link in emails.
  initialToken: { type: String, default: '' },
})

const emit = defineEmits(['close', 'shared'])

// Native <dialog> driven by the `visible` prop: open/close follow the prop,
// and a native close (Escape, backdrop) emits 'close' back to the parent.
const modal = useModal({ onClose: () => emit('close') })

const shares = ref([])
const loadingShares = ref(false)
const creating = ref(false)
const copiedLink = ref(false)
const manualCopyUrl = ref('')
const shareError = ref('')
const confirmingRevokeId = ref('')
let confirmingRevokeTimer = null
const newPermission = ref('read')
const newExpiresDays = ref(null)

const acceptToken = ref('')
const accepting = ref(false)
const acceptResult = ref(null)
const acceptError = ref('')

const inviteEmail = ref('')
const sendingInvite = ref(false)
const inviteSent = ref('')
const inviteError = ref('')
const bulkResults = ref([])

watch(() => props.visible, async (v) => {
  if (v) {
    modal.open()
    shareError.value = ''
    manualCopyUrl.value = ''
    if (props.initialToken) acceptToken.value = props.initialToken
    if (props.collectionId) await loadShareList()
  } else {
    modal.close()
  }
})

function shareUrl(shareId) {
  // The full deep link: opening it lands on the join dialog with the token
  // prefilled (App.vue handles ?share_token=). A bare token used to be
  // copied here, despite the button saying "Copy link".
  return `${window.location.origin}/?share_token=${shareId}`
}

function parseAddresses(text) {
  return [...new Set(
    text.split(/[\s,;]+/).map((a) => a.trim().toLowerCase()).filter((a) => a.includes('@'))
  )]
}

async function emailInvite() {
  const addresses = parseAddresses(inviteEmail.value)
  if (!addresses.length) return
  sendingInvite.value = true
  inviteSent.value = ''
  inviteError.value = ''
  bulkResults.value = []
  try {
    if (addresses.length === 1) {
      const share = await createShare(
        props.collectionId, newPermission.value, newExpiresDays.value, addresses[0]
      )
      if (share.email_sent) {
        inviteSent.value = addresses[0]
        inviteEmail.value = ''
      } else {
        inviteError.value = share.email_error || 'Email could not be sent'
      }
    } else {
      const data = await createSharesBulk(
        props.collectionId, addresses, newPermission.value, newExpiresDays.value
      )
      bulkResults.value = data.results || []
      if (bulkResults.value.every((r) => r.email_sent)) inviteEmail.value = ''
    }
    await loadShareList()
    emit('shared')
  } catch (err) {
    inviteError.value = err.message || 'Failed to send invitations'
  } finally {
    sendingInvite.value = false
  }
}

async function loadShareList() {
  loadingShares.value = true
  try {
    shares.value = await listShares(props.collectionId)
  } catch (err) {
    shareError.value = err.message || 'Could not load existing shares'
  } finally {
    loadingShares.value = false
  }
}

async function createNewShare() {
  creating.value = true
  shareError.value = ''
  try {
    const share = await createShare(props.collectionId, newPermission.value, newExpiresDays.value)
    await copyShareLink(share.share_id)
    await loadShareList()
    emit('shared')
  } catch (err) {
    shareError.value = err.message || 'Failed to create the share'
  } finally {
    creating.value = false
  }
}

async function copyShareLink(shareId) {
  const url = shareUrl(shareId)
  try {
    await navigator.clipboard.writeText(url)
    copiedLink.value = true
    manualCopyUrl.value = ''
    setTimeout(() => { copiedLink.value = false }, 3000)
  } catch {
    // Clipboard API unavailable (non-HTTPS): show the link for manual copy.
    manualCopyUrl.value = url
  }
}

async function revokeShareLink(shareId) {
  // First click arms; second click within 3s actually revokes.
  if (confirmingRevokeId.value !== shareId) {
    confirmingRevokeId.value = shareId
    clearTimeout(confirmingRevokeTimer)
    confirmingRevokeTimer = setTimeout(() => { confirmingRevokeId.value = '' }, 3000)
    return
  }
  clearTimeout(confirmingRevokeTimer)
  confirmingRevokeId.value = ''
  shareError.value = ''
  try {
    await revokeShare(shareId)
    await loadShareList()
  } catch (err) {
    shareError.value = err.message || 'Failed to revoke the share'
  }
}

async function acceptShareLink() {
  accepting.value = true
  acceptError.value = ''
  acceptResult.value = null
  try {
    acceptResult.value = await acceptShare(acceptToken.value.trim())
    acceptToken.value = ''
    emit('shared')
  } catch (err) {
    acceptError.value = err.message || 'Failed to accept share'
  } finally {
    accepting.value = false
  }
}

function formatDate(iso) {
  try {
    return new Date(iso).toLocaleDateString()
  } catch {
    return iso
  }
}

function close() {
  modal.close()
}
</script>
