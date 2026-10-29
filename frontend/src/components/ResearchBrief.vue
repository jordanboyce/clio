<template>
  <article class="space-y-3" data-testid="research-brief">
    <template v-for="section in filledSections" :key="section.key">
      <section>
        <h4 class="text-xs font-semibold uppercase tracking-wide text-base-content/55 mb-1.5">{{ section.title }}</h4>
        <ResearchEvidence :items="section.items" :collection-id="collectionId" :document-id="brief.document_id" />
      </section>
    </template>
    <p v-if="!filledSections.length" class="text-sm text-base-content/60">
      Nothing in this review could be checked against the paper, so it is empty.
    </p>
    <p v-if="emptySections.length" class="text-xs text-base-content/50">
      Not covered by the passages read: {{ emptySections.join(', ').toLowerCase() }}.
    </p>
    <footer class="text-xs text-base-content/50 space-y-0.5">
      <div>{{ coverageText }}</div>
      <div v-if="brief.rejected">
        {{ brief.rejected }} statement{{ brief.rejected === 1 ? ' was' : 's were' }} left out because the quote was not found in the paper.
      </div>
      <div v-for="note in otherNotes" :key="note">{{ note }}</div>
    </footer>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import ResearchEvidence from './ResearchEvidence.vue'

const props = defineProps({
  brief: { type: Object, required: true },
  collectionId: { type: String, required: true },
})

const filledSections = computed(() => (props.brief.sections || []).filter(s => s.items?.length))
const emptySections = computed(() => (props.brief.sections || []).filter(s => !s.items?.length && s.key !== 'tldr').map(s => s.title))

const coverageText = computed(() => {
  const c = props.brief.coverage || {}
  if (!c.of) return ''
  const depth = c.depth === 'thorough' ? 'Thorough review' : 'Brief review'
  const pages = c.pages?.length ? `, pages ${pageRanges(c.pages)}` : ''
  const share = c.share ? ` (${Math.round(c.share * 100)}% of the text)` : ''
  return `${depth}: read ${c.passages} of ${c.of} passages${share}${pages}${props.brief.model ? ` · ${props.brief.model}` : ''}`
})

const otherNotes = computed(() => (props.brief.coverage?.notes || [])
  .filter(n => !n.includes('were removed because their quote')))

function pageRanges(pages) {
  const out = []
  let start = pages[0]
  let prev = pages[0]
  for (const p of pages.slice(1).concat([null])) {
    if (p === prev + 1) { prev = p; continue }
    out.push(start === prev ? `${start}` : `${start}–${prev}`)
    start = prev = p
  }
  return out.join(', ')
}
</script>
