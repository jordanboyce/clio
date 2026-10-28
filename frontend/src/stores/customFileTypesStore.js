import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import http from '../utils/http'
import { setCustomFileTypes } from '../utils/fileTypes'

// The extensions an administrator registered, loaded once from the open
// GET /api/custom-file-types so every surface (file tiles, kind badges, the
// "no supported files" message, the admin panel) agrees with the server.
export const useCustomFileTypesStore = defineStore('customFileTypes', () => {
  const types = ref([])
  const loaded = ref(false)
  const loading = ref(false)
  // Whether the suggestion's optional AI pass has a chat model to use
  // (the server asks the same resolver the suggestion itself uses).
  const aiAvailable = ref(false)

  const accepted = computed(() => types.value.filter(t => t.enabled !== false).map(t => t.extension))

  async function load({ force = false } = {}) {
    if (loading.value) return
    if (loaded.value && !force) return
    loading.value = true
    try {
      const response = await http.get('/api/custom-file-types', { quiet: true })
      types.value = response.data?.types || []
      aiAvailable.value = !!response.data?.ai?.available
      setCustomFileTypes(types.value)
      loaded.value = true
    } catch {
      // Not fatal: files simply keep their built-in appearance.
    } finally {
      loading.value = false
    }
  }

  return { types, loaded, loading, aiAvailable, accepted, load }
})
