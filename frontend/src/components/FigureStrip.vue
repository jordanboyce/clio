<template>
  <!-- The pictures the server pulled out of the cited page: charts, diagrams,
       photographs. Each opens at full size in a new tab. A figure the server
       cannot serve (404) disappears on its own: the passage is the evidence,
       the pictures are what the reader recognises. -->
  <ul
    v-if="figures.length"
    class="figure-strip flex flex-wrap gap-2 mt-2 list-none p-0"
    :aria-label="`Figures on ${location}`"
  >
    <li v-for="figure in figures" :key="figure.figure_number" class="m-0">
      <a
        :href="figure.href"
        target="_blank"
        rel="noopener noreferrer"
        class="figure-thumb block rounded-sm border border-base-300 bg-base-200 overflow-hidden focus-visible:outline-2 focus-visible:outline-primary"
        :aria-label="`Open ${figure.alt}`"
        :title="figure.caption || figure.alt"
      >
        <img
          :src="figure.href"
          :alt="figure.alt"
          loading="lazy"
          decoding="async"
          class="block w-full h-full object-contain"
          @error="failed.add(figure.figure_number)"
        >
      </a>
    </li>
  </ul>
</template>

<script setup>
import { computed, reactive, watch } from 'vue'
import { figureAlt, figureHref } from '../utils/answerEvidence'

const props = defineProps({
  // A search result or chat source carrying `figures`.
  source: { type: Object, required: true },
})
const failed = reactive(new Set())
watch(() => props.source.figures, () => failed.clear())

const location = computed(() => {
  const page = props.source.page_number
  return page ? `page ${page} of ${props.source.filename}` : props.source.filename
})
const figures = computed(() => (props.source.figures || [])
  .map(figure => ({ ...figure, href: figureHref(props.source, figure), alt: figureAlt(props.source, figure) }))
  .filter(figure => figure.href && !failed.has(figure.figure_number)))
</script>

<style scoped>
.figure-thumb { width: 6rem; height: 4.5rem; }
.figure-thumb img { transition: opacity 200ms ease-out; }
@media (prefers-reduced-motion: reduce) { .figure-thumb img { transition: none; } }
</style>
