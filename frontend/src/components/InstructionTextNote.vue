<template>
  <div v-if="note" class="mt-1 text-xs" data-testid="instruction-text-note">
    <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span class="badge badge-sm gap-1" :class="note.cls" :title="note.title" data-testid="instruction-text-badge">
        <MessageSquareWarning :size="12" aria-hidden="true" />{{ note.text }}
      </span>
      <span class="text-base-content/60">
        Quoted from the source as data<template v-if="note.categories.length"> · {{ note.categories.join(', ') }}</template>
      </span>
    </div>
    <details v-if="note.excerpts.length" class="mt-1 text-base-content/60">
      <summary class="cursor-pointer py-1">What tripped it</summary>
      <ul class="ml-4 list-disc space-y-0.5">
        <li v-for="(e, i) in note.excerpts" :key="i"><code class="text-[11px] break-all">{{ e }}</code></li>
      </ul>
    </details>
  </div>
</template>

<script setup>
// A quiet line under a passage whose wording addresses an AI assistant. The
// passage stays visible: it is evidence from the source, and this says so.
import { computed } from 'vue'
import { MessageSquareWarning } from 'lucide-vue-next'
import { instructionTextNote } from '../utils/governance'

const props = defineProps({ source: { type: Object, required: true } })
const note = computed(() => instructionTextNote(props.source))
</script>
