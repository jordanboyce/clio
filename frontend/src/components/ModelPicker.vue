<template>
  <!-- The one place the AI provider and model are chosen. Chat, Find and
       Reports all follow it, so there is nothing to keep in sync elsewhere. -->
  <div class="dropdown dropdown-end">
    <button
      v-if="!configured.length"
      class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-7 min-h-0 border border-dashed border-base-content/30 rounded-full px-2.5 text-base-content/70"
      @click="$emit('connect')"
    >
      <Sparkles :size="11" aria-hidden="true" />
      Connect a model
    </button>

    <template v-else>
      <label
        tabindex="0"
        class="btn btn-xs btn-ghost gap-1.5 normal-case font-normal h-7 min-h-0 border border-base-300 rounded-full px-2.5"
        :title="pillTitle"
        :aria-label="`${pillTitle}. Click to change.`"
        aria-haspopup="menu"
        @click="loadModels"
      >
        <Sparkles :size="11" class="text-primary flex-shrink-0" aria-hidden="true" />
        <span class="text-xs max-w-52 truncate">
          {{ name }}<span v-if="modelLabel" class="text-base-content/50"> · {{ modelLabel }}</span>
        </span>
        <ChevronDown :size="10" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
      </label>

      <div tabindex="0" class="dropdown-content z-[60] mt-1 bg-base-100 border border-base-300 rounded-box shadow-lg w-80 p-3 space-y-3">
        <div>
          <div class="text-[10px] font-semibold uppercase tracking-wider text-base-content/50 mb-1.5">AI provider</div>
          <ul class="space-y-0.5" role="radiogroup" aria-label="AI provider">
            <li v-for="pid in configured" :key="pid">
              <button
                class="w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-left text-sm hover:bg-base-200"
                :class="pid === activeId ? 'bg-base-200 font-medium' : ''"
                role="radio"
                :aria-checked="pid === activeId"
                @click="choose(pid)"
              >
                <Check v-if="pid === activeId" :size="13" class="text-primary flex-shrink-0" aria-hidden="true" />
                <span v-else class="w-[13px] flex-shrink-0" aria-hidden="true"></span>
                <span class="flex-1 truncate">{{ getProviderDisplayName(pid) }}</span>
                <span v-if="teamIds.includes(pid)" class="badge badge-success badge-xs">Team key</span>
                <span class="badge badge-ghost badge-xs">{{ isLocalProvider(pid) ? 'local' : 'cloud' }}</span>
              </button>
            </li>
          </ul>
        </div>

        <div v-if="activeId">
          <label class="text-[10px] font-semibold uppercase tracking-wider text-base-content/50 mb-1.5 block" for="model-picker-model">Model</label>
          <select
            id="model-picker-model"
            class="select select-bordered select-sm w-full"
            :value="currentModel"
            :disabled="deployment"
            @change="setProviderModel(activeId, $event.target.value)"
          >
            <option value="">{{ deployment ? 'Chosen by your administrator' : 'Provider default' }}</option>
            <option v-if="missingModel" :value="missingModel">{{ missingModel }}</option>
            <optgroup v-if="recommended.length" label="Recommended">
              <option v-for="m in recommended" :key="m.id" :value="m.id">{{ m.label }}</option>
            </optgroup>
            <optgroup v-if="others.length" label="All models">
              <option v-for="m in others" :key="m.id" :value="m.id">{{ m.label }}</option>
            </optgroup>
          </select>
          <p v-if="loadingModels" class="text-[11px] text-base-content/45 mt-1">Loading models…</p>
        </div>

        <p class="text-[11px] leading-snug text-base-content/55">
          Used for Ask, Find answers and Reports.
          <template v-if="activeId && !isLocalProvider(activeId)">
            Cloud providers receive the text they process.
          </template>
        </p>
        <button class="btn btn-xs btn-ghost w-full justify-start gap-1.5" @click="$emit('connect')">
          <Settings :size="12" aria-hidden="true" /> Provider settings
        </button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Check, ChevronDown, Settings, Sparkles } from 'lucide-vue-next'
import { useProviderStore } from '../stores/providerStore'
import {
  fetchProviderModels,
  getProviderDisplayName,
  getServerProviderIds,
  isDeploymentProvider,
  isLocalProvider,
  setActiveProviderLS,
  setProviderModel,
} from '../utils/aiProviders'

defineEmits(['connect'])

const providerStore = useProviderStore()

const configured = computed(() => providerStore.configuredIds)
const activeId = computed(() => providerStore.activeProviderId)
const name = computed(() => providerStore.activeProviderName)
const teamIds = computed(() => { providerStore.version; return getServerProviderIds() })
// The deployment's own provider has no model of ours to change: the server picks.
const deployment = computed(() => !!activeId.value && isDeploymentProvider(activeId.value))
const currentModel = computed(() => providerStore.activeModel)
const deploymentModel = computed(() => providerStore.deploymentDefault.model || '')
const modelLabel = computed(() => currentModel.value || (deployment.value ? deploymentModel.value : ''))
const pillTitle = computed(() => `AI: ${name.value}${modelLabel.value ? ', ' + modelLabel.value : ''}`)

const models = ref([])
const loadingModels = ref(false)
let seq = 0
const recommended = computed(() => models.value.filter(m => m.recommended))
const others = computed(() => models.value.filter(m => !m.recommended))
const missingModel = computed(() => {
  const cur = currentModel.value
  return cur && !models.value.some(m => m.id === cur) ? cur : ''
})

async function loadModels() {
  const id = activeId.value
  if (!id || deployment.value) { models.value = []; return }
  const mine = ++seq
  loadingModels.value = true
  try {
    const { models: list } = await fetchProviderModels(id)
    if (mine === seq) models.value = list || []
  } catch {
    if (mine === seq) models.value = []
  } finally {
    if (mine === seq) loadingModels.value = false
  }
}

function choose(pid) {
  setActiveProviderLS(pid)
  models.value = []
  loadModels()
}
</script>
