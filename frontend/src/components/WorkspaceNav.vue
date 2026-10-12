<template>
  <nav :class="mobile ? 'flex items-stretch' : 'flex items-center gap-1.5'" aria-label="Workspace">
    <!-- Ask and Find are one choice, not two destinations, so on desktop they
         ride in a switch. The phone tab bar keeps them flat: a recessed track
         inside a row of tabs would read as a control, not a destination. -->
    <div v-if="!mobile && modeTabs.length > 1" class="segmented" role="group" aria-label="Workspace mode">
      <button
        v-for="tab in modeTabs"
        :key="tab.id"
        data-nav="primary"
        class="segment"
        :class="activeTab === tab.id ? 'is-on text-base-content' : 'text-base-content/60 hover:text-base-content'"
        :aria-current="activeTab === tab.id ? 'page' : undefined"
        @click="$emit('navigate', tab.id)"
      >
        <component :is="tab.icon" :size="14" aria-hidden="true" />
        <span>{{ tab.label }}</span>
      </button>
    </div>
    <button
      v-for="tab in looseTabs"
      :key="tab.id"
      data-nav="primary"
      :class="[buttonClass, activeTab === tab.id ? 'bg-base-200 font-semibold text-base-content' : 'text-base-content/65']"
      :aria-current="activeTab === tab.id ? 'page' : undefined"
      @click="$emit('navigate', tab.id)"
    >
      <component :is="tab.icon" :size="mobile ? 19 : 14" aria-hidden="true" />
      <span>{{ tab.label }}</span>
    </button>
    <details ref="menu" class="dropdown dropdown-end" :class="mobile ? 'dropdown-top flex-1' : ''" @keydown.esc="closeMenu">
      <summary :class="[buttonClass, 'list-none', mobile ? 'w-full h-full' : '', secondaryActive ? 'bg-base-200 font-semibold' : 'text-base-content/65']">
        <span class="relative inline-flex">
          <Ellipsis :size="mobile ? 19 : 14" aria-hidden="true" />
          <span
            v-if="adminConsole && adminBadge > 0"
            class="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-warning"
            aria-hidden="true"
          ></span>
        </span>
        <span>{{ secondaryLabel }}</span>
      </summary>
      <ul class="dropdown-content menu z-[70] w-56 rounded-box bg-base-100 border border-base-300 p-2 shadow-lg">
        <li v-for="tab in secondaryTabs" :key="tab.id">
          <button :aria-current="activeTab === tab.id ? 'page' : undefined" @click="navigate(tab.id)">
            <component :is="tab.icon" :size="15" aria-hidden="true" />{{ tab.label }}
            <span
              v-if="tab.id === 'admin' && adminBadge > 0"
              class="badge badge-warning badge-xs ml-auto tabular-nums"
              :aria-label="`${adminBadge} waiting for review`"
            >{{ adminBadge }}</span>
          </button>
        </li>
        <li><button @click="openNotes"><StickyNote :size="15" aria-hidden="true" />Notes and tools</button></li>
      </ul>
    </details>
  </nav>
</template>

<script setup>
import { computed, ref } from 'vue'
import { MessageSquare, Search, Plug, Ellipsis, FileText, BookOpen, Gauge, StickyNote } from 'lucide-vue-next'

const props = defineProps({
  activeTab: { type: String, required: true },
  chatEnabled: { type: Boolean, default: true },
  adminConsole: { type: Boolean, default: false },
  // Items waiting for an administrator; shown as a dot on "More" and a count on Administration.
  adminBadge: { type: Number, default: 0 },
  mobile: { type: Boolean, default: false },
})
const emit = defineEmits(['navigate', 'notes'])
const menu = ref(null)
const buttonClass = computed(() => props.mobile
  ? 'flex-1 min-w-0 flex flex-col items-center justify-center gap-1 min-h-14 px-2 py-2 text-xs cursor-pointer'
  : 'btn btn-xs btn-ghost gap-1.5 rounded-md font-normal')
const primaryTabs = computed(() => [
  ...(props.chatEnabled ? [{ id: 'chat', label: 'Ask', icon: MessageSquare }] : []),
  { id: 'search', label: 'Find', icon: Search },
  { id: 'mcp', label: 'Connect', icon: Plug },
])
// The two ways of asking the same corpus a question; the switch only earns
// its track when both are there (search-only deployments get a plain button).
const modeTabs = computed(() => primaryTabs.value.filter(tab => tab.id === 'chat' || tab.id === 'search'))
const looseTabs = computed(() => (props.mobile || modeTabs.value.length < 2)
  ? primaryTabs.value
  : primaryTabs.value.filter(tab => !modeTabs.value.includes(tab)))
const secondaryTabs = computed(() => [
  { id: 'generate', label: 'Reports', icon: FileText },
  { id: 'expertise', label: 'Saved instructions', icon: BookOpen },
  ...(props.adminConsole ? [{ id: 'admin', label: 'Administration', icon: Gauge }] : []),
])
const secondaryActive = computed(() => secondaryTabs.value.some(tab => tab.id === props.activeTab))
const secondaryLabel = computed(() => secondaryTabs.value.find(tab => tab.id === props.activeTab)?.label || 'More')
function closeMenu() {
  if (menu.value) {
    menu.value.open = false
    menu.value.querySelector('summary')?.focus()
  }
}
function navigate(id) { closeMenu(); emit('navigate', id) }
function openNotes() { closeMenu(); emit('notes') }
</script>
