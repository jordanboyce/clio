<template>
  <ul :class="size === 'sm' ? 'space-y-3' : 'space-y-4'" data-testid="research-evidence">
    <li v-for="(item, i) in items" :key="i" :class="pointClass">
      <div>{{ item.point }}</div>
      <blockquote class="mt-1.5 border-l-2 border-base-300 pl-3 text-base-content/70" :class="quoteClass">
        <span class="italic">“{{ item.quote }}”</span>
        <span class="inline-flex items-center gap-1 ml-1 align-baseline whitespace-nowrap">
          <a
            v-if="item.page"
            class="link link-hover text-xs text-base-content/60"
            :href="pageUrl(collectionId, documentId, item.page)"
            target="_blank"
            rel="noopener"
            :title="item.match < 1 ? `Matched the source's text at ${Math.round(item.match * 100)}% (extraction noise)` : 'Quoted verbatim from the source'"
          >p. {{ item.page }}</a>
          <button
            class="btn btn-ghost btn-xs h-6 min-h-0 px-1.5 text-xs font-normal text-base-content/60"
            :title="`Copy the quote with its citation (${styleLabel(style)})`"
            :aria-label="`Copy quote ${i + 1} with its citation`"
            @click="copyQuote(item, i)"
          ><Check v-if="copied === i" :size="12" class="text-success" /><Quote v-else :size="12" />Cite</button>
        </span>
      </blockquote>
    </li>
  </ul>
</template>

<script setup>
import { computed, ref } from 'vue'
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
  // sm: side panels · md: a review read on its own · lead: its one-line summary
  size: { type: String, default: 'sm' },
})

const pointClass = computed(() => ({
  sm: 'text-sm leading-relaxed',
  md: 'text-[0.9375rem] leading-relaxed',
  lead: 'text-base leading-relaxed font-medium',
}[props.size] || 'text-sm leading-relaxed'))
const quoteClass = computed(() => (props.size === 'sm' ? 'text-xs leading-relaxed' : 'text-sm leading-relaxed font-normal'))

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
