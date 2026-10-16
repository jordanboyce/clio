<template>
  <!-- Only once this collection has had a source change: nothing to show before. -->
  <div v-if="changes.latestId > 0" class="dropdown dropdown-end hidden md:block" data-testid="changes-bell">
    <button
      tabindex="0"
      class="btn btn-ghost btn-circle btn-sm relative"
      :title="label"
      :aria-label="label"
      aria-haspopup="menu"
    >
      <Activity :size="16" aria-hidden="true" />
      <span
        v-if="changes.unseen.length > 0"
        class="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full text-[10px] leading-4 font-semibold text-center tabular-nums bg-primary text-primary-content"
        data-testid="changes-badge"
      >{{ changes.unseen.length > 99 ? '99+' : changes.unseen.length }}</span>
    </button>
    <div tabindex="0" class="dropdown-content z-[60] bg-base-100 rounded-box shadow-lg border border-base-300 w-80 p-3">
      <div class="flex items-center justify-between mb-2">
        <span class="text-xs font-semibold uppercase tracking-wider text-base-content/55">Source updates</span>
        <button
          v-if="changes.unseen.length > 0"
          class="btn btn-ghost btn-xs"
          @click="changes.markSeen()"
        >Mark all seen</button>
      </div>
      <ul class="space-y-2 max-h-96 overflow-y-auto">
        <li
          v-for="event in changes.newestFirst.slice(0, 10)"
          :key="event.id"
          class="rounded-md px-2 py-1.5 text-sm"
          :class="event.id > changes.seenId ? 'bg-primary/10' : 'bg-base-200/50'"
        >
          <div class="flex items-baseline justify-between gap-2">
            <span class="font-medium truncate" :title="event.source">{{ event.label }}</span>
            <span class="text-[11px] text-base-content/55 whitespace-nowrap">{{ ago(event.at) }}</span>
          </div>
          <div class="text-base-content/80">{{ event.summary }}<span v-if="event.branch" class="text-base-content/50"> · {{ event.branch }}</span></div>
          <ul v-if="files(event).length" class="mt-1 text-xs text-base-content/65 font-mono space-y-0.5">
            <li v-for="f in files(event).slice(0, 3)" :key="f.kind + f.name" class="truncate">
              <span :class="kindClass[f.kind]">{{ kindMark[f.kind] }}</span> {{ f.name }}
            </li>
            <li v-if="files(event).length > 3" class="text-base-content/50">+ {{ event.added_count + event.changed_count + event.removed_count - 3 }} more</li>
          </ul>
          <ul v-if="event.commits && event.commits.length" class="mt-1 text-xs text-base-content/65 space-y-0.5">
            <li v-for="c in event.commits.slice(0, 2)" :key="c.sha" class="truncate" :title="`${c.author}: ${c.subject}`">
              <span class="font-mono text-base-content/50">{{ c.sha.slice(0, 7) }}</span> {{ c.subject }}
            </li>
            <li v-if="event.commits.length > 2" class="text-base-content/50">+ {{ event.commits.length - 2 }} more commits</li>
          </ul>
        </li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { Activity } from 'lucide-vue-next'
import { useChangesStore } from '../stores/changesStore'
import { useCollectionStore } from '../stores/collectionStore'

const changes = useChangesStore()
const collectionStore = useCollectionStore()

const kindMark = { added: '+', changed: '~', removed: '−' }
const kindClass = { added: 'text-success', changed: 'text-warning', removed: 'text-error' }

const label = computed(() =>
  changes.unseen.length
    ? `${changes.unseen.length} new source update${changes.unseen.length === 1 ? '' : 's'}`
    : 'Source updates'
)

// Added, changed and removed paths in one list, in that order.
const files = (event) => [
  ...(event.added || []).map(name => ({ kind: 'added', name })),
  ...(event.changed || []).map(name => ({ kind: 'changed', name })),
  ...(event.removed || []).map(name => ({ kind: 'removed', name })),
]

const ago = (iso) => {
  const ts = new Date(iso).getTime()
  if (!Number.isFinite(ts)) return ''
  const mins = Math.max(0, Math.floor((Date.now() - ts) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

onMounted(() => changes.start())
onBeforeUnmount(() => changes.stop())
watch(() => collectionStore.currentCollectionId, () => changes.onCollectionChanged())
</script>
