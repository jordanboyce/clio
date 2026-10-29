<template>
  <dialog class="modal" :open="open" aria-labelledby="ribbon-customize-title" @close="$emit('close')" @keydown.esc="$emit('close')">
    <div class="modal-box max-w-sm space-y-3">
      <div>
        <h3 id="ribbon-customize-title" class="font-semibold">Customize the ribbon</h3>
        <p class="text-sm text-base-content/60 mt-0.5">
          Choose what the icon bar shows and in what order. You can also drag icons on the ribbon itself.
        </p>
      </div>
      <ul class="divide-y divide-base-300/70 rounded-box border border-base-300" data-testid="ribbon-customize-list">
        <li
          v-for="(item, i) in items"
          :key="item.id"
          class="flex items-center gap-2 px-2.5 py-1.5"
          :class="{ 'bg-base-200/60': dragOver === item.id }"
          draggable="true"
          @dragstart="dragging = item.id"
          @dragover.prevent="dragOver = item.id"
          @drop.prevent="drop(item)"
          @dragend="dragging = null; dragOver = null"
        >
          <GripVertical :size="14" class="text-base-content/35 cursor-grab flex-shrink-0" aria-hidden="true" />
          <label class="flex flex-1 items-center gap-2 cursor-pointer text-sm">
            <input
              type="checkbox"
              class="checkbox checkbox-sm"
              :checked="!hiddenSet.has(item.id)"
              :aria-label="`Show ${item.label}`"
              @change="update(toggleHidden(layout, item.id))"
            />
            <span :class="hiddenSet.has(item.id) ? 'text-base-content/45' : ''">{{ item.label }}</span>
          </label>
          <button class="btn btn-ghost btn-xs btn-square" :disabled="i === 0" :aria-label="`Move ${item.label} up`" @click="update(moveItem(layout, item.id, -1))">
            <ChevronUp :size="14" />
          </button>
          <button class="btn btn-ghost btn-xs btn-square" :disabled="i === items.length - 1" :aria-label="`Move ${item.label} down`" @click="update(moveItem(layout, item.id, 1))">
            <ChevronDown :size="14" />
          </button>
        </li>
      </ul>
      <div class="modal-action mt-2">
        <button class="btn btn-sm btn-ghost" @click="update(defaultLayout())">Reset to default</button>
        <button class="btn btn-sm btn-primary" @click="$emit('close')">Done</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button @click="$emit('close')">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ChevronDown, ChevronUp, GripVertical } from 'lucide-vue-next'
import { defaultLayout, moveItem, placeBefore, toggleHidden } from '../utils/ribbon.js'

// Every item the person may use, in ribbon order; unticked ones are hidden.
const props = defineProps({
  open: { type: Boolean, default: false },
  items: { type: Array, required: true },
  layout: { type: Object, required: true },
})
const emit = defineEmits(['close', 'update:layout'])

const hiddenSet = computed(() => new Set(props.layout.hidden))
const dragging = ref(null)
const dragOver = ref(null)

function update(layout) { emit('update:layout', layout) }
function drop(target) {
  if (dragging.value && dragging.value !== target.id) update(placeBefore(props.layout, dragging.value, target.id))
  dragging.value = null
  dragOver.value = null
}
</script>
