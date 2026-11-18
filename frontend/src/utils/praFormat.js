// How the PRA viewer writes numbers, gates, sequences and the reader's
// caveats (docs/PRA.md, docs/PRA_ANALYSIS.md). Analysts read probabilities
// in scientific notation, the way SAPHIRE and SCRAM print them, so every
// probability, frequency and Birnbaum value goes through sci(); ratios that
// read as "times" (RAW, RRW) stay plain until they grow large.

const EM_DASH = '—'

export function isPraDoc(doc) {
  return String(doc?.source_format || '').toLowerCase().startsWith('pra-')
}

// 1.57e-5 → "1.57E-5". Null and non-numbers are "—": a missing value is
// shown as missing, never as 0.
export function sci(value, digits = 3) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return EM_DASH
  const x = Number(value)
  if (x === 0) return '0'
  const [mantissa, exp] = x.toExponential(Math.max(0, digits - 1)).split('e')
  return `${mantissa}E${exp.replace('+', '')}`
}

// A cutoff reads as written: 1E-13, 2.5E-12.
export function cutoff(value) {
  const text = sci(value)
  return text.replace(/\.?0+E/, 'E')
}

// A ratio (RAW, RRW): three significant figures, scientific past 10,000.
export function ratio(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return EM_DASH
  const x = Number(value)
  if (Math.abs(x) >= 1e4 || (x !== 0 && Math.abs(x) < 1e-3)) return sci(x)
  return String(Number(x.toPrecision(3)))
}

// A share of a total as a percentage; the long tail reads "<0.1%".
export function percent(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return EM_DASH
  const pct = Number(value) * 100
  if (pct > 0 && pct < 0.1) return '<0.1%'
  return `${pct >= 10 ? pct.toFixed(0) : pct.toFixed(1)}%`
}

export function plural(n, one, many = `${one}s`) {
  return `${Number(n || 0).toLocaleString('en-US')} ${n === 1 ? one : many}`
}

// "AND", "OR", "3 of 4" (k-of-n with n the gate's inputs, loaded or not).
const GATE_LABELS = { and: 'AND', or: 'OR', not: 'NOT', xor: 'XOR', nand: 'NAND', nor: 'NOR', transfer: 'TRANSFER' }
export function gateLabel(node) {
  if (!node) return ''
  if (node.type === 'atleast') {
    const n = node.children ? node.children.length : node.input_count
    return n ? `${node.k ?? '?'} of ${n}` : `at least ${node.k ?? '?'}`
  }
  return GATE_LABELS[node.type] || String(node.type || 'gate').toUpperCase()
}

// What the gate means in words, for the type's tooltip and screen readers.
export function gateMeaning(node) {
  if (!node) return ''
  switch (node.type) {
    case 'and': return 'Fails if all of its inputs fail'
    case 'or': return 'Fails if any of its inputs fails'
    case 'atleast': return `Fails if at least ${node.k ?? '?'} of its ${node.children?.length ?? node.input_count ?? '?'} inputs fail`
    case 'not': return 'The complement of its input'
    case 'xor': return 'Fails if exactly one of its inputs fails'
    case 'nand': return 'NOT AND: fails unless all of its inputs fail'
    case 'nor': return 'NOT OR: fails only if none of its inputs fails'
    default: return 'A gate type Clio reads but does not analyse'
  }
}

const TOOLS = {
  'saphire-mard': { tool: 'SAPHIRE', format: 'SAPHIRE MAR-D export' },
  'open-psa-mef': { tool: 'the PRA tool', format: 'Open-PSA Model Exchange Format' },
}
export function modelTool(modelFormat, sourceFormat) {
  const known = TOOLS[modelFormat]
  if (known) return known
  if (String(sourceFormat || '').includes('mard')) return TOOLS['saphire-mard']
  if (String(sourceFormat || '').includes('mef')) return TOOLS['open-psa-mef']
  return { tool: 'the PRA tool', format: modelFormat || sourceFormat || 'PRA model' }
}

