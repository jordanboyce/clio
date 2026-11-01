<template>
  <div class="flex flex-col h-full">

    <!-- Control rail (mirrors the sources panel: icons, no heading bar) -->
    <div class="side-rail border-b border-base-300 flex-shrink-0 bg-base-100" role="region" aria-label="Notes and tools">
      <span class="side-label text-base-content/45 flex-1 min-w-0 truncate pl-1.5">Notes and tools</span>
      <!-- Phones have no notes toggle in the header, so the sheet carries its
           own dismiss there; on desktop the header toggle is the only one. -->
      <button
        v-if="overlay"
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        @click="$emit('close')"
        title="Hide notes and tools"
        aria-label="Hide notes and tools"
      >
        <PanelRightClose :size="16" />
      </button>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Generate -->
      <section class="border-b border-base-300">
        <div class="p-1.5">
          <button
            class="side-row text-base-content/75 hover:text-base-content"
            :class="{ 'is-active': open.generate }"
            @click="open.generate = !open.generate"
            :aria-expanded="open.generate"
          >
            <FileText :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Generate</span>
            <ChevronDown :size="13" class="flex-shrink-0 text-base-content/35 transition-transform" :class="open.generate ? 'rotate-180' : ''" aria-hidden="true" />
          </button>
        </div>
        <div v-show="open.generate" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Turn this collection into a source-grounded document — review the citations before sharing.
          </p>
          <div class="flex flex-col gap-1.5">
            <button class="btn btn-outline btn-xs gap-1" @click="$emit('switch-tab', 'generate')">
              <Sparkles :size="11" />
              Create a report
            </button>
            <button class="btn btn-outline btn-xs gap-1" @click="sendToChat('Give me a briefing of this collection: the key themes, what each source covers, and any open questions, with citations.')">
              <MessageSquare :size="11" />
              Collection briefing
            </button>
          </div>
        </div>
      </section>

      <!-- Structured Tables -->
      <section class="border-b border-base-300">
        <div class="p-1.5">
          <button
            class="side-row text-base-content/75 hover:text-base-content"
            :class="{ 'is-active': open.tables }"
            @click="open.tables = !open.tables"
            :aria-expanded="open.tables"
          >
            <Table2 :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Structured tables</span>
            <ChevronDown :size="13" class="flex-shrink-0 text-base-content/35 transition-transform" :class="open.tables ? 'rotate-180' : ''" aria-hidden="true" />
          </button>
        </div>
        <div v-show="open.tables" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Query a CSV/Excel table loaded into this collection in plain English.
          </p>
          <div class="flex gap-1.5">
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('List all structured tables in this collection and describe their schemas.')">
              <List :size="11" />
              List tables
            </button>
            <button class="btn btn-outline btn-xs flex-1 gap-1" @click="sendToChat('Show me a 10-row sample from each structured table in this collection.')">
              <Eye :size="11" />
              Preview
            </button>
          </div>
        </div>
      </section>

      <!-- Notes -->
      <section class="border-b border-base-300">
        <div class="p-1.5">
          <button
            class="side-row text-base-content/75 hover:text-base-content"
            :class="{ 'is-active': open.notes }"
            @click="open.notes = !open.notes"
            :aria-expanded="open.notes"
          >
            <StickyNote :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Notes</span>
            <span v-if="notes.trim()" class="side-kbd tabular-nums text-base-content/40">{{ noteCount }}</span>
            <ChevronDown :size="13" class="flex-shrink-0 text-base-content/35 transition-transform" :class="open.notes ? 'rotate-180' : ''" aria-hidden="true" />
          </button>
        </div>
        <div v-show="open.notes" class="px-3 pb-3 space-y-1.5">
          <p class="text-[11px] text-base-content/50 leading-snug">
            Scratch pad scoped to this collection. Saved locally.
          </p>
          <textarea
            v-model="notes"
            aria-label="Collection notes"
            class="textarea textarea-bordered textarea-xs w-full text-xs leading-snug min-h-[120px]"
            placeholder="Jot down findings, hypotheses, follow-ups…"
          ></textarea>
        </div>
      </section>

      <!-- Export -->
      <section>
        <div class="p-1.5">
          <button
            class="side-row text-base-content/75 hover:text-base-content"
            :class="{ 'is-active': open.export }"
            @click="open.export = !open.export"
            :aria-expanded="open.export"
          >
            <Download :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Export</span>
            <ChevronDown :size="13" class="flex-shrink-0 text-base-content/35 transition-transform" :class="open.export ? 'rotate-180' : ''" aria-hidden="true" />
          </button>
        </div>
        <div v-show="open.export" class="px-3 pb-3 space-y-1.5">
          <button class="btn btn-outline btn-xs w-full gap-1" @click="exportNotes">
            <FileText :size="11" />
            Notes (.md)
          </button>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch } from 'vue'
import {
  ChevronDown, Table2, StickyNote, PanelRightClose,
  Download, Sparkles, FileText, List, Eye, MessageSquare,
} from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'

defineProps({ overlay: { type: Boolean, default: false } })
const emit = defineEmits(['close', 'send-to-chat', 'switch-tab'])
const collectionStore = useCollectionStore()

const open = reactive({
  generate: true,
  tables: false,
  notes: false,
  export: false,
})

const sendToChat = (prompt) => {
  emit('send-to-chat', prompt)
}

// Notes (per-collection localStorage)
const notesKey = computed(() => `analysis_notes:${collectionStore.currentCollectionId || 'default'}`)
const notes = ref(localStorage.getItem(notesKey.value) || '')
const noteCount = computed(() => notes.value.trim().split(/\s+/).filter(Boolean).length)

watch(notesKey, (key) => {
  notes.value = localStorage.getItem(key) || ''
})
watch(notes, (v) => {
  localStorage.setItem(notesKey.value, v)
})

const exportNotes = () => {
  const name = collectionStore.currentCollection?.name || 'collection'
  const blob = new Blob([notes.value || ''], { type: 'text/markdown' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${name.replace(/[^a-z0-9-_]+/gi, '_')}-notes.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>
