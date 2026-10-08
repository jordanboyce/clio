<template>
  <dialog ref="dialogRef" class="modal" aria-labelledby="about-title" @close="$emit('close')">
    <div class="modal-box max-w-md p-0 overflow-hidden">
      <div class="px-6 pt-6 pb-5">
        <div class="flex items-center gap-3">
          <span class="brand-art brand-art-mark h-9 w-9 flex-shrink-0" aria-hidden="true"></span>
          <div class="min-w-0">
            <h2 id="about-title" class="text-base font-semibold leading-tight">Clio</h2>
            <p class="text-xs text-base-content/50">Ask your sources. Check the evidence. Connect your AI tools.</p>
          </div>
          <button type="button" class="side-icon-btn text-base-content/60 hover:text-base-content ml-auto self-start" aria-label="Close" @click="close">
            <X :size="15" aria-hidden="true" />
          </button>
        </div>

        <dl class="mt-5 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[13px]">
          <dt class="text-base-content/50">Version</dt>
          <dd class="tabular-nums">{{ info.version || '—' }}</dd>
          <dt class="text-base-content/50">Embeddings</dt>
          <dd class="truncate" :title="info.embedding">{{ info.embedding || 'local' }}</dd>
          <dt class="text-base-content/50">MCP</dt>
          <dd>{{ info.mcp ? 'On · streamable HTTP' : 'Off' }}</dd>
          <dt class="text-base-content/50">Mode</dt>
          <dd>{{ info.offline ? 'Offline (air-gapped)' : 'Connected' }}</dd>
        </dl>

        <div class="mt-5">
          <h3 class="side-label text-base-content/50 mb-1.5">What's new</h3>
          <ul class="text-[13px] leading-relaxed text-base-content/80 space-y-1">
            <li>Command palette and keyboard shortcuts (<span class="kbd-hint">{{ MOD }}</span> <span class="kbd-hint">K</span>).</li>
            <li>Answers read as prose on the page, with a stop button while they stream.</li>
            <li>ChatGPT-compatible <code class="text-xs">search</code> and <code class="text-xs">fetch</code> MCP tools, plus re-index and job tools for agents.</li>
            <li>Incremental folder sync: unchanged files are skipped, removed files can be pruned.</li>
          </ul>
        </div>
      </div>
      <div class="flex items-center gap-2 px-6 py-3 border-t border-base-300/60 bg-base-200/40 text-xs">
        <a href="https://github.com/jordanboyce/clio#readme" target="_blank" rel="noopener" class="link link-hover text-base-content/70">Documentation</a>
        <span class="text-base-content/25">·</span>
        <a href="https://github.com/jordanboyce/clio/blob/master/LICENSE" target="_blank" rel="noopener" class="link link-hover text-base-content/70">Apache-2.0</a>
        <span class="ml-auto text-base-content/40">Part of the Prometheus ecosystem</span>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { X } from 'lucide-vue-next'
import http from '../utils/http'
import { MOD } from '../utils/shortcuts'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])
const dialogRef = ref(null)
const info = ref({ version: '', embedding: '', mcp: null, offline: false })

let loaded = false
async function load() {
  if (loaded) return
  loaded = true
  try {
    const { data } = await http.get('/api/about')
    info.value = {
      version: data.version || '',
      embedding: data.embedding_model || '',
      mcp: !!data.mcp_enabled,
      offline: !!data.offline_mode,
    }
  } catch { /* the dialog still shows the static parts */ }
}

function sync() {
  const d = dialogRef.value
  if (!d) return
  if (props.open && !d.open) { load(); d.showModal?.() }
  else if (!props.open && d.open) d.close()
}
function close() { emit('close') }
watch(() => props.open, sync)
onMounted(sync)
</script>
