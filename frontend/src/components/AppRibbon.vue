<template>
  <nav
    class="app-ribbon flex flex-col gap-1 flex-shrink-0 border-r border-base-300 bg-base-100 py-1.5"
    :class="labels ? 'w-44 items-stretch px-1.5' : 'w-11 items-center'"
    aria-label="Ribbon"
    data-testid="app-ribbon"
    @contextmenu.prevent="openMenu($event, null)"
  >
    <!-- The panel toggle is fixed at the top, as in Obsidian. -->
    <button
      class="ribbon-btn"
      :class="{ 'is-active': sourcesOpen, 'has-label': labels }"
      :aria-pressed="sourcesOpen"
      :aria-label="sourcesOpen ? 'Hide sources panel' : 'Show sources panel'"
      :data-tip="labels ? null : (sourcesOpen ? 'Hide sources panel' : 'Show sources panel')"
      @click="$emit('action', { action: 'toggle-sources' })"
    >
      <component :is="sourcesOpen ? PanelLeftClose : PanelLeft" :size="18" class="flex-shrink-0" />
      <span v-if="labels" class="truncate">Sources</span>
    </button>
    <div class="ribbon-divider" aria-hidden="true" />

    <template v-for="{ item, divider } in grouped" :key="item.id">
    <div v-if="divider" class="ribbon-divider" aria-hidden="true" />
    <button
      class="ribbon-btn"
      :class="{
        'has-label': labels,
        'is-active': isActive(item, activeTab, researchView),
        'is-drop-target': dropTarget === item.id && dragging && dragging !== item.id,
      }"
      :aria-label="item.label"
      :aria-current="isActive(item, activeTab, researchView) ? 'page' : undefined"
      :data-tip="labels ? null : item.label"
      :data-ribbon-item="item.id"
      draggable="true"
      @click="$emit('action', item)"
      @contextmenu.prevent.stop="openMenu($event, item)"
      @dragstart="onDragStart($event, item)"
      @dragover.prevent="dropTarget = item.id"
      @dragleave="dropTarget === item.id && (dropTarget = null)"
      @drop.prevent="onDrop(item)"
      @dragend="dragging = null; dropTarget = null"
    >
      <component :is="ICONS[item.id]" :size="18" class="flex-shrink-0" />
      <span v-if="labels" class="truncate">{{ item.label }}</span>
      <span v-if="item.id === 'admin' && adminBadge > 0" class="ribbon-dot" aria-hidden="true" />
    </button>
    </template>

    <div class="flex-1 w-full" @dragover.prevent @drop.prevent="onDrop(null)" />

    <div class="dropdown dropdown-right dropdown-end">
      <button tabindex="0" class="ribbon-btn" :class="{ 'has-label': labels }" aria-label="Help" :data-tip="labels ? null : 'Help'" aria-haspopup="menu">
        <HelpCircle :size="18" class="flex-shrink-0" />
        <span v-if="labels">Help</span>
      </button>
      <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-[80] w-56 p-2 shadow border border-base-300 ml-1">
        <li><a href="https://github.com/jordanboyce/clio#readme" target="_blank" rel="noopener"><BookOpen :size="14" />Documentation</a></li>
        <li><button @click="$emit('action', { action: 'palette' })"><Command :size="14" />Command palette</button></li>
        <li><button @click="$emit('action', { action: 'shortcuts' })"><Keyboard :size="14" />Keyboard shortcuts</button></li>
        <li><button @click="$emit('customize')"><SlidersHorizontal :size="14" />Customize ribbon…</button></li>
        <li><button @click="$emit('action', { action: 'onboarding' })"><Sparkles :size="14" />Run setup again</button></li>
        <li><button @click="$emit('action', { action: 'about' })"><Info :size="14" />About Clio</button></li>
      </ul>
    </div>
    <button
      class="ribbon-btn"
      :class="{ 'is-active': activeTab === 'settings', 'has-label': labels }"
      aria-label="Settings"
      :data-tip="labels ? null : 'Settings'"
      :aria-current="activeTab === 'settings' ? 'page' : undefined"
      @click="$emit('action', { tab: 'settings' })"
    >
      <Settings :size="18" class="flex-shrink-0" />
      <span v-if="labels">Settings</span>
    </button>

    <!-- Right-click menu -->
    <ul
      v-if="menu"
      ref="menuEl"
      class="menu fixed z-[90] w-52 rounded-box border border-base-300 bg-base-100 p-1.5 text-sm shadow-lg"
      :style="{ left: `${menu.x}px`, top: `${menu.y}px` }"
      role="menu"
      @contextmenu.prevent
    >
      <li v-if="menu.item"><button role="menuitem" @click="hide(menu.item)"><EyeOff :size="14" />Hide “{{ menu.item.label }}”</button></li>
      <li><button role="menuitem" @click="setLabels(!labels)"><Type :size="14" />{{ labels ? 'Show icons only' : 'Show names' }}</button></li>
      <li><button role="menuitem" @click="closeMenu(); $emit('customize')"><SlidersHorizontal :size="14" />Customize ribbon…</button></li>
      <li><button role="menuitem" @click="reset"><RotateCcw :size="14" />Reset to default</button></li>
    </ul>
  </nav>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import {
  BookOpen, Command, EyeOff, FileText, FolderPlus, Gauge, HelpCircle, Info, Keyboard, Layers, LibraryBig,
  MessageSquare, MessageSquarePlus, Network, NotebookPen, PanelLeft, PanelLeftClose, PanelRight, Plug, RotateCcw,
  Search, Settings, SlidersHorizontal, Sparkles, SquareTerminal, Type,
} from 'lucide-vue-next'
import { defaultLayout, isActive, placeBefore, toggleHidden, withDividers } from '../utils/ribbon.js'

