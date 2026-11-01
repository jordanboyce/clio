<template>
  <!--
    Acceptable-use acknowledgement. Two modes:
    - required: a takeover the person cannot dismiss until they accept — the
      backend refuses to add sources for them until then, so this is the
      honest UI for that state rather than a surprise 403 later;
    - voluntary: opened from the sidebar link to re-read the policy; closable.
  -->
  <div
    v-if="visible"
    class="fixed inset-0 z-[310] bg-base-content/30 flex items-center justify-center p-4 overflow-y-auto"
    role="dialog"
    aria-modal="true"
    aria-labelledby="aup-title"
  >
    <div ref="panelRef" tabindex="-1" class="w-full max-w-2xl my-auto rounded-box bg-base-100 border border-base-300 shadow-xl outline-none">
      <div class="px-6 pt-6 pb-4 border-b border-base-300/60 flex items-start gap-3">
        <ShieldCheck :size="22" class="text-primary mt-0.5 flex-shrink-0" aria-hidden="true" />
        <div class="flex-1 min-w-0">
          <h2 id="aup-title" class="text-lg font-semibold tracking-tight">Acceptable use</h2>
          <p class="text-xs text-base-content/55 mt-1">
            <template v-if="userStore.mustAcceptAup">
              Read and accept before adding sources. Version {{ version }}.
            </template>
            <template v-else-if="userStore.aup.accepted">
              You accepted version {{ version }}<span v-if="userStore.aup.accepted_at"> on {{ formatDate(userStore.aup.accepted_at) }}</span>.
            </template>
            <template v-else>
              Version {{ version }}.
            </template>
          </p>
        </div>
        <button
          v-if="!userStore.mustAcceptAup"
          class="btn btn-ghost btn-xs btn-circle"
          aria-label="Close"
          @click="close"
        >
          <X :size="14" />
        </button>
      </div>

      <div class="px-6 py-4 max-h-[55vh] overflow-y-auto">
        <div v-if="loading" class="flex items-center gap-2 text-sm text-base-content/60">
          <span class="loading loading-spinner loading-xs"></span> Loading policy…
        </div>
        <div v-else-if="error" class="alert alert-error py-2 text-sm">
          <span>{{ error }}</span>
          <button class="btn btn-xs btn-ghost" @click="load">Retry</button>
        </div>
        <div v-else class="prose prose-sm max-w-none text-base-content" v-html="rendered"></div>
      </div>

      <div class="px-6 py-4 border-t border-base-300/60 flex flex-col sm:flex-row sm:items-center gap-3">
        <label v-if="userStore.mustAcceptAup" class="flex items-start gap-2 cursor-pointer flex-1 text-sm">
          <input v-model="agreed" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
          <span>I have read this policy and understand that what I add is recorded against my identity.</span>
        </label>
        <div v-else class="flex-1"></div>
        <div class="flex items-center gap-2 justify-end">
          <button v-if="!userStore.mustAcceptAup" class="btn btn-sm btn-ghost" @click="close">Close</button>
          <button
            v-if="userStore.mustAcceptAup"
            class="btn btn-sm btn-primary"
            :disabled="!agreed || accepting || loading"
            @click="accept"
          >
            <span v-if="accepting" class="loading loading-spinner loading-xs"></span>
            I agree
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useOverlayFocus } from '../composables/useOverlayFocus'
import { ShieldCheck, X } from 'lucide-vue-next'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
import { useUiStore } from '../stores/uiStore'

const userStore = useUserStore()
const ui = useUiStore()

const loading = ref(false)
const error = ref('')
const text = ref('')
const version = ref(userStore.aup.version || '1')
const agreed = ref(false)
const accepting = ref(false)

const visible = computed(() => userStore.mustAcceptAup || userStore.aupOpen)
// Focus moves into the panel and stays there. Escape closes only the
// voluntary mode: the required takeover cannot be dismissed.
const panelRef = ref(null)
useOverlayFocus(visible, panelRef, {
  onEscape: () => { if (!userStore.mustAcceptAup) close() },
})
const rendered = computed(() => DOMPurify.sanitize(marked.parse(text.value || '')))

const formatDate = (iso) => {
  try { return new Date(iso).toLocaleDateString() } catch { return iso }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const resp = await http.get('/api/aup')
    text.value = resp.data.text || ''
    version.value = resp.data.version || version.value
  } catch (err) {
    error.value = err.message || 'Could not load the policy'
  } finally {
    loading.value = false
  }
}

async function accept() {
  accepting.value = true
  try {
    await userStore.acceptAup()
    ui.notify('Thanks — you can add sources now.', 'success')
  } catch (err) {
    ui.toastError(err, 'Could not record your acceptance')
  } finally {
    accepting.value = false
  }
}

function close() {
  userStore.aupOpen = false
}

watch(visible, (v) => { if (v) { agreed.value = false; load() } }, { immediate: true })
</script>
