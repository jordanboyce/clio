<template>
  <div>
    <div class="chat-markdown" :class="{ 'is-answer': answer }" @click="reviewCitation" v-html="html"></div>
    <!-- Source strip: every passage behind the answer, one glance, one click
         to the evidence. Numbers match the [Source N] citations in the prose. -->
    <div v-if="sources.length && !streaming" class="mt-3 flex flex-wrap gap-1.5" role="list" aria-label="Sources" @click="reviewCitation">
      <button
        v-for="(source, index) in visibleSources" :key="index" type="button" role="listitem"
        class="btn btn-xs btn-ghost h-auto min-h-0 py-1 px-2 gap-1.5 font-normal max-w-full border border-base-300 hover:border-base-content/30"
        :data-source-chip="index + 1"
        :title="chipTitle(source)"
        :aria-label="`Review source ${index + 1}: ${source.filename}`"
      >
        <span class="font-mono text-base-content/60 tabular-nums">{{ index + 1 }}</span>
        <span class="truncate max-w-[12rem]">{{ source.filename }}</span>
        <span v-if="source.page_number" class="text-base-content/50">p.{{ source.page_number }}</span>
      </button>
      <button
        v-if="sources.length > MAX_CHIPS" type="button"
        class="btn btn-xs btn-ghost h-auto min-h-0 py-1 px-2 font-normal text-base-content/60"
        :aria-label="`Review all ${sources.length} sources`"
        @click.stop="openAll"
      >+{{ sources.length - MAX_CHIPS }} more</button>
    </div>
    <details v-if="sources.length" ref="evidencePanel" class="mt-4 border-t border-base-300 pt-3">
      <summary class="cursor-pointer text-sm font-medium py-1">
        Review evidence <span class="font-normal text-base-content/70">· {{ sources.length }} passages</span>
      </summary>
      <p class="text-xs text-base-content/70 mt-2 mb-3 leading-relaxed">
        Retrieved passages, not a verification of every claim. Check wording, dates, and exceptions in the original.
      </p>
      <ol class="divide-y divide-base-300">
        <li v-for="(source, index) in sources" :key="index" :id="`${id}-source-${index + 1}`"
          tabindex="-1" class="py-3 scroll-mt-4 rounded-sm"
          :class="{ 'bg-primary/5': selected === index + 1 }">
          <div class="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm">
            <span class="text-base-content/70">[Source {{ index + 1 }}]</span>
            <span class="font-semibold break-all">{{ source.filename }}</span>
            <span v-if="source.page_number" class="text-xs text-base-content/70">Page / section {{ source.page_number }}</span>
            <span v-if="source.sensitivity && source.sensitivity !== 'public'" class="badge badge-outline badge-sm">{{ source.sensitivity }}</span>
          </div>
          <p class="text-sm leading-relaxed whitespace-pre-wrap break-words mt-2 max-w-prose">{{ source.text_snippet || 'No passage preview available.' }}</p>
          <a v-if="sourceHref(source)" :href="sourceHref(source)" target="_blank" rel="noopener noreferrer"
            class="link link-primary inline-flex items-center min-h-9 text-sm mt-1"
            :aria-label="`Open ${source.filename}, page or section ${source.page_number || 1}, in a new tab`">Open original ↗</a>
        </li>
      </ol>
    </details>
    <p v-else-if="!streaming && content" class="mt-3 text-xs text-base-content/70 leading-relaxed">
      No document passages attached. Review any table results in Research activity, or ask for supporting passages.
    </p>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, useId } from 'vue'
import { renderEvidenceMarkdown, sourceHref } from '../utils/answerEvidence'

const props = defineProps({
  content: { type: String, default: '' },
  sources: { type: Array, default: () => [] },
  streaming: Boolean,
  // Long-form answer on the page: gets the reading measure. Off for
  // compact places (search synthesis) that set their own width.
  answer: Boolean,
})
const id = useId()
const selected = ref(null)
const evidencePanel = ref(null)
const html = computed(() => renderEvidenceMarkdown(props.content, props.sources))

const MAX_CHIPS = 8
const visibleSources = computed(() => props.sources.slice(0, MAX_CHIPS))
const chipTitle = (source) => {
  const snippet = String(source.text_snippet || '').replace(/\s+/g, ' ').trim()
  return snippet ? (snippet.length > 220 ? snippet.slice(0, 217) + '…' : snippet) : source.filename
}

function openAll() {
  if (!evidencePanel.value) return
  evidencePanel.value.open = true
  evidencePanel.value.scrollIntoView?.({ block: 'nearest' })
}

async function reviewCitation(event) {
  const button = event.target.closest('button[data-source-number], button[data-source-chip]')
  if (!button || !evidencePanel.value) return
  selected.value = Number(button.dataset.sourceNumber || button.dataset.sourceChip)
  evidencePanel.value.open = true
  await nextTick()
  const passage = evidencePanel.value.querySelector(`[id="${id}-source-${selected.value}"]`)
  passage?.focus({ preventScroll: true })
  passage?.scrollIntoView?.({ block: 'nearest' })
}
</script>

<style scoped>
.chat-markdown { line-height: 1.7; overflow-wrap: anywhere; }
.chat-markdown :deep(p + p),
.chat-markdown :deep(ul),
.chat-markdown :deep(ol) { margin-top: 0.75rem; }
.chat-markdown :deep(ul) { list-style: disc; padding-inline-start: 1.5rem; }
.chat-markdown :deep(ol) { list-style: decimal; padding-inline-start: 1.5rem; }
.chat-markdown :deep(li + li) { margin-top: 0.25rem; }
.chat-markdown :deep(h1),
.chat-markdown :deep(h2),
.chat-markdown :deep(h3) { font-weight: 650; line-height: 1.3; margin-block: 1.25rem 0.5rem; }
.chat-markdown :deep(h1) { font-size: 1.5rem; }
.chat-markdown :deep(h2) { font-size: 1.25rem; }
.chat-markdown :deep(h3) { font-size: 1rem; }
.chat-markdown :deep(pre) { overflow-x: auto; padding: 0.75rem; border-radius: var(--radius-field); }
.chat-markdown :deep(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin-block: 0.75rem; }
.chat-markdown :deep(th),
.chat-markdown :deep(td) { padding: 0.5rem; border: 1px solid var(--color-base-300); text-align: start; }
</style>
