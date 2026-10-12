import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  fetchDeploymentDefault,
  fetchServerProviderIds,
  getConfiguredProviderIds,
  getDeploymentDefault,
  getProviderConfig,
  getProviderDisplayName,
  onProviderChange,
  resolveProvider,
} from '../utils/aiProviders'

// Reactive facade over utils/aiProviders.js.
//
// Provider persistence stays in that module (localStorage, and the
// resolution chain documented there is still normative). What lived
// outside it was the reactivity hack: localStorage isn't reactive, so a
// window CustomEvent ('clio:provider-changed') fanned out to listeners
// in four components, and App.vue kept a providerPillVersion counter to
// force recomputation. This store replaces all of that with one version
// ref: every write path in aiProviders.js calls notifyProviderChange(),
// which bumps it, and anything computed through the store re-resolves.
export const useProviderStore = defineStore('provider', () => {
  const version = ref(0)

  // Called by aiProviders.notifyProviderChange() on every config write.
  function touch() {
    version.value++
  }

  // Every write path in aiProviders.js funnels through
  // notifyProviderChange(); subscribing here is what turns those writes
  // into Vue reactivity for all consumers of this store.
  onProviderChange(touch)

  const configuredIds = computed(() => {
    version.value // reactivity: re-list when any config changes
    return getConfiguredProviderIds()
  })

  const activeProviderId = computed(() => {
    version.value
    return resolveProvider()
  })

  const activeProviderName = computed(() =>
    activeProviderId.value ? getProviderDisplayName(activeProviderId.value) : ''
  )

  // The configured model of the active provider ('' = the provider's own default).
  const activeModel = computed(() => {
    version.value
    const id = resolveProvider()
    return (id && getProviderConfig(id)?.model) || ''
  })

  // The provider the deployment itself configured (on-prem private model).
  // Reading version keeps consumers reactive across loadServerProviders().
  const deploymentDefault = computed(() => {
    version.value
    return getDeploymentDefault()
  })

  async function loadServerProviders() {
    // Both describe "what is already configured for you" and both feed
    // getConfiguredProviderIds(), so they load together and touch once.
    await Promise.all([fetchServerProviderIds(), fetchDeploymentDefault()])
    touch()
  }

  return {
    version,
    touch,
    configuredIds,
    activeProviderId,
    activeProviderName,
    deploymentDefault,
    activeModel,
    loadServerProviders,
  }
})
