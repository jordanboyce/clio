<script setup>
import { useUiStore } from '../stores/uiStore'
import { X, CheckCircle2, CircleAlert, AlertTriangle, Info, WifiOff } from 'lucide-vue-next'

const ui = useUiStore()

// Toasts are neutral cards on any theme: the status is carried by the icon
// and a thin tint on its colour, not by a full-colour alert block.
const icons = { success: CheckCircle2, error: CircleAlert, warning: AlertTriangle, info: Info }
const iconClass = { success: 'text-success', error: 'text-error', warning: 'text-warning', info: 'text-info' }
</script>

<template>
  <!-- aria-live: screen readers announce new toasts without focus moving -->
  <div
    class="toast toast-end toast-bottom z-[100] items-end"
    aria-live="polite"
    aria-atomic="false"
  >
    <TransitionGroup name="toast">
      <div
        v-for="toast in ui.toasts"
        :key="toast.id"
        class="relative flex items-start gap-2.5 w-[22rem] max-w-[calc(100vw-2rem)] rounded-xl bg-base-100 px-3.5 py-3 shadow-lg ring-1 ring-base-content/10"
        role="status"
      >
        <component :is="icons[toast.type] || Info" :size="16" class="mt-0.5 flex-shrink-0" :class="iconClass[toast.type] || 'text-info'" aria-hidden="true" />
        <span class="flex-1 min-w-0 text-[13px] leading-snug whitespace-pre-line break-words">{{ toast.message }}</span>
        <button
          class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content -mr-1 -mt-1"
          aria-label="Dismiss notification"
          @click="ui.dismiss(toast.id)"
        >
          <X :size="13" aria-hidden="true" />
        </button>
      </div>
    </TransitionGroup>
  </div>

  <!-- Backend-unreachable banner: cleared by the first successful request -->
  <Transition name="rise">
    <div
      v-if="ui.offline"
      class="fixed top-0 inset-x-0 z-[101] flex justify-center pointer-events-none"
      role="alert"
    >
      <div class="notice notice-warning shadow-lg rounded-t-none rounded-b-xl bg-base-100 ring-1 ring-base-content/10 max-w-md pointer-events-auto">
        <WifiOff :size="15" class="text-warning" aria-hidden="true" />
        <span>Cannot reach the server — retrying as you work. Your last change may not be saved.</span>
      </div>
    </div>
  </Transition>
</template>
