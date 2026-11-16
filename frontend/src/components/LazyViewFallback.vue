<template>
  <div class="flex flex-col items-center justify-center gap-3 py-16 text-center" role="status" data-testid="lazy-view-fallback">
    <template v-if="failed">
      <AlertTriangle :size="18" class="text-warning" aria-hidden="true" />
      <p class="text-sm text-base-content/70 max-w-xs">
        {{ name || 'This view' }} could not be loaded. The connection may have
        dropped, or Clio was updated while this tab was open; reloading picks
        up the new version.
      </p>
      <div class="flex flex-wrap justify-center gap-2">
        <button class="btn btn-sm btn-primary gap-1.5" data-testid="lazy-view-retry" @click="$emit('retry')">
          <RotateCcw :size="13" aria-hidden="true" />
          Try again
        </button>
        <button class="btn btn-sm btn-ghost gap-1.5" @click="reload">
          <RefreshCw :size="13" aria-hidden="true" />
          Reload Clio
        </button>
      </div>
    </template>
    <template v-else>
      <span class="loading loading-spinner loading-md text-primary/70" aria-hidden="true"></span>
      <p class="text-sm text-base-content/60">{{ name ? `Opening ${name}…` : 'Loading…' }}</p>
    </template>
  </div>
</template>

<script setup>
import { AlertTriangle, RefreshCw, RotateCcw } from 'lucide-vue-next'

defineProps({
  failed: { type: Boolean, default: false },
  // What is loading, as the ribbon names it ("Research"); empty for overlays.
  name: { type: String, default: '' },
})
defineEmits(['retry'])
const reload = () => window.location.reload()
</script>
