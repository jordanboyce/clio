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
          <dd>{{ info.offline ? 'Offline (air-gapped)' : 'Connected' }}<template v-if="info.profile === 'secure'"> · Secure profile</template></dd>
        </dl>

        <!-- Deployment check: the same report as `python scripts/preflight.py`.
             Nothing loads until asked; it stays hidden if this person may not see it. -->
        <div v-if="readiness.allowed" class="mt-4 text-[13px]" data-testid="readiness">
          <button
            v-if="!readiness.report"
            type="button"
            class="btn btn-ghost btn-xs gap-1.5 -ml-2"
            :disabled="readiness.loading"
            @click="checkReadiness"
          >
            <span v-if="readiness.loading" class="loading loading-spinner loading-xs"></span>
            <ShieldCheck v-else :size="13" aria-hidden="true" />
            Check this deployment
          </button>
          <div v-else>
            <p class="flex items-center gap-1.5 font-medium" :class="readiness.report.ready ? 'text-success' : 'text-error'">
              <ShieldCheck v-if="readiness.report.ready" :size="14" aria-hidden="true" />
              <ShieldAlert v-else :size="14" aria-hidden="true" />
              {{ readiness.report.ready ? 'Ready' : 'Not ready' }}
              <span class="font-normal text-base-content/60">
                · {{ readiness.report.counts.pass }} passed<template v-if="readiness.report.counts.warn">, {{ readiness.report.counts.warn }} to look at</template><template v-if="readiness.report.counts.fail">, {{ readiness.report.counts.fail }} failed</template>
              </span>
            </p>
            <ul v-if="attention.length" class="mt-1.5 space-y-1.5">
              <li v-for="c in attention" :key="c.id" class="text-base-content/80">
                <span class="font-medium" :class="c.status === 'fail' ? 'text-error' : 'text-warning'">{{ c.title }}.</span>
                {{ c.detail }}
                <span v-if="c.fix" class="block text-base-content/55">{{ c.fix }}</span>
              </li>
            </ul>
            <details class="mt-2">
              <summary class="cursor-pointer text-base-content/60 hover:text-base-content">Where data can go</summary>
              <ul class="mt-1 space-y-0.5 text-xs">
                <li v-for="e in readiness.report.egress" :key="e.kind" class="flex gap-2">
                  <span class="w-14 flex-shrink-0 font-medium" :class="egressClass[e.state]">{{ e.state }}</span>
                  <span class="text-base-content/80">{{ e.kind }}<span class="text-base-content/50"> · {{ e.destination }}</span></span>
                </li>
              </ul>
            </details>
          </div>
        </div>

        <div class="mt-5">
          <h3 class="side-label text-base-content/50 mb-1.5">What's new</h3>
          <ul class="text-[13px] leading-relaxed text-base-content/80 space-y-1">
            <li>Command palette and keyboard shortcuts (<span class="kbd-hint">{{ MOD }}</span> <span class="kbd-hint">K</span>).</li>
            <li>Answers read as prose on the page, with a stop button while they stream.</li>
            <li>ChatGPT-compatible <code class="text-xs">search</code> and <code class="text-xs">fetch</code> MCP tools, plus re-index and job tools for agents.</li>
            <li>Search picks the keyword/meaning balance for you; the manual modes live in search settings.</li>
            <li>Git repositories and watched folders, with an updates bell and optional webhook when sources change.</li>
          </ul>
        </div>
      </div>
      <div class="flex items-center gap-2 px-6 py-3 border-t border-base-300/60 bg-base-200/40 text-xs">
        <a href="https://github.com/jordanboyce/clio#readme" target="_blank" rel="noopener" class="link link-hover text-base-content/70">Documentation</a>
        <span class="text-base-content/25">·</span>
        <a href="https://github.com/jordanboyce/clio/blob/master/LICENSE" target="_blank" rel="noopener" class="link link-hover text-base-content/70">MIT License</a>
        <span class="ml-auto text-base-content/40">The record remembers.</span>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { X, ShieldCheck, ShieldAlert } from 'lucide-vue-next'
import http from '../utils/http'
import { MOD } from '../utils/shortcuts'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])
const dialogRef = ref(null)
const info = ref({ version: '', embedding: '', mcp: null, offline: false, profile: 'standard' })
const readiness = ref({ allowed: true, loading: false, report: null })
const egressClass = { none: 'text-success', internal: 'text-info', blocked: 'text-success', open: 'text-warning' }
const attention = computed(() => (readiness.value.report?.checks || []).filter(c => c.status !== 'pass'))

async function checkReadiness() {
  readiness.value.loading = true
  try {
    const { data } = await http.get('/api/system/readiness')
    readiness.value.report = data
  } catch (err) {
    // Admin-only deployments answer 401/403 for everyone else: just hide the section.
    if ([401, 403].includes(err?.status)) readiness.value.allowed = false
  } finally {
    readiness.value.loading = false
  }
}

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
      profile: data.profile || 'standard',
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