// The model's counts as one quiet line; zero kinds are left out.
const COUNT_WORDS = [
  ['fault_trees', 'fault tree'], ['gates', 'gate'], ['basic_events', 'basic event'], ['house_events', 'house event'],
  ['event_trees', 'event tree'], ['sequences', 'sequence'], ['cut_set_lists', 'stored cut set list'],
]
export function countsLine(counts) {
  if (!counts) return ''
  return COUNT_WORDS.filter(([key]) => counts[key]).map(([key, word]) => plural(counts[key], word)).join(' · ')
}

// ── cut set provenance and truncation, in plain words ───────────────────

// Server warnings the headline below already says in its own words.
const COVERED = [/^The [\d.]+ s time budget ran out/, /^Stopped after the [\d.]+ s time budget/, /^More than \d+ cut sets/]

// { headline, lowerBound, notes }: where the cut sets came from and how
// complete they are, then whatever else the server warned about.
export function cutSetProvenance(view) {
  if (!view) return { headline: '', lowerBound: false, notes: [] }
  const c = view.computation
  const notes = (view.warnings || []).filter(w => !COVERED.some(re => re.test(w)))
  if (view.source !== 'computed' || !c) {
    const cut = view.truncation ? `, truncated at ${cutoff(view.truncation)}` : ''
    return { headline: `From the model${cut}`, lowerBound: false, notes }
  }
  const order = c.max_order ? ` up to order ${c.max_order}` : ''
  const budget = `${c.time_budget_seconds ?? 20}\u00a0s`
  let headline
  let lowerBound = true
  if (!c.complete) {
    headline = `Computed by Clio, partial: the ${budget} time budget ran out before the first pass (cutoff ${cutoff(view.truncation)}) finished. The list and totals are lower bounds.`
  } else if (c.truncation_raised) {
    headline = `Computed by Clio. More than ${Number(c.max_cut_sets).toLocaleString('en-US')} cut sets, so the cutoff was raised to ${cutoff(view.truncation)} to keep the most probable${order}. Totals are lower bounds.`
  } else if (c.stopped) {
    headline = `Computed by Clio, complete down to ${cutoff(view.truncation)}${order}; the ${budget} time budget stopped it short of the ${cutoff(c.requested_truncation)} asked for. Totals are lower bounds.`
  } else {
    headline = `Computed by Clio: every minimal cut set at or above ${cutoff(view.truncation)}${order}.`
    lowerBound = false
  }
  return { headline, lowerBound, notes }
}

// ── event tree sequences ────────────────────────────────────────────────

const SUCCESS = /^(success|s|yes|y|true|up|ok|works)$/i
const FAILURE = /^(failure|f|fail|failed|no|n|false|down)$/i

// One mark per functional event, in column order: success, failure, a
// branch label the tool used ("other"), or not asked on this path ("skip").
// Path steps naming an event outside the columns (a substituted top) come
// after them, so nothing on the path is dropped.
export function sequenceMarks(functionalEvents, path) {
  const names = (functionalEvents || []).map(fe => (typeof fe === 'string' ? fe : fe.name))
  const byName = new Map()
  for (const step of path || []) if (!byName.has(step.functional_event)) byName.set(step.functional_event, step.state)
  const mark = (name, state) => {
    if (state === undefined) return { name, state: 'skip', label: 'not asked' }
    if (SUCCESS.test(state)) return { name, state: 'success', label: 'success' }
    if (FAILURE.test(state)) return { name, state: 'failure', label: 'failure' }
    return { name, state: 'other', label: String(state) }
  }
  const marks = names.map(name => mark(name, byName.get(name)))
  const known = new Set(names)
  for (const [name, state] of byName) if (!known.has(name)) marks.push({ ...mark(name, state), extra: true })
  return marks
}

// The path read aloud: "RPS-FT success, AFW-FT failure".
export function sequencePathText(marks) {
  return (marks || []).filter(m => m.state !== 'skip').map(m => `${m.name} ${m.label}`).join(', ')
}

