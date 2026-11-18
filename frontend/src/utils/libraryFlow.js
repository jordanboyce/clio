// The Library's order of work (ROADMAP R10): Analyzed → Screened → Reviewed
// → Cite & check. Each step says how far the scope has got, what it needs and
// what it costs, before anyone presses a button.
//
// Model calls are counted as the server makes them: screening is one call per
// source; a brief review one per source; a thorough one reads a long text in
// parts, up to THOROUGH_MAX_PARTS calls (services/research/briefs.py).

export const THOROUGH_MAX_PARTS = 8

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

function stateOf(done, total) {
  if (!total) return 'todo'
  if (done >= total) return 'done'
  return done > 0 ? 'partial' : 'todo'
}

/**
 * papers: the sources in scope (the selection, or the whole collection).
 * screened: ids with a verdict for `question` (ignored without a question).
 */
export function librarySteps({ papers = [], question = '', screened = [], hasModel = false, canWrite = false } = {}) {
  const total = papers.length
  const analyzed = papers.filter(p => p.analyzed).length
  const reviewed = papers.filter(p => (p.review_count || 0) > 0).length
  const q = String(question || '').trim()
  const screenedSet = new Set(screened)
  const screenedCount = q ? papers.filter(p => screenedSet.has(p.document_id)).length : 0
  const toScreen = total - screenedCount
  const toAnalyze = total - analyzed

  return [
    {
      id: 'analyze', label: 'Analyzed', done: analyzed, total, state: stateOf(analyzed, total),
      hint: toAnalyze ? 'Reads title, authors and references · no model' : 'No model needed',
      action: canWrite && toAnalyze ? `Analyze ${toAnalyze}` : '',
    },
    {
      id: 'screen', label: q ? 'Screened' : 'Screen', done: q ? screenedCount : null, total,
      state: q ? stateOf(screenedCount, total) : 'todo',
      hint: !hasModel ? 'Needs a model'
        : !q ? 'Ask a question below · 1 model call per source'
          : toScreen ? `${plural(toScreen, 'model call')} for the rest`
            : `for “${q}”`,
      question: q,
    },
    {
      id: 'review', label: 'Reviewed', done: reviewed, total, state: stateOf(reviewed, total),
      hint: hasModel ? `Brief: 1 call per source · thorough: up to ${THOROUGH_MAX_PARTS}` : 'Needs a model',
    },
    {
      id: 'cite', label: 'Cite & check', done: null, total, state: 'todo',
      hint: 'Citations and draft checks · no model',
    },
  ]
}

/** "about 6 model calls" for a review run, before it starts. */
export function reviewCost(count, depth) {
  if (!count) return ''
  if (depth === 'thorough') return `up to ${plural(count * THOROUGH_MAX_PARTS, 'model call')}`
  return plural(count, 'model call')
}
