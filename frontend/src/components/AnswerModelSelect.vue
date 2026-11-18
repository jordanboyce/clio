<template>
  <!-- The model that writes answers, for one provider. Only chat models are
       offered (the server's capability records say which); models for other
       jobs are counted, not listed, and ones of unknown kind stay reachable. -->
  <div class="form-control">
    <label class="label p-0 pb-1" :for="selectId">
      <span class="label-text text-sm font-medium">Model for answers</span>
      <button
        type="button"
        class="label-text-alt btn btn-ghost btn-sm"
        :disabled="loading"
        :aria-label="`Refresh the model list`"
        @click="$emit('refresh')"
      >{{ loading ? 'Loading…' : 'Refresh' }}</button>
    </label>
    <select
      :id="selectId"
      class="select select-bordered select-sm w-full"
      :value="customMode ? '__custom__' : (modelValue || '')"
      @change="$emit('select', $event.target.value)"
    >
      <option v-if="allowDefault" value="">{{ defaultLabel }}</option>
      <option v-if="missing" :value="missing">{{ missing }}</option>
      <optgroup v-if="groups.recommended.length" label="Recommended">
        <option v-for="m in groups.recommended" :key="m.id" :value="m.id">{{ m.display }}</option>
      </optgroup>
      <optgroup v-if="groups.fits.length" :label="groups.recommended.length ? 'Other chat models' : 'Chat models'">
        <option v-for="m in groups.fits" :key="m.id" :value="m.id">{{ m.display }}</option>
      </optgroup>
      <optgroup v-if="groups.unknown.length" label="Not known to answer">
        <option v-for="m in groups.unknown" :key="m.id" :value="m.id">{{ m.display }}</option>
      </optgroup>
      <option v-if="allowCustom" value="__custom__">Type a model id…</option>
    </select>
    <input
      v-if="customMode"
      :value="modelValue"
      type="text"
      placeholder="exact model id"
      aria-label="Model id"
      class="input input-bordered input-sm w-full mt-2"
      @input="$emit('update:modelValue', $event.target.value)"
      @change="$emit('commit')"
    />
    <p v-if="abilities.length" class="text-xs text-base-content/70 mt-1.5" data-testid="model-abilities">
      {{ abilities.join(' · ') }}<span v-if="guessed" class="text-base-content/50"> · from Clio’s table, not reported by the provider</span>
    </p>
    <p v-if="otherJobsText" class="text-xs text-base-content/60 mt-1" data-testid="model-other-jobs">
      Not listed: {{ otherJobsText }} models, which cannot write answers.
    </p>
    <p v-if="error" class="text-error text-sm mt-1" role="alert">{{ error }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { describeOtherJobs, modelAbilities, modelsForJob } from '../utils/aiProviders'

const props = defineProps({
  selectId: { type: String, required: true },
  models: { type: Array, default: () => [] },
  modelValue: { type: String, default: '' },
  customMode: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  allowDefault: { type: Boolean, default: true },
  allowCustom: { type: Boolean, default: true },
  defaultLabel: { type: String, default: 'Use provider defaults' },
})
defineEmits(['select', 'update:modelValue', 'commit', 'refresh'])

const groups = computed(() => modelsForJob(props.models, 'answers'))
const current = computed(() => props.models.find(m => m.id === props.modelValue) || null)
// A saved model the provider no longer lists still shows, so the select
// never silently claims a different one.
const missing = computed(() =>
  !props.customMode && props.modelValue && props.models.length && !current.value ? props.modelValue : ''
)
const abilities = computed(() => modelAbilities(current.value))
const guessed = computed(() => {
  const f = current.value?.pattern_fields || []
  return f.includes('tools') || f.includes('vision')
})
const otherJobsText = computed(() => describeOtherJobs(groups.value.otherJobs))
</script>
