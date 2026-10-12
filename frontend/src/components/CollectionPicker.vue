<template>
  <!-- Which collection the workspace is looking at. A real picker: switching
       stays where you are; the overview is one click further, not the default. -->
  <div class="dropdown dropdown-start">
    <label
      tabindex="0"
      class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-7 min-h-0 border border-base-300 rounded-full px-2.5"
      :class="{ 'bg-base-200': overview }"
      :title="`Collection: ${current?.name || 'Default'}`"
      :aria-label="`Collection: ${current?.name || 'Default'}. Click to switch.`"
      aria-haspopup="menu"
    >
      <span class="w-2 h-2 rounded-full flex-shrink-0" :style="{ backgroundColor: current?.color || '#3b82f6' }" aria-hidden="true"></span>
      <span class="max-w-36 truncate text-xs">{{ current?.name || 'Default' }}</span>
      <ChevronDown :size="10" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
    </label>

    <div tabindex="0" class="dropdown-content z-[60] mt-1 bg-base-100 border border-base-300 rounded-box shadow-lg w-72 p-2">
      <input
        v-if="collections.length > 7"
        v-model="query"
        type="search"
        class="input input-bordered input-sm w-full mb-2"
        placeholder="Find a collection"
        aria-label="Find a collection"
      />
      <ul class="max-h-72 overflow-y-auto space-y-0.5" role="listbox" aria-label="Collections">
        <li v-for="c in visible" :key="c.id">
          <button
            class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-sm hover:bg-base-200"
            :class="c.id === current?.id ? 'bg-base-200 font-medium' : ''"
            role="option"
            :aria-selected="c.id === current?.id"
            @click="pick(c.id)"
          >
            <span class="w-2 h-2 rounded-full flex-shrink-0" :style="{ backgroundColor: c.color }" aria-hidden="true"></span>
            <span class="flex-1 truncate">{{ c.name }}</span>
            <span v-if="c.shared" class="text-[10px] uppercase tracking-wider text-base-content/40">shared</span>
            <span class="text-xs tabular-nums text-base-content/45">{{ c.document_count || 0 }}</span>
            <Check v-if="c.id === current?.id" :size="12" class="text-primary flex-shrink-0" aria-hidden="true" />
          </button>
        </li>
        <li v-if="!visible.length" class="px-2 py-3 text-xs text-base-content/50">No collection matches “{{ query }}”.</li>
      </ul>
      <div class="border-t border-base-300 mt-2 pt-1.5 flex items-center justify-between">
        <button class="btn btn-xs btn-ghost gap-1.5" @click="$emit('overview')"><Layers :size="12" aria-hidden="true" />All collections</button>
        <button class="btn btn-xs btn-ghost gap-1.5" @click="$emit('create')"><Plus :size="12" aria-hidden="true" />New</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Check, ChevronDown, Layers, Plus } from 'lucide-vue-next'

const props = defineProps({
  collections: { type: Array, default: () => [] },
  current: { type: Object, default: null },
  overview: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'overview', 'create'])

const query = ref('')
const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return q ? props.collections.filter(c => (c.name || '').toLowerCase().includes(q)) : props.collections
})

function pick(id) {
  query.value = ''
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  emit('select', id)
}
</script>
