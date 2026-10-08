<template>
  <!-- "Indexed with …": the one fact that decides whether a bundle's vectors
       can be reused, stated the same way in Export and in Import. -->
  <div class="rounded-lg border border-base-300 bg-base-200/50 px-3 py-2.5 text-sm">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-base-content/50">{{ title }}</div>
    <div class="mt-0.5 font-medium break-words">{{ embedding?.label || 'Unknown model' }}</div>
    <div v-if="embedding && !embedding.recorded" class="text-xs text-warning mt-1">
      This collection was indexed before Clio recorded its model, so whoever imports it has to embed the text again.
    </div>
    <slot />
  </div>
</template>

<script setup>
defineProps({
  embedding: { type: Object, default: null },
  title: { type: String, default: 'Indexed with' },
})
</script>
