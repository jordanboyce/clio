<template>
  <span
    v-if="info"
    class="inline-flex items-center gap-0.5 flex-shrink-0"
    :class="[
      compact ? 'text-[10px]' : 'badge badge-xs badge-outline',
      info.local_only ? (compact ? 'text-error' : 'badge-error') : (compact ? 'text-base-content/60' : 'badge-ghost'),
    ]"
    :title="classificationTitle(info, inherited)"
    data-testid="classification-badge"
    :data-local-only="info.local_only"
  >
    <ShieldBan v-if="info.local_only" :size="9" aria-hidden="true" />
    {{ info.label }}
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { ShieldBan } from 'lucide-vue-next'
import { useUserStore } from '../stores/userStore'
import { classificationInfo, classificationTitle } from '../utils/governance'

const props = defineProps({
  // A marking id, as stored on a collection or a document.
  marking: { type: String, default: '' },
  // The document has no marking of its own and shows its collection's.
  inherited: { type: Boolean, default: false },
  // Plain coloured text for dense rows, instead of a badge.
  compact: { type: Boolean, default: false },
})

const userStore = useUserStore()
const info = computed(() => classificationInfo(userStore.classifications, props.marking))
</script>