const ICONS = {
  chat: MessageSquare, search: Search, research: LibraryBig, graph: Network, reviews: NotebookPen,
  generate: FileText, expertise: BookOpen, mcp: Plug, collections: Layers, 'add-sources': FolderPlus,
  'new-chat': MessageSquarePlus, notes: PanelRight, palette: SquareTerminal, admin: Gauge,
}

const props = defineProps({
  items: { type: Array, required: true },
  layout: { type: Object, required: true },
  activeTab: { type: String, required: true },
  researchView: { type: String, default: 'library' },
  sourcesOpen: { type: Boolean, default: false },
  adminBadge: { type: Number, default: 0 },
})
const emit = defineEmits(['action', 'customize', 'update:layout'])

const labels = computed(() => Boolean(props.layout.labels))
const grouped = computed(() => withDividers(props.items))
const menu = ref(null)
const menuEl = ref(null)
const dragging = ref(null)
const dropTarget = ref(null)

function openMenu(event, item) {
  menu.value = { x: Math.min(event.clientX, window.innerWidth - 220), y: Math.min(event.clientY, window.innerHeight - 130), item }
}
function closeMenu() { menu.value = null }
function hide(item) {
  emit('update:layout', toggleHidden(props.layout, item.id))
  closeMenu()
}
function setLabels(on) {
  emit('update:layout', { ...props.layout, labels: on })
  closeMenu()
}
function reset() {
  emit('update:layout', defaultLayout())
  closeMenu()
}

const onDocPointer = (e) => { if (menu.value && !menuEl.value?.contains(e.target)) closeMenu() }
const onKey = (e) => { if (e.key === 'Escape') closeMenu() }
watch(menu, (m) => {
  if (m) {
    document.addEventListener('pointerdown', onDocPointer, true)
    document.addEventListener('keydown', onKey)
  } else {
    document.removeEventListener('pointerdown', onDocPointer, true)
    document.removeEventListener('keydown', onKey)
  }
})
onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', onDocPointer, true)
  document.removeEventListener('keydown', onKey)
})

// Drag an icon to reorder the ribbon, as in Obsidian.
function onDragStart(event, item) {
  dragging.value = item.id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', item.id)
}
function onDrop(target) {
  if (dragging.value && dragging.value !== target?.id) {
    emit('update:layout', placeBefore(props.layout, dragging.value, target?.id || null))
  }
  dragging.value = null
  dropTarget.value = null
}
</script>

<style scoped>
.ribbon-btn {
  position: relative;
  display: inline-flex; align-items: center; justify-content: center;
  width: 2rem; height: 2rem; border-radius: 0.375rem;
  color: color-mix(in oklch, var(--color-base-content) 55%, transparent);
  transition: background-color 120ms, color 120ms;
}
.ribbon-btn.has-label { width: 100%; justify-content: flex-start; gap: 0.6rem; padding: 0 0.5rem; font-size: 0.8125rem; }
.ribbon-divider { align-self: center; width: 1.5rem; margin: 0.2rem 0; border-top: 1px solid var(--color-base-300); }
.ribbon-btn:hover { color: var(--color-base-content); background-color: color-mix(in oklch, var(--color-base-content) 9%, transparent); }
.ribbon-btn.is-active { color: var(--color-base-content); background-color: color-mix(in oklch, var(--color-base-content) 14%, transparent); }
.ribbon-btn.is-drop-target { box-shadow: 0 -2px 0 0 var(--color-primary); }
.ribbon-btn:focus-visible { outline: 2px solid var(--color-primary); outline-offset: 1px; }
/* Tooltip to the right, like Obsidian's ribbon. */
.ribbon-btn[data-tip]:hover::after {
  content: attr(data-tip);
  position: absolute; left: calc(100% + 0.5rem); top: 50%; transform: translateY(-50%);
  white-space: nowrap; pointer-events: none; z-index: 85;
  padding: 0.2rem 0.5rem; border-radius: 0.375rem; font-size: 0.75rem; line-height: 1.1rem;
  background: var(--color-neutral); color: var(--color-neutral-content);
}
.ribbon-dot { position: absolute; top: 0.3rem; right: 0.3rem; width: 0.45rem; height: 0.45rem; border-radius: 9999px; background: var(--color-warning); }
</style>
