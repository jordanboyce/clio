<template>
  <!-- D5: the one confirm dialog (ui.confirm), in place of the browser's
       native confirm(), which blocks the page, ignores the theme and cannot
       say which action is destructive. Escape or the backdrop is "No". -->
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="confirm-title" data-testid="confirm-dialog" @close="onClosed">
    <div v-if="request" class="modal-box max-w-md space-y-3">
      <h3 id="confirm-title" class="font-semibold">{{ request.title }}</h3>
      <p v-if="request.message" class="text-sm text-base-content/75 whitespace-pre-line">{{ request.message }}</p>
      <div class="modal-action">
        <button class="btn btn-sm btn-ghost" data-testid="confirm-cancel" @click="answer(false)">{{ request.cancelLabel || 'Cancel' }}</button>
        <button
          class="btn btn-sm"
          :class="request.danger ? 'btn-error' : 'btn-primary'"
          data-testid="confirm-ok"
          @click="answer(true)"
        >{{ request.confirmLabel || 'Confirm' }}</button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Cancel">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, nextTick, watch } from 'vue'
import { useModal } from '../composables/useModal'
import { useUiStore } from '../stores/uiStore'

const ui = useUiStore()
const modal = useModal()
const request = computed(() => ui.confirmRequest)
let answered = false

watch(request, async (r) => {
  if (!r) return
  answered = false
  await nextTick()
  modal.open()
})

function answer(value) {
  answered = true
  ui.settleConfirm(value)
  modal.close()
}

function onClosed() {
  modal.onClosed()
  if (!answered) ui.settleConfirm(false)
}
</script>
