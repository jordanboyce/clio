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

// Page-level injection evidence from the stored scan, strongest first.
export function injectionEvidence(injection) {
  return (injection?.top || []).map((t) => ({
    ...t,
    category: categoryLabel(t.category),
  }))
}

// One line for the bell and footer: "2 held · 3 injection · 1 reported".
export function reviewSummaryText(summary, pendingRegistrations = 0) {
  const s = summary || {}
  const parts = []
  if (s.held) parts.push(`${s.held} held`)
  if (s.flagged) parts.push(`${s.flagged} flagged`)
  if (s.injection) parts.push(`${s.injection} injection`)
  if (s.reports) parts.push(`${s.reports} reported`)
  if (pendingRegistrations) parts.push(`${pendingRegistrations} access request${pendingRegistrations === 1 ? '' : 's'}`)
  return parts.join(' · ')
}
