<template>
  <!-- D6: the browser would not copy (a plain-HTTP host); the text is here,
       selected, to copy by hand. -->
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="manual-copy-title" data-testid="manual-copy" @close="onClosed">
    <div v-if="request" class="modal-box max-w-lg space-y-3">
      <h3 id="manual-copy-title" class="font-semibold">Copy {{ request.label.toLowerCase() }}</h3>
      <p class="text-sm text-base-content/70">This browser does not let the page copy here. The text is selected below: press Ctrl+C (or Cmd+C) to copy it.</p>
      <textarea
        ref="area"
        class="textarea textarea-bordered w-full font-mono text-xs h-32"
        readonly
        :value="request.text"
        :aria-label="request.label"
        @focus="$event.target.select()"
      ></textarea>
      <div class="modal-action">
        <button class="btn btn-sm" @click="modal.close()">Done</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useModal } from '../composables/useModal'
import { useUiStore } from '../stores/uiStore'

const ui = useUiStore()
const modal = useModal()
const area = ref(null)
const request = computed(() => ui.manualCopy)

watch(request, async (r) => {
  if (!r) return
  await nextTick()
  modal.open()
  await nextTick()
  area.value?.focus()
  area.value?.select()
})

function onClosed() {
  modal.onClosed()
  ui.manualCopy = null
}
</script>
