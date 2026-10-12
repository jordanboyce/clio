import { ref } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useCollectionStore } from './collectionStore'

// Collection stats (documents/pages/chunks + offline flag) for the footer,
// the tab empty-states, and anything else that needs them.
//
// Previously App.vue owned this object, re-fetched it from four separate
// watchers, and prop-drilled chunkCount/documentCount into SearchTab and
// ChatTab. One store, one debounced fetch; consumers read reactively.
export const useStatsStore = defineStore('stats', () => {
  const documents = ref(0)
  const pages = ref(0)
  const chunks = ref(0)
  // Storage against the per-collection cap (limit 0 = unlimited).
  const storageBytes = ref(0)
  const storageLimitBytes = ref(0)
  const storagePercent = ref(0)
  const offline = ref(false)
  // The configured local embedding model is not in the local cache, so it
  // would have to be downloaded on first use — impossible on a network that
  // blocks huggingface.co. Surfaced as a banner until fixed or dismissed.
  const embeddingModelMissing = ref(false)
  // Live readiness of the search index: {status, backend, model, provider,
  // progress, error, can_download}. `status` is ready | loading |
  // downloading | missing | error | unconfigured. Polled by the banner while
  // a download is in flight.
  const embedding = ref(null)
  const loaded = ref(false)
  let embeddingPollTimer = null

  let inFlight = null
  let debounceTimer = null

  async function fetchStats() {
    const collectionStore = useCollectionStore()
    const collectionId = collectionStore.currentCollectionId
    // Coalesce: concurrent callers share one request.
    if (inFlight) return inFlight
    inFlight = (async () => {
      try {
        // SQL-backed aggregates — never the unpaginated /documents list,
        // which is a ~25MB response at 63k documents.
        const [statsResponse, healthResponse] = await Promise.all([
          http.get(`/api/collections/${encodeURIComponent(collectionId)}/stats`),
          http.get(`/health?collection_id=${encodeURIComponent(collectionId)}`),
        ])
        documents.value = statsResponse.data.total_documents || 0
        pages.value = statsResponse.data.total_pages || 0
        chunks.value = statsResponse.data.total_chunks || 0
        storageBytes.value = statsResponse.data.storage_bytes || 0
        storageLimitBytes.value = statsResponse.data.storage_limit_bytes || 0
        storagePercent.value = statsResponse.data.storage_percent || 0
        offline.value = !!healthResponse.data.offline_mode
        applyEmbedding(healthResponse.data.embedding)
        loaded.value = true
      } catch {
        // Stats are decorative; the http interceptor already surfaced any
        // real connectivity problem via the offline banner.
      } finally {
        inFlight = null
      }
    })()
    return inFlight
  }

  function applyEmbedding(info) {
    if (!info) return
    embedding.value = { ...(embedding.value || {}), ...info }
    const status = info.status
    embeddingModelMissing.value = status ? status === 'missing' : !!info.local_model_missing
    if (status === 'downloading' || status === 'loading') startEmbeddingPolling()
    else stopEmbeddingPolling()
  }

  async function refreshEmbedding() {
    try {
      const { data } = await http.get('/api/embedding/status')
      applyEmbedding(data)
    } catch { /* the next health poll will catch up */ }
  }

  function startEmbeddingPolling() {
    if (embeddingPollTimer) return
    embeddingPollTimer = setInterval(refreshEmbedding, 1500)
  }
  function stopEmbeddingPolling() {
    if (embeddingPollTimer) clearInterval(embeddingPollTimer)
    embeddingPollTimer = null
  }

  // Ask the server to fetch and load the configured local model now.
  async function warmEmbedding() {
    const { data } = await http.post('/api/embedding/warm')
    applyEmbedding(data)
    return data
  }

  // For high-frequency triggers (job progress ticks): trailing-edge debounce.
  function fetchStatsDebounced(delay = 500) {
    clearTimeout(debounceTimer)
    debounceTimer = setTimeout(fetchStats, delay)
  }

  return {
    documents, pages, chunks, storageBytes, storageLimitBytes, storagePercent,
    offline, embeddingModelMissing, embedding, loaded, fetchStats, fetchStatsDebounced,
    refreshEmbedding, warmEmbedding, applyEmbedding,
  }
})
