<template>
  <div
    class="research-node rounded-md border bg-base-100 px-2.5 py-1.5 text-left transition-opacity"
    :class="[
      data.type === 'external' ? 'border-dashed border-base-content/35 bg-base-200/60' : 'border-base-300 shadow-sm',
      data.dimmed ? 'opacity-25' : '',
      selected ? 'ring-2 ring-primary' : (data.match ? 'ring-2 ring-warning' : ''),
    ]"
    :style="{ width: `${data.width}px`, minHeight: `${data.height}px` }"
    :title="data.title || data.label"
  >
    <Handle type="target" :position="targetPosition" class="research-handle" />
    <div class="text-xs font-medium leading-snug line-clamp-2">{{ data.label }}</div>
    <div class="mt-0.5 flex items-center gap-1.5 text-[11px] text-base-content/60">
      <span class="truncate">{{ meta }}</span>
      <span v-if="data.kind === 'book'" class="badge badge-ghost badge-xs shrink-0">book</span>
      <span v-if="data.cited_by" class="ml-auto shrink-0 tabular-nums" :title="`Cited by ${data.cited_by}`">↖{{ data.cited_by }}</span>
    </div>
    <Handle type="source" :position="sourcePosition" class="research-handle" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { authorsShort } from '../utils/researchApi.js'

const props = defineProps({
  data: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})

const targetPosition = Position.Left
const sourcePosition = Position.Right

const meta = computed(() => {
  const d = props.data
  if (d.type === 'external') {
    const who = d.in_collection ? 'In your collection' : 'Not in your collection'
    return [who, d.year].filter(Boolean).join(' · ')
  }
  return [authorsShort(d.authors) || (d.analyzed ? '' : 'Not analyzed'), d.year].filter(Boolean).join(' · ') || d.filename
})
</script>
