<template>
  <!-- U2 (work order D1): files that could not be added, kept until retried
       or dismissed, each with why and what to do. -->
  <details v-if="items.length" class="rounded-lg border border-warning/40 bg-warning/5 text-sm" data-testid="failed-sources" :open="items.length <= 3">
    <summary class="flex items-center gap-2 px-3 py-2 cursor-pointer list-none [&::-webkit-details-marker]:hidden">
      <AlertTriangle :size="14" class="shrink-0 text-warning" aria-hidden="true" />
      <span class="flex-1 min-w-0 font-medium">{{ items.length }} could not be added</span>
      <button
        v-if="canRetry && retryable.length > 1"
        type="button"
        class="btn btn-ghost btn-xs"
        :disabled="busy"
        @click.prevent="retry(retryable)"
      >Retry all</button>
    </summary>
    <ul class="px-3 pb-2.5 space-y-2 max-h-56 overflow-y-auto">
      <li v-for="f in items" :key="f.key" class="text-xs" data-testid="failed-source">
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <div class="font-medium truncate" :title="f.filename">{{ f.filename }}</div>
            <p class="text-base-content/70">{{ f.reason }}</p>
            <p v-if="f.fix" class="text-base-content/55">{{ f.fix }}</p>
          </div>
          <div class="flex flex-col gap-1 shrink-0">
            <button v-if="canRetry && (f.retry || f.retryPath)" type="button" class="btn btn-xs" :disabled="busy" @click="retry([f])">Retry</button>
            <button type="button" class="btn btn-ghost btn-xs" :aria-label="`Dismiss ${f.filename}`" @click="jobs.dismissFailed(f.key)">Dismiss</button>
          </div>
        </div>
      </li>
    </ul>
    <p v-if="error" class="px-3 pb-2 text-xs text-error" role="alert">{{ error }}</p>
  </details>
</template>

<script setup>
import { computed, ref } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useUiStore } from '../stores/uiStore'

const props = defineProps({
  collectionId: { type: String, required: true },
  canRetry: { type: Boolean, default: false },
})

const jobs = useBackgroundJobsStore()
const ui = useUiStore()
const busy = ref(false)
const error = ref('')
const items = computed(() => jobs.failedFor(props.collectionId))
const retryable = computed(() => items.value.filter(f => f.retry || f.retryPath))

async function retry(entries) {
  busy.value = true
  error.value = ''
  try {
    await jobs.retryFailed(props.collectionId, entries)
    ui.notify(`Trying ${entries.length === 1 ? entries[0].filename : `${entries.length} files`} again`, 'info')
  } catch (err) {
    error.value = err?.message || 'Could not try again'
  } finally {
    busy.value = false
  }
}
</script>
