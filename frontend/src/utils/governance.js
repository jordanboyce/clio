// Shared presentation for governance state — one place so the sidebar,
// search results, collection cards, and the admin console agree on what a
// label or a policy status looks like. DaisyUI semantic tokens only.

export const SENSITIVITY_LEVELS = ['public', 'internal', 'confidential', 'restricted']

export const SENSITIVITY_HELP = {
  public: 'Fine for anyone to see.',
  internal: 'Default — for people on this deployment.',
  confidential: 'Handle with care; labelled in every result and citation.',
  restricted: 'Cannot be shared, and invisible to MCP clients unless a token is scoped to it.',
}

export function labelBadgeClass(label) {
  switch (label) {
    case 'public':
      return 'badge-ghost'
    case 'confidential':
      return 'badge-warning badge-outline'
    case 'restricted':
      return 'badge-error badge-outline'
    default:
      return 'badge-ghost'
  }
}

// Whether a label is worth a badge at all — "internal" is the default and
// would just be noise on every row.
export function showLabel(label) {
  return !!label && label !== 'internal'
}

export function policyBadge(status) {
  switch (status) {
    case 'quarantined':
      return { text: 'held', cls: 'badge-error', title: 'Held for review — hidden from search, chat and MCP until an administrator approves it.' }
    case 'flagged':
      return { text: 'flagged', cls: 'badge-warning', title: 'The content-policy scan flagged this document. It is still searchable.' }
    case 'approved':
      return { text: 'reviewed', cls: 'badge-ghost', title: 'Flagged by the scan and approved by an administrator.' }
    default:
      return null
  }
}

// Turn the stored policy_flags into rows the review UI can render.
export function policyFlagPages(flags) {
  if (!flags || !flags.flagged_pages) return []
  return Object.entries(flags.flagged_pages).map(([page, scan]) => ({ page: parseInt(page, 10), ...scan }))
}

export function categoryLabel(cat) {
  return String(cat || '').replace(/_/g, ' ')
}

// ── Review queue ───────────────────────────────────────────────────────────
// What the admin console and the notification bell both need to agree on.

export const PRIORITY_ORDER = ['critical', 'high', 'medium', 'low']

export function priorityBadge(priority) {
  switch (priority) {
    case 'critical':
      return { text: 'critical', cls: 'badge-error' }
    case 'high':
      return { text: 'high', cls: 'badge-error badge-outline' }
    case 'medium':
      return { text: 'medium', cls: 'badge-warning badge-outline' }
    default:
      return { text: 'low', cls: 'badge-ghost' }
  }
}

export const KIND_LABELS = {
  policy: 'Content scan',
  injection: 'Prompt injection',
  report: 'Reported',
}

// Filter chips for the queue. `held` and `flagged` both come from the content
// scan; they are separate chips because a hold hides the document and a flag
// does not, which changes how urgently a person wants to look.
export const REVIEW_FILTERS = [
  { id: 'all', label: 'All', match: () => true },
  { id: 'held', label: 'Held', match: (i) => i.policy_status === 'quarantined' },
  { id: 'flagged', label: 'Flagged', match: (i) => i.kinds?.includes('policy') && i.policy_status === 'flagged' },
  { id: 'injection', label: 'Injection', match: (i) => i.kinds?.includes('injection') },
  { id: 'report', label: 'Reported', match: (i) => i.kinds?.includes('report') },
]

export function filterReviewItems(items, filterId) {
  const f = REVIEW_FILTERS.find((x) => x.id === filterId) || REVIEW_FILTERS[0]
  return (items || []).filter(f.match)
}

const _SEV = { low: 1, medium: 2, high: 3 }

// How the source list should badge a document's injection scan. A reviewed
// warning and a low-confidence match must not look like an open incident —
// that is what made the list unusable.
export function injectionBadge(doc) {
  const warnings = doc?.injection_warnings
  const pages = warnings ? Object.keys(warnings).length : 0
  if (!pages) return null
  const where = `${pages} page${pages === 1 ? '' : 's'}`
  if (doc.injection_review) {
    return {
      text: 'reviewed',
      cls: 'badge-ghost',
      title: `Reviewed. The injection note on ${where} stays on the source and is not a task.`,
    }
  }
  let rank = 0
  for (const scan of Object.values(warnings)) {
    for (const f of scan?.findings || []) rank = Math.max(rank, _SEV[f.severity] || 0)
    if (!(scan?.findings || []).length && (scan?.risk_score || 0) >= 0.5) rank = Math.max(rank, 2)
  }
  if (rank >= 3) {
    return { text: 'injection', cls: 'badge-warning', title: `Text on ${where} looks like a prompt-injection attempt. Click for the excerpt.` }
  }
  if (rank >= 2) {
    return { text: 'injection', cls: 'badge-warning badge-outline', title: `Possible prompt-injection wording on ${where}. Click for the excerpt.` }
  }
  return { text: 'note', cls: 'badge-ghost', title: `Low-confidence wording on ${where}. Likely ordinary prose or a quoted example.` }
}

// Page-level injection evidence from the stored scan, strongest first.
export function injectionEvidence(injection) {
  return (injection?.top || []).map((t) => ({
    ...t,
    category: categoryLabel(t.category),
  }))
}

// One line for the bell and footer: "2 held · 3 injection · 1 reported".
export function reviewSummaryText(summary) {
  const s = summary || {}
  const parts = []
  if (s.held) parts.push(`${s.held} held`)
  if (s.flagged) parts.push(`${s.flagged} flagged`)
  if (s.injection) parts.push(`${s.injection} injection`)
  if (s.reports) parts.push(`${s.reports} reported`)
  return parts.join(' · ')
}
