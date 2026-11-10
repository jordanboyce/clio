<template>
  <div v-if="features.length" ref="root" class="relative min-w-0">
    <button
      class="flex items-center gap-2.5 min-w-0 hover:text-base-content transition-colors"
      :aria-label="summary"
      :aria-expanded="open"
      aria-haspopup="dialog"
      :title="summary"
      data-testid="feature-strip"
      @click="open = !open"
    >
      <!-- Every feature by name on a wide window, a count on a narrower one. -->
      <span
        v-for="f in features"
        :key="f.id"
        class="hidden xl:flex items-center gap-1 flex-shrink-0"
        :class="STATES[f.state].text"
        :data-feature="f.id"
        :data-state="f.state"
      >
        <component :is="STATES[f.state].icon" :size="10" aria-hidden="true" />
        {{ f.short }}
      </span>
      <span class="xl:hidden flex items-center gap-1 flex-shrink-0">
        <component :is="STATES[worst].icon" :size="11" :class="STATES[worst].text" aria-hidden="true" />
        Features {{ onCount }}/{{ features.length }}
      </span>
    </button>

    <div
      v-if="open"
      class="absolute bottom-full left-0 mb-2 z-[60] w-[26rem] max-w-[90vw] max-h-[70vh] overflow-y-auto bg-base-100 border border-base-300 rounded-box shadow-lg p-3 text-sm text-base-content"
      role="dialog"
      aria-label="Feature status"
      data-testid="feature-panel"
    >
      <div class="flex items-baseline justify-between gap-3 mb-2">
        <h2 class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Feature status</h2>
        <span class="text-xs text-base-content/55 tabular-nums">{{ onCount }} of {{ features.length }} fully working</span>
      </div>
      <button
        type="button"
        class="btn btn-xs btn-outline mb-3"
        data-testid="feature-configure"
        @click="open = false; $emit('configure')"
      >
        <Settings :size="11" aria-hidden="true" />
        Change these in Settings
      </button>
      <ul class="space-y-2.5">
        <li v-for="f in features" :key="f.id" class="flex gap-2">
          <component :is="STATES[f.state].icon" :size="14" class="mt-0.5 flex-shrink-0" :class="STATES[f.state].text" aria-hidden="true" />
          <div class="min-w-0">
            <div class="flex items-baseline gap-2">
              <span class="font-medium">{{ f.label }}</span>
              <span class="text-xs" :class="STATES[f.state].text">{{ STATES[f.state].name }}</span>
            </div>
            <p class="text-xs text-base-content/65">{{ f.detail }}</p>
            <p v-if="f.fix" class="text-xs text-base-content/65 mt-0.5"><span class="font-medium">To fix:</span> {{ f.fix }}</p>
          </div>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CheckCircle2, CircleAlert, MinusCircle, Settings, XCircle } from 'lucide-vue-next'
import http from '../utils/http'
import { useProviderStore } from '../stores/providerStore'
import { useStatsStore } from '../stores/statsStore'

// Each state has its own icon as well as its own colour, so it can be told
// apart without colour.
const STATES = {
  on: { name: 'On', icon: CheckCircle2, text: 'text-success' },
  limited: { name: 'Limited', icon: CircleAlert, text: 'text-warning' },
  off: { name: 'Off', icon: MinusCircle, text: 'text-base-content/40' },
  unavailable: { name: 'Unavailable', icon: XCircle, text: 'text-error' },
}
// Short enough that all of them fit in the footer at 1280 px.
const SHORT = {
  semantic: 'Semantic', reranker: 'Rerank', ocr: 'OCR', transcription: 'Speech', figures: 'Figures',
  ai: 'AI', research: 'Research', links: 'Links', git: 'Git', mcp: 'MCP', pii: 'PII',
}
const REFRESH_MS = 60000

defineEmits(['configure'])

const providerStore = useProviderStore()
const statsStore = useStatsStore()
const server = ref([])
const open = ref(false)
const root = ref(null)

// The server only knows about a model set for the whole deployment. One
// connected in this browser counts too, and only the browser can see it.
const features = computed(() => server.value.map((f) => {
  const known = STATES[f.state] ? f : { ...f, state: 'unavailable' }
  const withShort = { ...known, short: SHORT[f.id] || f.label }
  if (f.id !== 'ai' || f.state !== 'off' || !providerStore.activeProviderId) return withShort
  return {
    ...withShort,
    state: 'on',
    detail: `Answers come from ${providerStore.activeProviderName}, connected in this browser. The connection is tested when a question is asked.`,
    fix: '',
  }
}))

const onCount = computed(() => features.value.filter((f) => f.state === 'on').length)
const worst = computed(() => ['unavailable', 'limited', 'off']
  .find((state) => features.value.some((f) => f.state === state)) || 'on')
const summary = computed(() => {
  const line = features.value.map((f) => `${f.label}: ${STATES[f.state].name}`).join(', ')
  return `Feature status. ${onCount.value} of ${features.value.length} fully working. ${line}. Click for details.`
})

async function refresh() {
  if (document.hidden) return
  try {
    const { data } = await http.get('/api/features')
    server.value = data.features || []
  } catch {
    // Advisory: a failed poll keeps what was last known, and the next one retries.
  }
}

function closeOnOutsideClick(event) {
  if (open.value && root.value && !root.value.contains(event.target)) open.value = false
}

// On the document, because focus stays on the strip's button while the panel is open.
function closeOnEscape(event) {
  if (open.value && event.key === 'Escape') open.value = false
}

let timer = null
onMounted(() => {
  refresh()
  timer = setInterval(refresh, REFRESH_MS)
  document.addEventListener('visibilitychange', refresh)
  window.addEventListener('clio:features-changed', refresh)
  document.addEventListener('click', closeOnOutsideClick)
  document.addEventListener('keydown', closeOnEscape)
})
onBeforeUnmount(() => {
  clearInterval(timer)
  document.removeEventListener('visibilitychange', refresh)
  window.removeEventListener('clio:features-changed', refresh)
  document.removeEventListener('click', closeOnOutsideClick)
  document.removeEventListener('keydown', closeOnEscape)
})
// The search model finishing its download should show without waiting for a poll.
watch(() => statsStore.embedding?.status, refresh)
watch(open, (isOpen) => { if (isOpen) refresh() })
</script>
