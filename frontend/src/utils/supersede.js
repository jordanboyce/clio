// Presentation for "replaced by" relationships and folded duplicate passages.
// One place, so the source list, search results and chat evidence word them
// the same way.

// The quiet badge on a source row. Null when nothing replaced it.
export function replacedBadge(doc) {
  if (!doc || !doc.superseded_by) return null
  const name = doc.superseded_by_filename || 'a newer document'
  const note = doc.superseded_note ? ` Note: ${doc.superseded_note}` : ''
  return {
    text: 'Replaced',
    name,
    title: `Replaced by ${name}. Search ranks it below the current version but still finds it.${note}`,
  }
}

// What a search result or chat source says about itself. Each field is null
// when it does not apply, so templates can v-if on it.
export function supersedeNotes(source) {
  const replacement = source?.superseded_by
  const dups = Array.isArray(source?.duplicates) ? source.duplicates : []
  const count = Number(source?.duplicate_count) || dups.length
  const names = [...new Set(dups.map(d => d.filename).filter(Boolean))]
  return {
    older: replacement ? `Older version — replaced by ${replacement.filename || 'a newer document'}` : null,
    also: count > 0
      ? `Also in ${count} other ${count === 1 ? 'file' : 'files'}`
      : null,
    alsoTitle: names.length ? names.join(', ') + (count > names.length ? ` and ${count - names.length} more` : '') : null,
  }
}

const REASON_TEXT = {
  version_in_name: 'The names differ by a version, year or revision.',
  copy_of: 'Looks like a downloaded copy of the other file.',
  same_text: 'The text is identical to the other file.',
}

export function suggestionReason(suggestion) {
  return REASON_TEXT[suggestion?.reason] || 'Looks like an older version.'
}

// Key for remembering a dismissed suggestion on this browser.
export function suggestionKey(suggestion) {
  return `${suggestion.document_id}>${suggestion.replaced_by}`
}
