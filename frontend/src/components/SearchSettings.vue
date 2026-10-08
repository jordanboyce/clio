<template>
  <dialog :ref="modal.dialogRef" class="modal modal-end" aria-labelledby="search-settings-title" @close="modal.onClosed">
    <div class="modal-box h-full max-h-full w-full max-w-md rounded-none p-0 flex flex-col">
      <header class="flex items-center justify-between gap-4 border-b border-base-300 p-5">
        <div>
          <h2 id="search-settings-title" class="text-lg font-semibold">Search settings</h2>
          <p class="text-sm text-base-content/75 mt-1">Applies to your next search.</p>
        </div>
        <button class="btn btn-ghost btn-circle min-h-11 min-w-11" aria-label="Close search settings" @click="modal.close()"><X :size="20" /></button>
      </header>
      <div class="flex-1 overflow-y-auto p-5 space-y-7">
        <fieldset class="space-y-4">
          <legend class="font-semibold px-0">AI assistance</legend>
          <p v-if="!configuredProviders.length" class="text-sm text-base-content/75 leading-relaxed">Search works without AI. <button class="link link-primary" @click="openProviderSettings">Connect a provider</button> to add answers and AI ranking.</p>
          <label class="flex items-start justify-between gap-4 cursor-pointer">
            <span><span class="block text-sm font-medium">Write an answer</span><span class="block text-sm text-base-content/75 mt-1">Summarize the matching passages with source references.</span></span>
            <input v-model="synthesize" type="checkbox" class="toggle toggle-primary shrink-0" :disabled="!configuredProviders.length" />
          </label>
          <label class="flex items-start justify-between gap-4 cursor-pointer">
            <span><span class="block text-sm font-medium">Improve result order</span><span class="block text-sm text-base-content/75 mt-1">Ask AI to put the most relevant passages first. Adds processing time.</span></span>
            <input v-model="rerank" type="checkbox" class="toggle toggle-primary shrink-0" :disabled="!configuredProviders.length" />
          </label>

          <div v-if="configuredProviders.length" class="rounded-lg border border-base-300 bg-base-200/50 p-3 text-sm">
            <div class="flex items-center gap-2">
              <Sparkles :size="14" class="text-primary shrink-0" aria-hidden="true" />
              <span class="flex-1 min-w-0 truncate font-medium">{{ getProviderDisplayName(providerId) }}<span v-if="modelName" class="font-normal text-base-content/60"> · {{ modelName }}</span></span>
              <span class="text-xs text-base-content/75">{{ isLocalProvider(providerId) ? 'Local' : 'Cloud' }}</span>
            </div>
            <p class="text-base-content/75 mt-1.5">Find uses the AI model chosen in the top bar. Change it there and Ask and Reports follow.</p>
            <p v-if="(synthesize || rerank) && !isLocalProvider(providerId)" class="text-base-content/75 mt-1.5">This cloud provider receives your query and the matching passages.</p>
          </div>
        </fieldset>

        <section class="border-t border-base-300 pt-5" aria-labelledby="search-tune-title">
          <h3 id="search-tune-title" class="font-semibold">Matching balance</h3>
          <template v-if="searchMode === 'hybrid'">
            <p class="text-sm text-base-content/75 mt-1 leading-relaxed">How much “Meaning + keywords” leans on related ideas versus exact words.</p>
            <label class="block mt-4">
              <span class="block text-sm font-medium mb-3">{{ Math.round(semanticWeight * 100) }}% meaning · {{ Math.round((1 - semanticWeight) * 100) }}% keywords</span>
              <input v-model.number="semanticWeight" type="range" min="0" max="1" step="0.1" aria-label="Meaning weight" class="range range-primary range-sm w-full" />
              <span class="flex justify-between text-sm text-base-content/75 mt-2"><span>More keywords</span><span>More meaning</span></span>
            </label>
          </template>
          <p v-else class="text-sm text-base-content/75 mt-1 leading-relaxed">Only applies to “Meaning + keywords”. Pick that mode in the toolbar to adjust the balance.</p>
        </section>
      </div>
      <footer class="border-t border-base-300 p-4 flex justify-end"><button class="btn btn-primary min-h-11" @click="modal.close()">Done</button></footer>
    </div>
    <form method="dialog" class="modal-backdrop"><button>Close search settings</button></form>
  </dialog>
</template>

<script setup>
import { computed, watch } from 'vue'
import { Sparkles, X } from 'lucide-vue-next'
import { useModal } from '../composables/useModal'
import { useProviderStore } from '../stores/providerStore'
import { getProviderDisplayName, isLocalProvider } from '../utils/aiProviders'

// Mode and result count are set in the Find toolbar; this drawer holds the
// settings that need explanation: AI assistance and the hybrid balance. Which
// model answers is not one of them: it is chosen once, in the top bar.
defineProps({
  configuredProviders: { type: Array, default: () => [] },
  providerId: { type: String, default: '' },
  searchMode: { type: String, default: 'hybrid' },
})
const emit = defineEmits(['switch-tab'])
const open = defineModel('open', { type: Boolean, default: false })
const semanticWeight = defineModel('semanticWeight', { type: Number, default: 0.7 })
const rerank = defineModel('rerank', { type: Boolean, default: true })
const synthesize = defineModel('synthesize', { type: Boolean, default: true })
const providerStore = useProviderStore()
const modelName = computed(() => providerStore.activeModel || providerStore.deploymentDefault.model || '')
const modal = useModal({ onClose: () => { open.value = false } })
watch(open, value => value ? modal.open() : modal.close(), { flush: 'post' })
function openProviderSettings() {
  modal.close()
  emit('switch-tab', 'settings')
}
</script>
