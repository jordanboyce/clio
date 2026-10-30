<template>
  <article class="py-5 min-w-0">
    <div class="flex items-start gap-3">
      <span class="text-sm text-base-content/70 tabular-nums pt-0.5 shrink-0">{{ index + 1 }}.</span>
      <div class="min-w-0 flex-1">
        <h4 class="font-semibold text-base break-words">{{ displayName }}</h4>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-sm text-base-content/75">
          <span v-if="location">{{ location }}</span>
          <span v-if="result.source_format" class="uppercase text-xs">{{ result.source_format }}</span>
          <span v-if="showLabel(result.sensitivity)" class="badge badge-sm" :class="labelBadgeClass(result.sensitivity)">{{ result.sensitivity }}</span>
        </div>
        <SupersedeNote :source="result" />
        <InstructionTextNote :source="result" />
        <div v-if="result.csv_columns && result.csv_values" class="mt-3 overflow-x-auto">
          <table class="table table-sm"><thead><tr><th v-for="col in result.csv_columns" :key="col">{{ col }}</th></tr></thead><tbody><tr><td v-for="col in result.csv_columns" :key="col"><span v-html="highlightSnippet(result.csv_values[col] ?? '', query)"></span></td></tr></tbody></table>
        </div>
        <p v-else class="mt-3 text-sm leading-7 whitespace-pre-wrap break-words max-w-prose" :class="{ 'quoted-passage': result.instruction_text }" :data-quoted="result.instruction_text ? 'true' : null" v-html="highlightSnippet(result.text_snippet || 'No passage preview available.', query)"></p>
        <div class="flex flex-wrap items-center gap-x-4 gap-y-1 mt-3">
          <a v-if="href" :href="href" target="_blank" rel="noopener noreferrer" class="link link-primary inline-flex items-center gap-1.5 min-h-11 text-sm font-medium" :aria-label="`Open ${result.filename} in a new tab`">{{ result.source_format === 'pdf' && result.page_number ? `Open page ${result.page_number}` : 'Open source' }}<ExternalLink :size="14" /></a>
          <details class="text-sm text-base-content/75">
            <summary class="cursor-pointer py-3">Match details</summary>
            <dl class="flex flex-wrap gap-x-4 gap-y-2 pb-2 text-xs">
              <div v-if="Number.isFinite(result.similarity_score)"><dt class="inline">Retrieval score: </dt><dd class="inline tabular-nums">{{ result.similarity_score.toFixed(3) }}</dd></div>
              <div v-if="result.extraction_method"><dt class="inline">Extraction: </dt><dd class="inline">{{ result.extraction_method }}</dd></div>
              <div v-if="result.source_type"><dt class="inline">Source: </dt><dd class="inline">{{ result.source_type.replaceAll('_', ' ') }}</dd></div>
            </dl>
            <p class="text-xs pb-3">The retrieval score ranks matches; it is not a confidence percentage.</p>
          </details>
        </div>
      </div>
    </div>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import { ExternalLink } from 'lucide-vue-next'
import { labelBadgeClass, showLabel } from '../utils/governance'
import { sourceHref } from '../utils/answerEvidence'
import { highlightSnippet } from '../utils/searchPresentation'
import SupersedeNote from './SupersedeNote.vue'
import InstructionTextNote from './InstructionTextNote.vue'
const props = defineProps({ result: { type: Object, required: true }, index: { type: Number, required: true }, query: { type: String, default: '' } })
const href = computed(() => sourceHref(props.result))
// Text pasted into the collection is stored as "<title>.html"; show the title.
const displayName = computed(() => {
  const name = props.result.filename || 'Untitled source'
  return props.result.source_type === 'text' ? name.replace(/\.html?$/i, '') : name
})
const location = computed(() => {
  const r = props.result
  if (r.csv_row_number != null) return `Row ${r.csv_row_number}`
  if (r.line_start) return `Lines ${r.line_start}${r.line_end && r.line_end !== r.line_start ? `–${r.line_end}` : ''}`
  if (r.symbol_name) return r.symbol_name
  return r.page_number ? `${r.source_format === 'pdf' ? 'Page' : 'Section'} ${r.page_number}` : ''
})
</script>

<style scoped>
/* A passage whose wording addresses an AI reads as a quotation, not as part of the page. */
.quoted-passage { border-inline-start: 3px solid var(--color-warning); padding-inline-start: 0.75rem; }
</style>
