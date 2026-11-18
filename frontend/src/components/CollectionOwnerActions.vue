<template>
  <!-- Export and Edit: owner-tier actions (api/deps.require_collection_access).
       A contributor sees them disabled with the reason, so the 403 they would
       get is said before they click; a reader's view stays small and shows
       neither. -->
  <template v-if="allowed">
    <button
      class="btn btn-ghost btn-xs btn-square"
      title="Export for another Clio or a website"
      :aria-label="`Export collection ${collection.name}`"
      data-testid="collection-export"
      @click.stop="$emit('export', collection)"
    >
      <Download :size="13" />
    </button>
    <button
      class="btn btn-ghost btn-xs btn-square"
      title="Edit collection"
      :aria-label="`Edit collection ${collection.name}`"
      data-testid="collection-edit"
      @click.stop="$emit('edit', collection)"
    >
      <Pencil :size="13" />
    </button>
  </template>
  <template v-else-if="contributor">
    <!-- A disabled button gets no hover events, so the tooltip sits on a wrapper. -->
    <span
      v-for="action in blockedActions"
      :key="action.id"
      class="inline-flex"
      :title="reason"
      tabindex="0"
      :aria-label="`${action.label} ${collection.name}: ${reason}`"
      :data-testid="`collection-${action.id}-blocked`"
      @click.stop
    >
      <button class="btn btn-ghost btn-xs btn-square pointer-events-none" disabled aria-hidden="true" tabindex="-1">
        <component :is="action.icon" :size="13" />
      </button>
    </span>
  </template>
</template>

<script setup>
import { computed } from 'vue'
import { Download, Pencil } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'

const props = defineProps({
  collection: { type: Object, required: true },
})
defineEmits(['export', 'edit'])

const collectionStore = useCollectionStore()

const allowed = computed(() => collectionStore.canConfigure(props.collection))
const contributor = computed(() => (props.collection?.permission || 'owner') === 'readwrite')
const reason = computed(() => collectionStore.configureBlockedReason(props.collection))
const blockedActions = [
  { id: 'export', label: 'Export collection', icon: Download },
  { id: 'edit', label: 'Edit collection', icon: Pencil },
]
</script>
