<template>
  <div class="sources-tree px-1 pb-2 text-[13px]" role="tree" aria-label="Sources" data-testid="sources-tree" @keydown="onKeydown">
    <div
      v-for="(row, index) in rows"
      :key="row.key"
      class="tree-row group"
      :class="{
        'is-selected': row.kind === 'file' && selected.has(row.doc.document_id),
        'is-focused': focusIndex === index,
      }"
      :style="{ paddingLeft: `${0.35 + row.depth * INDENT}rem` }"
      role="treeitem"
      :aria-level="row.depth + 1"
      :aria-expanded="row.kind === 'folder' ? row.open : undefined"
      :aria-selected="row.kind === 'file' ? selected.has(row.doc.document_id) : undefined"
      :tabindex="focusIndex === index ? 0 : -1"
      :data-row="index"
      @click="onRowClick(row, index, $event)"
      @dblclick="row.kind === 'file' && $emit('open', row.doc)"
      @contextmenu.prevent="openMenu($event, row)"
    >
      <!-- Indent guides, as in Obsidian and VS Code -->
      <span v-for="d in row.depth" :key="d" class="tree-guide" :style="{ left: `${0.35 + (d - 1) * INDENT + 0.42}rem` }" aria-hidden="true" />

      <template v-if="row.kind === 'folder'">
        <ChevronRight :size="13" class="tree-chevron" :class="row.open ? 'rotate-90' : ''" aria-hidden="true" />
        <input
          type="checkbox"
          class="checkbox checkbox-xs tree-check"
          :class="{ 'is-shown': selectionMode || folderState(row.node) !== 'none' }"
          :checked="folderState(row.node) === 'all'"
          :indeterminate.prop="folderState(row.node) === 'some'"
          :aria-label="`Select everything in ${row.node.name}`"
          @click.stop
          @change="$emit('select', folderDocIds(row.node), folderState(row.node) !== 'all')"
        />
        <component :is="folderIcon(row)" :size="14" class="flex-shrink-0 text-base-content/55" aria-hidden="true" />
        <span class="truncate flex-1 text-base-content/85">{{ row.node.name }}</span>
        <span class="tree-count tabular-nums">{{ row.node.count }}</span>
      </template>

      <template v-else>
        <span class="w-[13px] flex-shrink-0" aria-hidden="true" />
        <input
          type="checkbox"
          class="checkbox checkbox-xs tree-check"
          :class="{ 'is-shown': selectionMode }"
          :checked="selected.has(row.doc.document_id)"
          :aria-label="`Select ${row.doc.filename}`"
          @click.stop
          @change="$emit('toggle', row.doc.document_id)"
        />
        <component :is="fileInfo(row.doc).icon" :size="14" class="flex-shrink-0" :class="iconTone(row.doc)" aria-hidden="true" />
        <span class="truncate flex-1" :title="tooltip(row.doc)">{{ stem(row.doc.tree_label || row.doc.filename) }}</span>
        <!-- What a reviewer or a regulated team needs to see on every row:
             a held or flagged document, a possible prompt injection, a
             sensitivity label, a newer version. -->
        <button
          v-if="policyBadge(row.doc.policy_status)"
          class="tree-flag"
          :class="POLICY_TONE[row.doc.policy_status] || 'text-base-content/55'"
          :title="policyBadge(row.doc.policy_status).title + ' Click for details.'"
          @click.stop="$emit('policy', row.doc)"
        >{{ policyBadge(row.doc.policy_status).text }}</button>
        <button
          v-if="injectionBadge(row.doc)"
          class="tree-flag inline-flex items-center"
          :class="row.doc.injection_review ? 'text-base-content/40' : 'text-warning'"
          :title="injectionBadge(row.doc).title"
          :aria-label="`Prompt-injection note for ${row.doc.filename}`"
          @click.stop="$emit('injection', row.doc)"
        ><ShieldAlert :size="11" /></button>
        <span
          v-if="showLabel(row.doc.sensitivity_effective)"
          class="tree-flag inline-flex items-center gap-0.5"
          :class="LABEL_TONE[row.doc.sensitivity_effective] || 'text-base-content/55'"
          :title="`Sensitivity: ${row.doc.sensitivity_effective}${row.doc.sensitivity ? ' (set on this document)' : ' (collection default)'}`"
        ><Lock :size="9" aria-hidden="true" />{{ row.doc.sensitivity_effective }}</span>
        <span v-if="row.doc.superseded_by" class="tree-flag text-base-content/45" title="Replaced by a newer version">replaced</span>
        <span class="tree-ext">{{ fileInfo(row.doc).label }}</span>
        <button
          class="tree-more"
          :aria-label="`More actions for ${row.doc.filename}`"
          @click.stop="openMenu($event, row)"
        ><MoreHorizontal :size="13" /></button>
      </template>
    </div>

    <p v-if="!rows.length" class="px-3 py-6 text-center text-xs text-base-content/50">Nothing here.</p>

    <!-- Right-click menu -->
    <ul
      v-if="menu"
      ref="menuEl"
      class="menu fixed z-[90] w-56 rounded-box border border-base-300 bg-base-100 p-1.5 text-sm shadow-lg"
      :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
      role="menu"
    >
      <template v-if="menu.row.kind === 'file'">
        <li><button role="menuitem" @click="act('open')"><ExternalLink :size="14" />Open</button></li>
        <li><button role="menuitem" @click="act('chunks')"><FileSearch :size="14" />View indexed text</button></li>
        <li><button role="menuitem" @click="act('only')"><CheckSquare :size="14" />Select only this</button></li>
        <li v-if="canEdit"><button role="menuitem" @click="act('replace')"><Replace :size="14" />Mark as replaced by…</button></li>
        <li><button role="menuitem" @click="act('report')"><Flag :size="14" />Report…</button></li>
        <li v-if="canEdit"><button role="menuitem" class="text-error" @click="act('delete')"><Trash2 :size="14" />Delete</button></li>
      </template>
      <template v-else>
        <li><button role="menuitem" @click="act('select-folder')"><CheckSquare :size="14" />Select everything here</button></li>
        <li><button role="menuitem" @click="act('only-folder')"><Focus :size="14" />Select only this folder</button></li>
        <li><button role="menuitem" @click="act('collapse-others')"><ChevronsDownUp :size="14" />Collapse the others</button></li>
      </template>
    </ul>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  CheckSquare, ChevronRight, ChevronsDownUp, ExternalLink, FileSearch, Flag, Focus, Folder, FolderGit2, FolderOpen,
  Globe, Lock, MoreHorizontal, Replace, ShieldAlert, StickyNote, Trash2,
} from 'lucide-vue-next'
import { describeFile } from '../utils/fileTypes'
import { injectionBadge, policyBadge, showLabel } from '../utils/governance'
import { folderDocIds, folderSelection, visibleRows } from '../utils/sourceTree.js'

