<template>
  <!-- Admin only: GET /api/system/settings-sources answers 403 to anyone else,
       and then nothing is shown. -->
  <div v-if="loaded" class="mt-8" data-testid="environment-settings">
    <h3 class="text-sm font-medium mb-1">Set by environment</h3>
    <p class="text-xs text-base-content/55 leading-relaxed max-w-[60ch] mb-3">
      What this server's environment or env file sets, read-only. A value saved here in Settings is applied over
      the environment at every start.
    </p>

    <div v-if="overridden.length" class="alert alert-warning py-2 mb-3 text-sm items-start" role="note" data-testid="environment-overridden">
      <AlertTriangle :size="16" class="mt-0.5" aria-hidden="true" />
      <div>
        <p class="font-medium">Saved in Settings, not the environment's value</p>
        <ul class="text-xs mt-1 space-y-0.5">
          <li v-for="o in overridden" :key="o.name">
            <code>{{ o.name }}</code>: the environment says {{ shown(o.environment) }}, Settings has {{ shown(o.saved) }}, and
            Settings wins.
          </li>
        </ul>
      </div>
    </div>

    <details class="rounded-lg border border-base-300/60">
      <summary class="cursor-pointer px-3 py-2 text-xs">
        {{ environment.length }} {{ environment.length === 1 ? 'setting' : 'settings' }} from the environment
      </summary>
      <dl class="divide-y divide-base-300/50 text-xs px-3 max-h-80 overflow-y-auto">
        <div v-for="row in environment" :key="row.name" class="flex items-start justify-between gap-6 py-1.5">
          <dt class="font-mono">{{ row.name }}</dt>
          <dd class="text-right break-all text-base-content/70">
            {{ shown(row.value) }}
            <span v-if="row.overridden_in_settings" class="badge badge-warning badge-xs ml-1">Settings wins</span>
          </dd>
        </div>
      </dl>
    </details>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import http from '../utils/http'

const loaded = ref(false)
const environment = ref([])
const overridden = ref([])

const shown = (value) => {
  if (value === '' || value === null || value === undefined) return 'empty'
  return String(value)
}

onMounted(async () => {
  try {
    const { data } = await http.get('/api/system/settings-sources')
    environment.value = data.environment || []
    overridden.value = data.overridden || []
    loaded.value = true
  } catch {
    loaded.value = false // not an admin here, or an older server
  }
})
</script>
