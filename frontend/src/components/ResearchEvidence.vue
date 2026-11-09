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
          :title="item.match < 1 ? `Matched the source's text at ${Math.round(item.match * 100)}% (extraction noise)` : 'Quoted verbatim from the source'"
        >p. {{ item.page }}</a>
        <button
          class="not-italic btn btn-ghost btn-xs h-5 min-h-0 px-1 ml-1 text-base-content/55"
          :title="`Copy the quote with its citation (${styleLabel(style)})`"
          :aria-label="`Copy quote ${i + 1} with its citation`"
          @click="copyQuote(item, i)"
        ><Check v-if="copied === i" :size="11" class="text-success" /><Quote v-else :size="11" />Cite</button>
      </blockquote>
    </li>
  </ul>
</template>

<script setup>
import { ref } from 'vue'
import { Check, Quote } from 'lucide-vue-next'
import { pageUrl, researchApi } from '../utils/researchApi.js'
import { styleLabel, useCitationStyle } from '../utils/citations.js'
import { useUiStore } from '../stores/uiStore'

// One verified statement per row: what it says, the source's own words that
// back it, and a link to the page. Nothing renders here without its quote —
// the server drops any statement whose quote it could not find.
const props = defineProps({
  items: { type: Array, default: () => [] },
  collectionId: { type: String, required: true },
  documentId: { type: String, required: true },
})

const style = useCitationStyle()
const copied = ref(null)
let timer = null

// “The source's words” (Okafor et al., 2021, p. 1187): the quote as
// verified, with the page printed on that page of the source.
async function copyQuote(item, index) {
  try {
    const out = await researchApi.citations(props.collectionId, {
      passages: [{ document_id: props.documentId, page: item.page || null }], style: style.value,
    })
    const cite = out.passages[0]
    const text = `“${item.quote}” ${cite.in_text}\n\n${cite.reference}`
    await navigator.clipboard.writeText(text)
    copied.value = index
    clearTimeout(timer)
    timer = setTimeout(() => { copied.value = null }, 1500)
  } catch (err) {
    useUiStore().toastError(err, 'Could not copy the citation')
  }
}
</script>