const props = defineProps({
  tree: { type: Object, required: true },
  collapsed: { type: Object, required: true },     // Set of folder paths
  selectedIds: { type: Array, default: () => [] },
  canEdit: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle', 'select', 'select-only', 'toggle-folder', 'collapse-others', 'open', 'chunks', 'replace', 'report', 'delete', 'injection', 'policy'])

// Tighter than a document list: a repository runs eight folders deep.
const INDENT = 0.8
const POLICY_TONE = { quarantined: 'text-error', flagged: 'text-warning', rejected: 'text-error' }
const LABEL_TONE = { confidential: 'text-warning', restricted: 'text-error', public: 'text-success' }

const selected = computed(() => new Set(props.selectedIds))
const selectionMode = computed(() => props.selectedIds.length > 0)
const rows = computed(() => visibleRows(props.tree, props.collapsed))
const focusIndex = ref(0)
watch(rows, (r) => { if (focusIndex.value >= r.length) focusIndex.value = Math.max(0, r.length - 1) })

const fileInfo = (doc) => describeFile(doc?.filename, { isWeb: doc?.source_type === 'url', isNote: doc?.source_type === 'text' })
const iconTone = (doc) => (fileInfo(doc).tile.split(' ').find(c => c.startsWith('text-')) || 'text-base-content/60')
const folderState = (node) => folderSelection(node, selected.value)
const stem = (name) => {
  const n = String(name || '')
  const i = n.lastIndexOf('.')
  return i > 0 && n.length - i <= 6 ? n.slice(0, i) : n
}
function folderIcon(row) {
  if (row.node.group === 'git') return FolderGit2
  if (row.node.group === 'links' || row.node.group === 'site') return Globe
  if (row.node.group === 'notes') return StickyNote
  return row.open ? FolderOpen : Folder
}
function tooltip(doc) {
  const lines = [doc.source_path || doc.filename]
  if (doc.tree_label && doc.tree_label !== doc.filename) lines.push(`Stored as ${doc.filename}`)
  if (doc.uploaded_by) lines.push(`Added by ${doc.uploaded_by}`)
  return lines.join('\n')
}

function onRowClick(row, index, event) {
  focusIndex.value = index
  if (row.kind === 'folder') return emit('toggle-folder', row.node.path)
  // Click selects, like the checkbox; Ctrl/⌘-click selects this one alone.
  if (event.metaKey || event.ctrlKey) return emit('select-only', [row.doc.document_id])
  emit('toggle', row.doc.document_id)
}

// Arrow keys move, Right/Left open and close folders, Space selects,
// Enter opens — the file-explorer keys people already know.
function onKeydown(event) {
  const r = rows.value
  if (!r.length) return
  const row = r[focusIndex.value]
  const move = (i) => {
    event.preventDefault()
    focusIndex.value = Math.max(0, Math.min(r.length - 1, i))
    requestAnimationFrame(() => document.querySelector(`.sources-tree [data-row="${focusIndex.value}"]`)?.focus())
  }
  switch (event.key) {
    case 'ArrowDown': return move(focusIndex.value + 1)
    case 'ArrowUp': return move(focusIndex.value - 1)
    case 'Home': return move(0)
    case 'End': return move(r.length - 1)
    case 'ArrowRight':
      if (row?.kind === 'folder' && !row.open) { event.preventDefault(); emit('toggle-folder', row.node.path) }
      return
    case 'ArrowLeft':
      if (row?.kind === 'folder' && row.open) { event.preventDefault(); emit('toggle-folder', row.node.path) }
      return
    case ' ':
      event.preventDefault()
      if (row?.kind === 'file') emit('toggle', row.doc.document_id)
      else if (row) emit('select', folderDocIds(row.node), folderState(row.node) !== 'all')
      return
    case 'Enter':
      if (row?.kind === 'file') { event.preventDefault(); emit('open', row.doc) }
  }
}

// ── context menu ──
const menu = ref(null)
const menuEl = ref(null)
function openMenu(event, row) {
  menu.value = { x: Math.min(event.clientX, window.innerWidth - 240), y: Math.min(event.clientY, window.innerHeight - 260), row }
}
function closeMenu() { menu.value = null }
function act(what) {
  const row = menu.value?.row
  closeMenu()
  if (!row) return
  if (row.kind === 'file') {
    if (what === 'only') return emit('select-only', [row.doc.document_id])
    return emit(what, row.doc)
  }
  if (what === 'select-folder') return emit('select', folderDocIds(row.node), true)
  if (what === 'only-folder') return emit('select-only', folderDocIds(row.node))
  if (what === 'collapse-others') return emit('collapse-others', row.node.path)
}
const onDocPointer = (e) => { if (menu.value && !menuEl.value?.contains(e.target)) closeMenu() }
const onEsc = (e) => { if (e.key === 'Escape') closeMenu() }
watch(menu, (m) => {
  const fn = m ? 'addEventListener' : 'removeEventListener'
  document[fn]('pointerdown', onDocPointer, true)
  document[fn]('keydown', onEsc)
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointer, true)
  document.removeEventListener('keydown', onEsc)
})
</script>

<style scoped>
.tree-row {
  position: relative;
  display: flex; align-items: center; gap: 0.35rem;
  height: 1.65rem; padding-right: 0.35rem; border-radius: 0.3rem;
  cursor: pointer; user-select: none;
  color: color-mix(in oklch, var(--color-base-content) 82%, transparent);
}
.tree-row:hover { background-color: color-mix(in oklch, var(--color-base-content) 6%, transparent); }
.tree-row.is-selected { background-color: color-mix(in oklch, var(--color-primary) 13%, transparent); color: var(--color-base-content); }
.tree-row:focus-visible { outline: 1px solid var(--color-primary); outline-offset: -1px; }
.tree-guide {
  position: absolute; top: 0; bottom: 0; width: 1px;
  background-color: color-mix(in oklch, var(--color-base-content) 12%, transparent);
}
.tree-chevron { flex-shrink: 0; color: color-mix(in oklch, var(--color-base-content) 45%, transparent); transition: transform 120ms; }
/* The checkbox stays out of the way until you hover or start selecting. */
.tree-check { flex-shrink: 0; width: 0.85rem; height: 0.85rem; opacity: 0; }
.tree-row:hover .tree-check, .tree-check.is-shown, .tree-check:focus-visible { opacity: 1; }
.tree-count, .tree-ext { flex-shrink: 0; font-size: 10px; color: color-mix(in oklch, var(--color-base-content) 40%, transparent); }
.tree-count { opacity: 0; }
.tree-row:hover .tree-count { opacity: 1; }
.tree-flag { flex-shrink: 0; font-size: 10px; }
.tree-more {
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
  width: 1.2rem; height: 1.2rem; border-radius: 0.25rem; opacity: 0;
  color: color-mix(in oklch, var(--color-base-content) 55%, transparent);
}
.tree-row:hover .tree-more, .tree-more:focus-visible { opacity: 1; }
.tree-more:hover { background-color: color-mix(in oklch, var(--color-base-content) 10%, transparent); }
@media (pointer: coarse) { .tree-check, .tree-more { opacity: 1; } }
</style>