// Where a sequence goes: its end state, or the event tree it transfers to.
export function sequenceOutcome(seq) {
  if (seq?.transfer) return { kind: 'transfer', label: seq.transfer }
  if (seq?.end_state) return { kind: 'end_state', label: seq.end_state }
  return { kind: 'none', label: '' }
}

// ── importance ──────────────────────────────────────────────────────────

export const IMPORTANCE_COLUMNS = [
  { id: 'fv', field: 'fussell_vesely', label: 'FV', name: 'Fussell-Vesely', format: ratio,
    hint: 'The fraction of the total that involves this event: (F − F[p=0]) / F.' },
  { id: 'raw', field: 'raw', label: 'RAW', name: 'Risk achievement worth', format: ratio,
    hint: 'How many times the total rises if the event is certain: F[p=1] / F.' },
  { id: 'rrw', field: 'rrw', label: 'RRW', name: 'Risk reduction worth', format: ratio,
    hint: 'How many times the total falls if the event never occurs: F / F[p=0]. Unbounded (∞) when the event is in every cut set.' },
  { id: 'birnbaum', field: 'birnbaum', label: 'Birnbaum', name: 'Birnbaum', format: sci,
    hint: 'The sensitivity of the total to the event\'s probability: F[p=1] − F[p=0].' },
]

// RRW is null both when unknown and when unbounded; FV of 1 tells them apart.
export function rrwText(row) {
  if (row?.rrw === null && row?.fussell_vesely !== null && row?.fussell_vesely >= 1 - 1e-12) return '∞'
  return ratio(row?.rrw)
}

// ── where a citation points ─────────────────────────────────────────────

// A cited passage of a PRA source names the tree it is from (services/pra/
// text.py writes "# Fault tree X", "Gate G (OR) in fault tree X", "Sequence S
// of event tree E"). Open the viewer there; anything else opens the overview.
export function focusFromPassage(text) {
  const s = String(text || '')
  let m = s.match(/^#\s*Fault tree (\S+)/m) || s.match(/\bin fault tree (\S+?)(?=\s|—|$)/)
  if (m) return { view: 'fault-trees', name: m[1] }
  m = s.match(/^#\s*Event tree (\S+)/m) || s.match(/\bSequence \S+ of event tree (\S+?)(?=[\s.]|$)/)
  if (m) return { view: 'event-trees', name: m[1].replace(/\.$/, '') }
  return null
}

// The link that opens a PRA source in the viewer, in this window or a new
// one; App.vue reads it back on load.
export function praHref({ documentId, collectionId, focus = null }) {
  const params = new URLSearchParams({ pra: documentId, collection_id: collectionId || 'default' })
  if (focus?.name) params.set(focus.view === 'event-trees' ? 'et' : 'ft', focus.name)
  return `/?${params.toString()}`
}

// The viewer target for a cited passage or search hit of a PRA source, or
// null for any other source. The collection is the one the hit came from.
export function praTargetFor(source) {
  if (!isPraDoc(source) || !source?.document_id) return null
  let collectionId = source.collection_id || ''
  if (!collectionId) {
    try {
      collectionId = new URL(source.page_url || source.pdf_url || '', window.location.origin).searchParams.get('collection_id') || ''
    } catch { collectionId = '' }
  }
  return {
    documentId: source.document_id,
    collectionId: collectionId || 'default',
    filename: source.filename || '',
    focus: focusFromPassage(source.text_snippet),
  }
}

// A click that should open the viewer here rather than follow the link
// (a modified click still opens a new tab, as links do).
export function isPlainClick(event) {
  return !(event?.metaKey || event?.ctrlKey || event?.shiftKey || event?.altKey || event?.button > 0)
}

export function parsePraHref(search) {
  const params = new URLSearchParams(search || '')
  const documentId = params.get('pra')
  if (!documentId) return null
  const ft = params.get('ft')
  const et = params.get('et')
  return {
    documentId,
    collectionId: params.get('collection_id') || 'default',
    focus: ft ? { view: 'fault-trees', name: ft } : et ? { view: 'event-trees', name: et } : null,
  }
}
