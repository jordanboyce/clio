<template>
  <article class="space-y-5" data-testid="research-brief">
    <template v-if="filled.length">
      <section v-for="section in filled" :key="section.key" :data-testid="`brief-section-${section.key}`">
        <h4 class="brief-heading">{{ section.title }}</h4>
        <ResearchEvidence
          :items="section.items"
          :collection-id="collectionId"
          :document-id="brief.document_id"
          :size="section.key === 'tldr' ? 'lead' : size"
        />
      </section>
    </template>
    <div v-else class="space-y-1" data-testid="brief-empty">
      <p class="text-sm font-medium">Nothing in this review could be checked against the source.</p>
      <p class="text-sm text-base-content/65">
        Every statement a review makes must quote the source, and none of these quotes were found in it, so nothing is shown.
        A thorough review reads more of the text and may do better.
      </p>
    </div>

    <!-- What could not be verified: said plainly, after what could -->
    <section v-if="brief.rejected || missing.length || notes.length" class="space-y-1" data-testid="brief-unverified">
      <h4 class="brief-heading">Not verified</h4>
      <p v-if="brief.rejected" class="text-sm text-base-content/70">
        {{ brief.rejected }} statement{{ brief.rejected === 1 ? ' was' : 's were' }} left out because the quote was not found in the source.
      </p>
      <p v-if="missing.length" class="text-sm text-base-content/70">
        Not covered by the passages read: {{ missing.join(', ').toLowerCase() }}.
      </p>
      <p v-for="note in notes" :key="note" class="text-sm text-base-content/70">{{ note }}</p>
    </section>

    <footer v-if="coverage" class="text-xs text-base-content/60">
      {{ depthLabel(brief.depth || brief.coverage?.depth) }} · {{ coverage }}<span v-if="brief.model"> · {{ brief.model }}</span>
    </footer>
  </article>
</template>

<script setup>
import { computed } from 'vue'
import ResearchEvidence from './ResearchEvidence.vue'
import { coverageText, depthLabel, otherNotes, uncovered } from '../utils/reviewView.js'

const props = defineProps({
  brief: { type: Object, required: true },
  collectionId: { type: String, required: true },
  // 'sm' in narrow panels; 'md' where the review is the page's subject.
  size: { type: String, default: 'sm' },
})

const filled = computed(() => (props.brief.sections || []).filter(s => s.items?.length))
const missing = computed(() => (filled.value.length ? uncovered(props.brief) : []))
const notes = computed(() => otherNotes(props.brief))
const coverage = computed(() => coverageText(props.brief))
</script>

<style scoped>
.brief-heading { margin-bottom: 0.5rem; font-size: 0.875rem; font-weight: 600; line-height: 1.3; color: var(--color-base-content); }
</style>
