// How a review reads: its one-line summary, what was verified, what could not
// be, and how much of the source it read. Plain functions so the Reviews view
// and the source panel say the same thing.

const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`

/** The "In one line" statement, if the review has one. */
export function reviewSummary(review) {
  return review?.sections?.find(s => s.key === 'tldr')?.items?.[0] || null
}

/** Every verified statement, counted across sections. */
export function verifiedCount(review) {
  return (review?.sections || []).reduce((n, s) => n + (s.items?.length || 0), 0)
}

/** What a list row says at a glance: verified, left out, or nothing checked. */
export function reviewStatus(review) {
  const verified = verifiedCount(review)
  const rejected = Number(review?.rejected) || 0
  if (!verified) {
    return { tone: 'warning', verified, rejected, label: 'Nothing could be verified' }
  }
  const parts = [`${plural(verified, 'statement')} verified`]
  if (rejected) parts.push(`${rejected} left out`)
  return { tone: rejected ? 'mixed' : 'ok', verified, rejected, label: parts.join(' · ') }
}

/** Sections with statements, the one-line summary excluded (it leads). */
export function bodySections(review) {
  return (review?.sections || []).filter(s => s.key !== 'tldr' && s.items?.length)
}

/** Section titles the passages read did not cover. */
export function uncovered(review) {
  return (review?.sections || []).filter(s => s.key !== 'tldr' && !s.items?.length).map(s => s.title)
}

export function pageRanges(pages = []) {
  if (!pages.length) return ''
  const out = []
  let start = pages[0]
  let prev = pages[0]
  for (const p of pages.slice(1).concat([null])) {
    if (p === prev + 1) { prev = p; continue }
    out.push(start === prev ? `${start}` : `${start}–${prev}`)
    start = prev = p
  }
  return out.join(', ')
}

/** "Read 3 of 6 passages (62% of the text), pages 1–2". */
export function coverageText(review) {
  const c = review?.coverage || {}
  if (!c.of) return ''
  const share = c.share ? ` (${Math.round(c.share * 100)}% of the text)` : ''
  const pages = c.pages?.length ? `, ${c.pages.length === 1 ? 'page' : 'pages'} ${pageRanges(c.pages)}` : ''
  return `Read ${c.passages} of ${c.of} passages${share}${pages}`
}

/** The server's notes, minus the ones the view already says in its own words. */
export function otherNotes(review) {
  return (review?.coverage?.notes || []).filter(n => !/were removed because their quote|could be verified against the source/.test(n))
}

/** Unread first, then newest: the morning queue. Stable for equal keys. */
export function reviewQueue(reviews = []) {
  return reviews.map((r, i) => [r, i]).sort((a, b) => {
    const ua = a[0].read_at ? 1 : 0
    const ub = b[0].read_at ? 1 : 0
    if (ua !== ub) return ua - ub
    const t = String(b[0].created_at || '').localeCompare(String(a[0].created_at || ''))
    return t || a[1] - b[1]
  }).map(([r]) => r)
}

export function depthLabel(depth) {
  return depth === 'thorough' ? 'Thorough review' : 'Brief review'
}
