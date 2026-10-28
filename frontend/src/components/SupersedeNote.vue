<template>
  <div v-if="notes.older || notes.also" class="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs mt-1" data-testid="supersede-note">
    <span v-if="notes.older" class="inline-flex items-center gap-1 text-warning" data-testid="older-version">
      <History :size="12" aria-hidden="true" />{{ notes.older }}
    </span>
    <span v-if="notes.also" class="inline-flex items-center gap-1 text-base-content/70" :title="notes.alsoTitle" data-testid="also-in">
      <Copy :size="12" aria-hidden="true" />{{ notes.also }}
    </span>
  </div>
</template>

<script setup>
// Two quiet lines under a search result or a chat source: it came from an
// older version (and which document replaced it), and the same text also
// appears in other files.
import { computed } from 'vue'
import { History, Copy } from 'lucide-vue-next'
import { supersedeNotes } from '../utils/supersede'

const props = defineProps({ source: { type: Object, required: true } })
const notes = computed(() => supersedeNotes(props.source))
</script>
