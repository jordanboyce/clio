<template>
  <!-- The cited page as a small picture, when the server can draw one (PDF
       pages and image files). It opens the original at that page. A preview
       the server cannot render (404) simply disappears: the passage is the
       evidence; the picture is a way to recognise it. -->
  <a
    v-if="src && !failed"
    :href="href || src"
    target="_blank"
    rel="noopener noreferrer"
    class="page-preview shrink-0 block rounded-sm border border-base-300 bg-base-200 overflow-hidden focus-visible:outline-2 focus-visible:outline-primary"
    :aria-label="`Open ${alt}`"
    :title="alt"
  >
    <img
      :src="src"
      :alt="alt"
      loading="lazy"
      decoding="async"
      class="block w-full h-auto object-cover object-top"
      :class="{ 'opacity-0': !loaded, 'opacity-100': loaded }"
      @load="loaded = true"
      @error="failed = true"
    >
  </a>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { previewAlt, previewHref } from '../utils/answerEvidence'

const props = defineProps({
  source: { type: Object, required: true },
  // Where a click goes: the original at the cited page. Falls back to the
  // thumbnail itself.
  href: { type: String, default: '' },
})
const src = computed(() => previewHref(props.source))
const alt = computed(() => previewAlt(props.source))
const loaded = ref(false)
const failed = ref(false)
watch(src, () => { loaded.value = false; failed.value = false })
</script>

<style scoped>
.page-preview { width: 5.5rem; min-height: 3.5rem; max-height: 8rem; }
.page-preview img { transition: opacity 200ms ease-out; }
@media (prefers-reduced-motion: reduce) { .page-preview img { transition: none; } }
</style>
