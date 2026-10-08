<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="palette-backdrop"
        role="presentation"
        @mousedown.self="close"
      >
        <Transition name="pop" appear>
          <div
            class="palette"
            role="dialog"
            aria-modal="true"
            aria-label="Command palette"
            @keydown="onKeydown"
          >
            <div class="palette-field">
              <Search :size="16" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
              <input
                ref="inputRef"
                v-model="query"
                type="text"
                autocomplete="off"
                autocorrect="off"
                spellcheck="false"
                placeholder="Jump to, switch, or do…"
                aria-label="Search commands"
                role="combobox"
                aria-expanded="true"
                aria-controls="palette-listbox"
                :aria-activedescendant="active ? `palette-item-${active.id}` : undefined"
              />
              <span class="kbd-hint" aria-hidden="true">Esc</span>
            </div>

            <div id="palette-listbox" ref="listRef" class="palette-list scroll-quiet" role="listbox">
              <template v-for="group in groups" :key="group.title">
                <div class="palette-group" role="presentation">{{ group.title }}</div>
                <button
                  v-for="item in group.items"
                  :id="`palette-item-${item.id}`"
                  :key="item.id"
                  type="button"
                  class="palette-row"
                  :class="{ 'is-active': active && active.id === item.id }"
                  role="option"
                  :aria-selected="active && active.id === item.id"
                  @mousemove="activeIndex = flat.indexOf(item)"
                  @click="run(item)"
                >
                  <component :is="item.icon" :size="15" class="text-base-content/55 flex-shrink-0" aria-hidden="true" />
                  <span v-if="item.swatch" class="w-2 h-2 rounded-full flex-shrink-0 -ml-1" :style="{ backgroundColor: item.swatch }" aria-hidden="true"></span>
                  <span class="truncate">{{ item.label }}</span>
                  <span v-if="item.detail" class="text-xs text-base-content/45 truncate">{{ item.detail }}</span>
                  <span v-if="item.keys" class="palette-hint flex items-center gap-1" aria-hidden="true">
                    <span v-for="(k, i) in item.keys" :key="i" class="kbd-hint">{{ k }}</span>
                  </span>
                  <span v-else-if="item.hint" class="palette-hint">{{ item.hint }}</span>
                </button>
              </template>
              <div v-if="flat.length === 0" class="px-3 py-8 text-center text-sm text-base-content/45">
                Nothing matches “{{ query }}”
              </div>
            </div>
          </div>
        </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { Search } from 'lucide-vue-next'

// The palette is pure presentation: the shell hands it a flat list of
// commands (label, group, icon, run, optional keywords/keys/detail) and it
// does the filtering, ranking and keyboard handling. It never reaches into
// stores itself, so what it can do is exactly what App.vue says it can.
const props = defineProps({
  open: { type: Boolean, default: false },
  commands: { type: Array, default: () => [] },
})
const emit = defineEmits(['close'])

const query = ref('')
const activeIndex = ref(0)
const inputRef = ref(null)
const listRef = ref(null)

const normalise = (s) => (s || '').toLowerCase().normalize('NFKD').replace(/[̀-ͯ]/g, '')

// Subsequence match with a small ranking: prefix beats word-start beats
// scattered letters, and shorter labels win ties. Good enough for a few
// dozen commands and it tolerates typos of omission.
function score(item, q) {
  if (!q) return 1
  const hay = normalise(`${item.label} ${item.keywords || ''} ${item.group}`)
  const label = normalise(item.label)
  if (label.startsWith(q)) return 100 - label.length / 100
  if (hay.includes(q)) {
    const idx = hay.indexOf(q)
    const wordStart = idx === 0 || /\s/.test(hay[idx - 1])
    return (wordStart ? 60 : 40) - idx / 100
  }
  let i = 0
  for (const ch of hay) {
    if (ch === q[i]) i++
    if (i === q.length) return 10 - hay.length / 1000
  }
  return 0
}

const flat = computed(() => {
  const q = normalise(query.value.trim())
  const scored = props.commands
    .filter(c => !c.hidden)
    .map(c => ({ item: c, s: score(c, q) }))
    .filter(x => x.s > 0)
  if (q) scored.sort((a, b) => b.s - a.s)
  return scored.map(x => x.item)
})

// Group order follows first appearance so the ranking still shows through.
const groups = computed(() => {
  const order = []
  const byTitle = new Map()
  for (const item of flat.value) {
    if (!byTitle.has(item.group)) { byTitle.set(item.group, []); order.push(item.group) }
    byTitle.get(item.group).push(item)
  }
  return order.map(title => ({ title, items: byTitle.get(title) }))
})

const active = computed(() => flat.value[activeIndex.value] || null)

watch(flat, () => { activeIndex.value = 0 })
watch(() => props.open, async (v) => {
  if (v) {
    query.value = ''
    activeIndex.value = 0
    await nextTick()
    inputRef.value?.focus()
  }
})

function close() { emit('close') }

function run(item) {
  close()
  // Let the dialog unmount before the action moves focus elsewhere.
  nextTick(() => item.run())
}

function scrollActiveIntoView() {
  nextTick(() => {
    const el = listRef.value?.querySelector('.palette-row.is-active')
    el?.scrollIntoView?.({ block: 'nearest' })
  })
}

function onKeydown(e) {
  if (e.key === 'Escape') { e.preventDefault(); close(); return }
  if (e.key === 'ArrowDown' || (e.ctrlKey && e.key === 'n')) {
    e.preventDefault()
    if (flat.value.length) activeIndex.value = (activeIndex.value + 1) % flat.value.length
    scrollActiveIntoView()
    return
  }
  if (e.key === 'ArrowUp' || (e.ctrlKey && e.key === 'p')) {
    e.preventDefault()
    if (flat.value.length) activeIndex.value = (activeIndex.value - 1 + flat.value.length) % flat.value.length
    scrollActiveIntoView()
    return
  }
  if (e.key === 'Home') { e.preventDefault(); activeIndex.value = 0; scrollActiveIntoView(); return }
  if (e.key === 'End') { e.preventDefault(); activeIndex.value = Math.max(0, flat.value.length - 1); scrollActiveIntoView(); return }
  if (e.key === 'Enter') {
    e.preventDefault()
    if (active.value) run(active.value)
  }
}
</script>
