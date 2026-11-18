import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useSearchStore } from './searchStore'
import { useUiStore } from './uiStore'

export const useCollectionStore = defineStore('collection', () => {
  // State. Identity (userId) and the private-collections flag deliberately
  // live in userStore only — they used to be duplicated here, populated
  // from a different endpoint, and the two copies could disagree.
  const collections = ref([])
  const currentCollectionId = ref('default')
  const loading = ref(false)

  // Computed
  const currentCollection = computed(() => {
    return collections.value.find(c => c.id === currentCollectionId.value) || null
  })

  const sortedCollections = computed(() => {
    // Owned first (default at top), then shared
    const owned = collections.value.filter(c => !c.shared)
    const shared = collections.value.filter(c => c.shared)
    owned.sort((a, b) => {
      if (a.id === 'default') return -1
      if (b.id === 'default') return 1
      return a.name.localeCompare(b.name)
    })
    shared.sort((a, b) => a.name.localeCompare(b.name))
    return [...owned, ...shared]
  })

  const ownedCollections = computed(() => collections.value.filter(c => !c.shared))
  const sharedCollections = computed(() => collections.value.filter(c => c.shared))

  const currentPermission = computed(() => {
    const c = currentCollection.value
    return c?.permission || 'owner'
  })

  // Two gates, not one. canEdit is the *contributor* gate — may this person
  // add and remove sources. canConfigure is the *owner* gate — may they
  // change how the collection is built (chunking, embedding model, guide,
  // re-indexing, publishing, deleting). A read-write collaborator clears the
  // first and not the second, mirroring api/deps.require_collection_access.
  // Everything a reader cannot do is hidden rather than shown disabled: the
  // point of the reader view is that it is small, not that it is greyed out.
  const canEditCurrent = computed(() => {
    return currentPermission.value === 'owner' || currentPermission.value === 'readwrite'
  })

  const canConfigureCurrent = computed(() => currentPermission.value === 'owner')

  const isReadOnlyCurrent = computed(() => currentPermission.value === 'read')

  const currentIsPublished = computed(() => !!currentCollection.value?.published)

  function canConfigure(collection) {
    return (collection?.permission || 'owner') === 'owner'
  }

  // Why the owner-tier actions are not this person's, in the words of the
  // server's own 403 (api/deps.require_collection_access), or null when they
  // are. A team collection has no single owner: once ADMIN_EMAILS is in
  // force its setup belongs to the admins, and everyone else contributes.
  function configureBlockedReason(collection) {
    const permission = collection?.permission || 'owner'
    if (permission === 'owner') return null
    if (permission === 'read') return 'This collection is shared with you read-only.'
    if (collection?.team) {
      return 'This is a team collection: only an admin can change how it is set up, export, relabel or delete it. You can add and remove sources.'
    }
    return "Only the collection's owner can change how it is set up, export or delete it. Make your own copy to get one you control."
  }

  const currentConfigureBlockedReason = computed(() => configureBlockedReason(currentCollection.value))

  // Actions. CRUD failures throw the http client's normalized error —
  // callers decide how to present them. loadCollections is fire-and-forget
  // from several places, so it reports its own failure.
  async function loadCollections() {
    loading.value = true
    try {
      const response = await http.get('/api/collections')
      collections.value = response.data.collections || []

      // Ensure current collection still exists
      const exists = collections.value.some(c => c.id === currentCollectionId.value)
      if (!exists && collections.value.length > 0) {
        currentCollectionId.value = 'default'
      }
    } catch (err) {
      useUiStore().toastError(err, 'Failed to load collections')
    } finally {
      loading.value = false
    }
  }

  async function createCollection(data) {
    loading.value = true
    try {
      const response = await http.post('/api/collections', data)
      collections.value.push(response.data)
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function updateCollection(collectionId, updates) {
    loading.value = true
    try {
      const response = await http.put(`/api/collections/${collectionId}`, updates)
      const index = collections.value.findIndex(c => c.id === collectionId)
      if (index !== -1) {
        // PUT answers with the stored row, which carries no permission/share
        // flags — merging rather than replacing keeps the ones the list
        // resolved, so an edit can't silently promote a reader to owner.
        const { permission, team, shared } = collections.value[index]
        collections.value[index] = { permission, team, shared, ...response.data }
      }
      return response.data
    } finally {
      loading.value = false
    }
  }

  // Clone: read access is enough. This is the way out of a collection you
  // can read but not configure — you get your own copy, unpublished, with
  // the build settings inherited and a background job indexing it.
  async function cloneCollection(collectionId, name = null) {
    loading.value = true
    try {
      const response = await http.post(`/api/collections/${collectionId}/clone`,
        name ? { name } : {})
      await loadCollections()
      return response.data
    } finally {
      loading.value = false
    }
  }

  async function deleteCollection(collectionId) {
    if (collectionId === 'default') return false

    loading.value = true
    try {
      await http.delete(`/api/collections/${collectionId}`)
      collections.value = collections.value.filter(c => c.id !== collectionId)

      // If we deleted the current collection, switch to default
      if (currentCollectionId.value === collectionId) {
        currentCollectionId.value = 'default'
      }
      return true
    } finally {
      loading.value = false
    }
  }

  function setCurrentCollection(collectionId) {
    // Only do something if the collection is actually changing
    if (collectionId === currentCollectionId.value) {
      return
    }

    currentCollectionId.value = collectionId
    // Persist selection
    localStorage.setItem('clio_current_collection', collectionId)

    // Clear search state when switching collections
    const searchStore = useSearchStore()
    searchStore.setQuery('')
    searchStore.clearResults()
    searchStore.syncCacheCount()  // Update cache count for new collection
  }

  function initializeFromStorage() {
    const saved = localStorage.getItem('clio_current_collection')
    if (saved) {
      currentCollectionId.value = saved
    }
  }

  // Initialize
  initializeFromStorage()

  return {
    // State
    collections,
    currentCollectionId,
    loading,
    // Computed
    currentCollection,
    sortedCollections,
    ownedCollections,
    sharedCollections,
    currentPermission,
    canEditCurrent,
    canConfigureCurrent,
    isReadOnlyCurrent,
    currentIsPublished,
    canConfigure,
    configureBlockedReason,
    currentConfigureBlockedReason,
    // Actions
    loadCollections,
    createCollection,
    updateCollection,
    cloneCollection,
    deleteCollection,
    setCurrentCollection,
  }
})
