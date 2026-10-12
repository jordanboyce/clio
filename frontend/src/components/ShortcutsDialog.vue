<template>
  <dialog ref="dialogRef" class="modal" aria-labelledby="shortcuts-title" @close="$emit('close')">
    <div class="modal-box max-w-xl p-0 overflow-hidden">
      <div class="flex items-center justify-between px-5 pt-4 pb-3 border-b border-base-300/60">
        <h2 id="shortcuts-title" class="text-sm font-semibold">Keyboard shortcuts</h2>
        <button type="button" class="side-icon-btn text-base-content/60 hover:text-base-content" aria-label="Close" @click="close">
          <X :size="15" aria-hidden="true" />
        </button>
      </div>
      <div class="grid sm:grid-cols-2 gap-x-8 gap-y-5 px-5 py-4 max-h-[70vh] overflow-y-auto scroll-quiet">
        <section v-for="group in SHORTCUT_GROUPS" :key="group.title">
          <h3 class="side-label text-base-content/50 mb-1.5">{{ group.title }}</h3>
          <ul class="divide-y divide-base-300/40">
            <li v-for="item in group.items" :key="item.label" class="flex items-center justify-between gap-3 py-1.5 text-[13px]">
              <span class="text-base-content/80">{{ item.label }}</span>
              <span class="flex items-center gap-1 flex-shrink-0" aria-hidden="true">
                <template v-for="(k, i) in item.keys" :key="i">
                  <span v-if="i > 0 && item.seq" class="text-[10px] text-base-content/35">then</span>
                  <span class="kbd-hint">{{ k }}</span>
                </template>
              </span>
              <span class="sr-only">{{ item.keys.join(item.seq ? ' then ' : ' + ') }}</span>
            </li>
          </ul>
        </section>
      </div>
      <p class="px-5 pb-4 text-xs text-base-content/45">Press <span class="kbd-hint">{{ MOD }}</span> <span class="kbd-hint">K</span> for everything else.</p>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { X } from 'lucide-vue-next'
import { SHORTCUT_GROUPS, MOD } from '../utils/shortcuts'

const props = defineProps({ open: { type: Boolean, default: false } })
const emit = defineEmits(['close'])
const dialogRef = ref(null)

function sync() {
  const d = dialogRef.value
  if (!d) return
  if (props.open && !d.open) d.showModal?.()
  else if (!props.open && d.open) d.close()
}
function close() { emit('close') }
watch(() => props.open, sync)
onMounted(sync)
</script>
