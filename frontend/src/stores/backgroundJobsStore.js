import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import http from '../utils/http'
import { useUiStore } from './uiStore'

// Indexing jobs, as the rest of the app sees them.
//
// A job is created by the server, runs in a server thread, and outlives
// the tab that started it. This store is only a view of that: it opens an
// SSE stream per job, polls as a fallback, and keeps finished jobs around
// so the drawer can still answer "did those files actually go in?".
//
// Three things it deliberately does that the first version did not:
//  - finished jobs are loaded from the server on startup, not just active
//    ones, because a job's failed-file list is the only record a person
//    has of sources that silently did not make it in;
//  - a job waiting for a slot reports its place in line, since the server
//    now queues jobs instead of refusing them;
//  - cancellation is shown as "cancelling" until the server confirms it,
//    because a running job stops at the next file boundary, not at once.
export const useBackgroundJobsStore = defineStore('backgroundJobs', () => {
  // State
  const uploadJobs = ref([]) // { jobId, collectionId, status, progress, currentFile, ... }
  const reindexJob = ref(null) // Only one reindex job at a time
  const pollingInterval = ref(null)
  const eventSources = ref({}) // SSE connections per job
  // Job ids the person has cleared from the drawer. Kept so a history
  // reload does not resurrect what they just dismissed.
  const dismissedJobIds = ref(new Set())

  const ACTIVE_STATUSES = ['pending', 'queued', 'running', 'cancelling']
  const isActiveStatus = (status) => ACTIVE_STATUSES.includes(status)

  // Computed
  const hasActiveJobs = computed(() => {
    const activeUploads = uploadJobs.value.some(j => isActiveStatus(j.status))
    const activeReindex = reindexJob.value && isActiveStatus(reindexJob.value.status)
    return activeUploads || activeReindex
  })

  const activeJobCount = computed(() => {
    let count = uploadJobs.value.filter(j => isActiveStatus(j.status)).length
    if (reindexJob.value && isActiveStatus(reindexJob.value.status)) count += 1
    return count
  })

  function failedFilesOf(job) {
    const summary = job.resultSummary
    if (!summary || !Array.isArray(summary.failed_files)) return []
    return summary.failed_files
  }

  const allJobs = computed(() => {
    const jobs = []

    uploadJobs.value.forEach(job => {
      const failed = failedFilesOf(job)
      jobs.push({
        type: job.jobType || 'upload',
        id: job.jobId,
        collectionId: job.collectionId,
        status: job.status,
        progress: job.progressPercent || 0,
        currentFile: job.currentFile,
        totalFiles: job.totalFiles,
        processedFiles: job.processedFiles,
        error: job.error,
        startedAt: job.startedAt,
        completedAt: job.completedAt,
        // Granular progress
        phase: job.phase,
        phaseProgress: job.phaseProgress || 0,
        phaseDetail: job.phaseDetail,
        chunksProcessed: job.chunksProcessed || 0,
        chunksTotal: job.chunksTotal || 0,
        // Queue + cancellation
        queuePosition: job.queuePosition,
        cancelRequested: job.cancelRequested || false,
        // Outcome, kept for finished jobs
        indexedFiles: job.resultSummary?.documents_processed ?? null,
        failedFiles: failed,
        // Stable identifier of why the job failed (e.g. embedding_mismatch),
        // from the job itself or, failing that, its first coded file failure.
        errorCode: job.resultSummary?.error_code || failed.find(f => f.code)?.code || null,
        skippedFiles: job.skippedFiles || [],
        cancellable: job.jobType !== 'reindex' && isActiveStatus(job.status),
      })
    })

    if (reindexJob.value) {
      jobs.push({
        type: 'reindex',
        id: reindexJob.value.id,
        collectionId: reindexJob.value.collection_id,
        status: reindexJob.value.status,
        progress: reindexJob.value.total_documents > 0
          ? Math.round((reindexJob.value.processed_documents / reindexJob.value.total_documents) * 100)
          : 0,
        currentFile: reindexJob.value.current_file,
        totalFiles: reindexJob.value.total_documents,
        processedFiles: reindexJob.value.processed_documents,
        error: reindexJob.value.error,
        startedAt: reindexJob.value.started_at,
        completedAt: reindexJob.value.completed_at,
        failedFiles: [],
        skippedFiles: [],
        cancellable: false,
      })
    }

    // Newest first: the job someone just started is the one they are
    // looking for, and it used to land at the bottom of the list.
    return jobs.sort((a, b) => {
      const activeDelta = Number(isActiveStatus(b.status)) - Number(isActiveStatus(a.status))
      if (activeDelta !== 0) return activeDelta
      return String(b.startedAt || '').localeCompare(String(a.startedAt || ''))
    })
  })

  const finishedJobs = computed(() => allJobs.value.filter(j => !isActiveStatus(j.status)))

  // Live-refresh signal: while a job is indexing, bump dataRefreshTick so
  // the sources sidebar and header/footer stats can update mid-job instead
  // of waiting for completion. Throttled to at most every ~10s, or sooner
  // once ~200 more files have been processed since the last signal.
  const REFRESH_INTERVAL_MS = 10000
  const REFRESH_FILE_STEP = 200
  const dataRefreshTick = ref(0)
  const dataRefreshCollectionId = ref(null)
  const _refreshMarks = {} // job key -> { at, processed }

  function maybeSignalDataRefresh(jobKey, status, collectionId, processedFiles) {
    if (!isActiveStatus(status)) {
      delete _refreshMarks[jobKey]
      return
    }
    const processed = processedFiles || 0
    const mark = _refreshMarks[jobKey]
    if (!mark) {
      // First progress event starts the throttle clock; no refresh yet.
      _refreshMarks[jobKey] = { at: Date.now(), processed }
      return
    }
    const now = Date.now()
    if (now - mark.at >= REFRESH_INTERVAL_MS || processed - mark.processed >= REFRESH_FILE_STEP) {
      mark.at = now
      mark.processed = processed
      dataRefreshCollectionId.value = collectionId
      dataRefreshTick.value++
    }
  }

  // Shape one server job row into the record this store keeps.
  function toJobRecord(jobData) {
    return {
      jobId: jobData.job_id,
      collectionId: jobData.collection_id,
      status: jobData.status,
      totalFiles: jobData.total_files,
      processedFiles: jobData.processed_files,
      currentFile: jobData.current_file,
      progressPercent: jobData.progress_percent,
      error: jobData.error,
      resultSummary: jobData.result_summary,
      startedAt: jobData.started_at,
      completedAt: jobData.completed_at,
      phase: jobData.phase,
      phaseProgress: jobData.phase_progress,
      phaseDetail: jobData.phase_detail,
      chunksProcessed: jobData.chunks_processed,
      chunksTotal: jobData.chunks_total,
      jobType: jobData.job_type || 'upload',
      queuePosition: jobData.queue_position ?? null,
      cancelRequested: jobData.cancel_requested || false,
      skippedFiles: jobData.skipped_files || [],
      // Set once the sidebar has reloaded its list for this job's finish.
      reloaded: false,
      // announced: the outcome toast has been shown (or deliberately
      // suppressed for history restored at startup).
      // finalized: the final row has been fetched, so the failed-file list
      // is in hand. Two flags, not one: a job restored while still running
      // must not toast its start, but must still report how it ended.
      announced: false,
      finalized: false,
    }
  }

  // Actions
  function addUploadJob(jobData) {
    const record = toJobRecord(jobData)
    dismissedJobIds.value.delete(record.jobId)
    uploadJobs.value = uploadJobs.value.filter(j => j.jobId !== record.jobId)
    uploadJobs.value.push(record)

    if (isActiveStatus(record.status)) {
      startSSEStream(record.jobId)
      // Polling as a fallback: SSE may not connect (proxies that buffer
      // event streams), and a job can finish before the stream is up.
      startPolling()
    }
    return record
  }

  function updateUploadJob(jobData) {
    const job = uploadJobs.value.find(j => j.jobId === jobData.job_id)
    if (!job) return
    const previousStatus = job.status
    Object.assign(job, {
      status: jobData.status,
      processedFiles: jobData.processed_files,
      currentFile: jobData.current_file,
      progressPercent: jobData.progress_percent,
      error: jobData.error,
      resultSummary: jobData.result_summary ?? job.resultSummary,
      completedAt: jobData.completed_at,
    })
    if (jobData.phase !== undefined) job.phase = jobData.phase
    if (jobData.phase_progress !== undefined) job.phaseProgress = jobData.phase_progress
    if (jobData.phase_detail !== undefined) job.phaseDetail = jobData.phase_detail
    if (jobData.chunks_processed !== undefined) job.chunksProcessed = jobData.chunks_processed
    if (jobData.chunks_total !== undefined) job.chunksTotal = jobData.chunks_total
    if (jobData.job_type !== undefined) job.jobType = jobData.job_type
    if (jobData.queue_position !== undefined) job.queuePosition = jobData.queue_position
    if (jobData.cancel_requested !== undefined) job.cancelRequested = jobData.cancel_requested

    maybeSignalDataRefresh(job.jobId, job.status, job.collectionId, job.processedFiles)
    if (isActiveStatus(previousStatus) && !isActiveStatus(job.status)) onJobFinished(job)
  }

  function updateFromSSEEvent(jobId, eventData) {
    const job = uploadJobs.value.find(j => j.jobId === jobId)
    if (!job) return
    const previousStatus = job.status

    if (eventData.phase) job.phase = eventData.phase
    if (eventData.phase_progress !== undefined) job.phaseProgress = eventData.phase_progress
    if (eventData.phase_detail) job.phaseDetail = eventData.phase_detail
    if (eventData.current_file) job.currentFile = eventData.current_file
    if (eventData.total_files !== undefined && eventData.total_files > 0) {
      job.totalFiles = eventData.total_files
    }
    if (eventData.chunks_processed !== undefined) job.chunksProcessed = eventData.chunks_processed
    if (eventData.chunks_total !== undefined) job.chunksTotal = eventData.chunks_total
    if (eventData.overall_percent !== undefined) job.progressPercent = eventData.overall_percent
    if (eventData.error) job.error = eventData.error

    // file_index is the 1-based index of the file being worked on, so it
    // only counts as processed once that file resolves. Treating a
    // file_start as progress made the count run one ahead of reality.
    if (eventData.file_index !== undefined &&
        (eventData.event_type === 'file_complete' || eventData.event_type === 'file_error')) {
      job.processedFiles = eventData.file_index
    }

    switch (eventData.event_type) {
      case 'job_complete':
        job.status = 'completed'
        job.progressPercent = 100
        job.currentFile = null
        break
      case 'job_error':
        job.status = 'failed'
        job.currentFile = null
        break
      case 'job_cancelled':
        job.status = 'cancelled'
        job.currentFile = null
        break
      case 'job_cancelling':
        job.cancelRequested = true
        break
      case 'file_start':
      case 'file_complete':
      case 'file_error':
      case 'phase_progress':
        job.status = 'running'
        job.queuePosition = null
        break
      default:
        break
    }

    maybeSignalDataRefresh(job.jobId, job.status, job.collectionId, job.processedFiles)
    if (isActiveStatus(previousStatus) && !isActiveStatus(job.status)) onJobFinished(job)
  }

  // When a job ends, pull its final row once: the SSE terminal event
  // carries no result summary, and the summary is where the failed files
  // are. Without this the drawer could only say "failed", never which.
  async function onJobFinished(job) {
    if (job.finalized) return
    job.finalized = true
    try {
      const response = await http.get(`/documents/upload/${job.jobId}/status`)
      const fresh = response.data
      job.resultSummary = fresh.result_summary ?? job.resultSummary
      job.processedFiles = fresh.processed_files
      job.totalFiles = fresh.total_files
      job.progressPercent = fresh.progress_percent
      job.error = fresh.error
      job.completedAt = fresh.completed_at
      job.status = fresh.status
      job.currentFile = null
      job.queuePosition = null
      job.cancelRequested = false
    } catch {
      // The summary is a nicety; the terminal status already landed.
    }
    if (!job.announced) {
      job.announced = true
      announceOutcome(job)
    }
  }

  // One toast per finished job. Files failing inside an otherwise
  // successful job was the quietest failure in the app: the job went
  // green and the sources were simply missing.
  function announceOutcome(job) {
    const ui = useUiStore()
    const failed = failedFilesOf(job)
    const noun = job.jobType === 'upload' ? 'Upload' : 'Indexing'
    if (job.status === 'failed') {
      ui.notify(`${noun} failed: ${job.error || 'unknown error'}`, 'error', { duration: 8000 })
      return
    }
    if (job.status === 'cancelled') {
      ui.notify(job.error || `${noun} cancelled`, 'warning')
      return
    }
    if (job.status !== 'completed') return
    const indexed = job.resultSummary?.documents_processed ?? job.processedFiles ?? 0
    if (failed.length > 0) {
      ui.notify(
        `Indexed ${indexed} file${indexed === 1 ? '' : 's'}, ` +
        `${failed.length} could not be read — open Background jobs for the list.`,
        'warning',
        { duration: 9000 },
      )
    } else {
      ui.notify(`Indexed ${indexed} file${indexed === 1 ? '' : 's'}`, 'success')
    }
  }

  function startSSEStream(jobId) {
    if (eventSources.value[jobId]) return // no duplicate connections

    const eventSource = new EventSource(`/documents/upload/${jobId}/stream`)
    const onEvent = (e) => {
      try {
        updateFromSSEEvent(jobId, JSON.parse(e.data))
      } catch {
        // A malformed frame is not worth tearing the stream down for.
      }
    }
    const onTerminal = (e) => {
      onEvent(e)
      closeSSEStream(jobId)
      stopPollingIfNoActiveJobs()
    }

    for (const name of ['connected', 'file_start', 'phase_progress', 'file_complete',
                        'file_error', 'job_cancelling']) {
      eventSource.addEventListener(name, onEvent)
    }
    for (const name of ['job_complete', 'job_error', 'job_cancelled']) {
      eventSource.addEventListener(name, onTerminal)
    }

    eventSource.onerror = () => {
      // Includes normal end-of-stream. Close and let polling carry on;
      // polling stops by itself once nothing is active.
      closeSSEStream(jobId)
      if (hasActiveJobs.value) startPolling()
    }

    eventSources.value[jobId] = eventSource
  }

  function closeSSEStream(jobId) {
    if (eventSources.value[jobId]) {
      eventSources.value[jobId].close()
      delete eventSources.value[jobId]
    }
  }

  function closeAllSSEStreams() {
    Object.keys(eventSources.value).forEach(jobId => closeSSEStream(jobId))
  }

  function stopPollingIfNoActiveJobs() {
    if (!hasActiveJobs.value) stopPolling()
  }

  function removeUploadJob(jobId) {
    closeSSEStream(jobId)
    dismissedJobIds.value.add(jobId)
    uploadJobs.value = uploadJobs.value.filter(j => j.jobId !== jobId)
    stopPollingIfNoActiveJobs()
  }

  // Clear the finished jobs out of the drawer (client-side only; the
  // server keeps its history).
  function clearFinishedJobs() {
    uploadJobs.value = uploadJobs.value.filter(job => {
      if (isActiveStatus(job.status)) return true
      closeSSEStream(job.jobId)
      dismissedJobIds.value.add(job.jobId)
      return false
    })
    if (reindexJob.value && !isActiveStatus(reindexJob.value.status)) {
      clearReindexJob()
    }
  }

  async function cancelUploadJob(jobId) {
    const job = uploadJobs.value.find(j => j.jobId === jobId)
    // Reflect the request straight away: a running job stops at the next
    // file boundary, which can be a minute on a large PDF, and a button
    // that looks inert gets pressed again.
    if (job) job.cancelRequested = true
    try {
      const response = await http.post(`/documents/upload/${jobId}/cancel`)
      if (response.data.status === 'cancelled' && job) {
        job.status = 'cancelled'
        job.error = job.error || response.data.message
        job.currentFile = null
        job.queuePosition = null
        closeSSEStream(jobId)
        onJobFinished(job)
        stopPollingIfNoActiveJobs()
      }
      return response.data
    } catch (err) {
      if (job) job.cancelRequested = false
      throw err
    }
  }

  function setReindexJob(jobData) {
    reindexJob.value = jobData
    if (jobData && isActiveStatus(jobData.status)) startPolling()
  }

  function clearReindexJob() {
    reindexJob.value = null
    stopPollingIfNoActiveJobs()
  }

  async function pollAllJobs() {
    // Poll upload jobs (fallback when SSE is not available)
    for (const job of uploadJobs.value) {
      if (!isActiveStatus(job.status)) continue
      if (eventSources.value[job.jobId]) continue // SSE has this one
      try {
        const response = await http.get(`/documents/upload/${job.jobId}/status`)
        updateUploadJob(response.data)
      } catch (err) {
        // A job row that has gone missing is not coming back; drop it
        // rather than polling a 404 forever.
        if (err?.status === 404) removeUploadJob(job.jobId)
      }
    }

    // Poll reindex job
    if (reindexJob.value && isActiveStatus(reindexJob.value.status)) {
      try {
        const response = await http.get('/api/reindex/status')
        reindexJob.value = response.data
        if (reindexJob.value) {
          maybeSignalDataRefresh(
            'reindex',
            reindexJob.value.status,
            reindexJob.value.collection_id,
            reindexJob.value.processed_documents,
          )
        }
      } catch (err) {
        if (err?.status === 404) reindexJob.value = null
      }
    }

    stopPollingIfNoActiveJobs()
  }

  function startPolling() {
    if (pollingInterval.value) return // Already polling
    pollingInterval.value = setInterval(pollAllJobs, 1500)
  }

  function stopPolling() {
    if (pollingInterval.value) {
      clearInterval(pollingInterval.value)
      pollingInterval.value = null
    }
  }

  // Restore state on startup: jobs still running (they live in server
  // threads and survive the tab), plus recent finished ones so their
  // outcomes are still readable after a refresh.
  async function checkActiveJobs() {
    try {
      const reindexResponse = await http.get('/api/reindex/status')
      if (reindexResponse.data && isActiveStatus(reindexResponse.data.status)) {
        setReindexJob(reindexResponse.data)
      }
    } catch {
      // No active reindex job
    }

    try {
      const response = await http.get('/documents/jobs', { params: { limit: 15 } })
      for (const job of response.data || []) {
        if (dismissedJobIds.value.has(job.job_id)) continue
        if (uploadJobs.value.some(j => j.jobId === job.job_id)) continue
        const record = addUploadJob(job)
        // History restored at startup is already known, so it does not
        // toast. A job restored while still running keeps its flags clear:
        // when it ends, this tab reports the outcome like any other.
        if (!isActiveStatus(job.status)) {
          record.announced = true
          record.finalized = true
        }
      }
    } catch {
      // History is a nicety — fall back to active jobs alone.
      try {
        const active = await http.get('/documents/upload/active')
        for (const job of active.data || []) {
          if (uploadJobs.value.some(j => j.jobId === job.job_id)) continue
          addUploadJob(job)
        }
      } catch {
        // Nothing to restore.
      }
    }
  }

  // Cleanup on unmount
  function cleanup() {
    stopPolling()
    closeAllSSEStreams()
  }

  // Public method to refresh jobs from the server (e.g., after starting a new job)
  async function refreshJobs() {
    await checkActiveJobs()
  }

  return {
    // State
    uploadJobs,
    reindexJob,

    // Computed
    hasActiveJobs,
    activeJobCount,
    allJobs,
    finishedJobs,

    // Helpers
    isActiveStatus,

    // Live-refresh signal (throttled; watched by App and SourcesSidebar)
    dataRefreshTick,
    dataRefreshCollectionId,

    // Actions
    addUploadJob,
    updateUploadJob,
    removeUploadJob,
    clearFinishedJobs,
    cancelUploadJob,
    setReindexJob,
    clearReindexJob,
    pollAllJobs,
    startPolling,
    stopPolling,
    checkActiveJobs,
    refreshJobs,
    cleanup,
    startSSEStream,
    closeSSEStream,
  }
})
