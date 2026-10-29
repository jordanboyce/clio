<template>
  <ul class="space-y-2.5" data-testid="research-evidence">
    <li v-for="(item, i) in items" :key="i" class="text-sm leading-relaxed">
      <div>{{ item.point }}</div>
      <blockquote class="mt-1 border-l-2 border-base-300 pl-2.5 text-xs text-base-content/70 italic">
        “{{ item.quote }}”
        <a
          v-if="item.page"
          class="not-italic link link-hover text-base-content/55 ml-1 whitespace-nowrap"
          :href="pageUrl(collectionId, documentId, item.page)"
          target="_blank"
          rel="noopener"
          :title="item.match < 1 ? `Matched the paper's text at ${Math.round(item.match * 100)}% (extraction noise)` : 'Quoted verbatim from the paper'"
        >p. {{ item.page }}</a>
      </blockquote>
    </li>
  </ul>
</template>

<script setup>
import { pageUrl } from '../utils/researchApi.js'

// One verified statement per row: what it says, the paper's own words that
// back it, and a link to the page. Nothing renders here without its quote —
// the server drops any statement whose quote it could not find.
defineProps({
  items: { type: Array, default: () => [] },
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
})
</script>
