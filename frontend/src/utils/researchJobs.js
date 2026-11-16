// Library jobs (analyze, screen, review) as the server reports them, and
// what to tell the person when one ends.
//
// The server's states (services/research/jobs.py): queued and running are
// still going; done, done_with_errors, failed and cancelled are finished.
// A job row also carries `finished`, so a state this file does not know yet
// still reads as finished rather than as running forever.

export const ACTIVE_STATES = ['queued', 'running']

export const JOB_STATE_LABELS = {
  queued: 'Waiting',
  running: 'Running',
  done: 'Done',
  done_with_errors: 'Finished with errors',
  failed: 'Stopped',
  cancelled: 'Cancelled',
}

export const JOB_STATE_BADGES = {
  running: 'badge-info',
  done: 'badge-success',
  done_with_errors: 'badge-warning',
  failed: 'badge-error',
  cancelled: 'badge-ghost',
}

export function isActive(job) {
  if (!job) return false
  if (typeof job.finished === 'boolean') return !job.finished
  return ACTIVE_STATES.includes(job.status)
}

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`

// What a finished job (or the several jobs one request was split into)
// should tell the person: null for a clean finish, otherwise
// { message, type } for a toast. `missing` counts jobs that are no longer
// listed at all. Words: { noun: 'Analysis', past: 'analyzed', failed: 'could not be read' }.
export function finishNotice(jobs, words, { missing = 0 } = {}) {
  const list = (jobs || []).filter(Boolean)
  const total = list.reduce((n, j) => n + (j.total || 0), 0)
  const done = list.reduce((n, j) => n + (j.done || 0), 0)
  const failed = list.reduce((n, j) => n + (j.failed || 0), 0)
  const stopped = list.find(j => j.status === 'failed')
  const cancelled = list.some(j => j.status === 'cancelled')
  const notDone = Math.max(0, total - done - failed)
  // First line only: a provider's error can run on with a docs link.
  const firstError = String(list.flatMap(j => j.errors || []).find(e => e?.error)?.error || '').split(/\r?\n/)[0].trim() || undefined

  if (missing && !list.length) {
    return { message: `The ${words.noun.toLowerCase()} job is no longer listed, so how it ended is unknown. Reload to see what finished.`, type: 'warning' }
  }
  const parts = []
  if (failed) parts.push(`${failed} ${words.failed}`)
  if (notDone) parts.push(`${notDone} not done`)
  if (missing) parts.push(`${plural(missing, 'part')} no longer listed`)
  const tally = `${words.noun}: ${done} of ${plural(total, 'source')} ${words.past}`
    + (parts.length ? `; ${parts.join(', ')}` : '') + '.'

  if (cancelled && !stopped) return { message: `${words.noun} was cancelled. ${tally}`, type: 'warning' }
  if (stopped && (stopped.current || notDone)) {
    // A stop part-way: a budget running out, a restart, a crash.
    const why = stopped.current || firstError || 'the job stopped early'
    return { message: `${words.noun} stopped: ${why}. ${tally}`, type: done ? 'warning' : 'error' }
  }
  if (stopped && !done) {
    return { message: `${tally}${firstError ? ` First error: ${firstError}` : ''}`, type: 'error' }
  }
  if (failed || notDone || missing || list.some(j => j.status === 'done_with_errors')) {
    const extra = !failed && firstError ? ` ${firstError}` : ''
    return { message: `${words.noun} finished with errors. ${tally}${extra}`, type: 'warning' }
  }
  return null
}

// Polls the job list for a set of job ids until every one has finished.
// Each watch has its own timer, so starting a second job never orphans the
// first one's poll (and its spinner). `fetchJobs(context)` returns the job
// list; `context` (e.g. the collection the jobs belong to) is fixed per watch.
export function createJobPoller(fetchJobs, { interval = 2500 } = {}) {
  const watches = new Set()

  function watch(jobIds, { onProgress, onFinished, context } = {}) {
    const ids = [...jobIds]
    const entry = { timer: null, stopped: false }
    watches.add(entry)

    const tick = async () => {
      if (entry.stopped) return
      let jobs
      try {
        jobs = await fetchJobs(context)
      } catch {
        if (!entry.stopped) entry.timer = setTimeout(tick, interval)
        return
      }
      if (entry.stopped) return
      const mine = ids.map(id => (jobs || []).find(j => j.job_id === id))
      if (mine.some(isActive)) {
        onProgress?.(mine.filter(Boolean))
        entry.timer = setTimeout(tick, interval)
        return
      }
      watches.delete(entry)
      entry.stopped = true
      onFinished?.(mine.filter(Boolean), { missing: mine.filter(j => !j).length })
    }
    entry.timer = setTimeout(tick, interval)
    return () => { entry.stopped = true; clearTimeout(entry.timer); watches.delete(entry) }
  }

  function stopAll() {
    for (const entry of watches) { entry.stopped = true; clearTimeout(entry.timer) }
    watches.clear()
  }

  return { watch, stopAll, get active() { return watches.size } }
}
