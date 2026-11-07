// Words that carry no meaning of their own: marking every "for" and "the" in
// a passage hides the words that matched. Used only when the query has
// something else to mark.
const STOPWORDS = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'can', 'did', 'do', 'does', 'for', 'from',
  'had', 'has', 'have', 'how', 'if', 'in', 'into', 'is', 'it', 'its', 'not', 'of', 'on', 'or', 'our',
  'should', 'so', 'than', 'that', 'the', 'their', 'them', 'then', 'there', 'these', 'they', 'this',
  'those', 'to', 'was', 'we', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with',
  'would', 'you', 'your',
])

/** The words of a query worth marking in a passage. */
export function highlightTerms(query) {
  const words = String(query || '')
    .toLowerCase()
    // Quotes, question marks and other punctuation around a word are not part of it.
    .split(/[^\p{L}\p{N}'-]+/u)
    .map(word => word.replace(/^['-]+|['-]+$/g, ''))
    .filter(word => word.length > 2)
  const meaningful = words.filter(word => !STOPWORDS.has(word))
  return [...new Set(meaningful.length ? meaningful : words)]
}

// Escape document text before adding our own markup. One pass avoids matches
// inside HTML attributes or inside marks produced for earlier query terms.
// A term matches a whole word, or a word that adds a common ending to it
// ("return" marks "returned", "team" marks "teams"), never the middle of
// another word ("for" does not mark "before").
export function highlightSnippet(value, query) {
  const text = String(value)
  const escape = value => value.replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]))
  const words = highlightTerms(query)
  if (!words.length) return escape(text)
  const alternatives = words
    .sort((a, b) => b.length - a.length)
    .map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|')
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alternatives})(?:s|es|ed|d|ing|er|ers|'s)?(?![\\p{L}\\p{N}])`, 'giu')
  let html = '', end = 0
  for (const match of text.matchAll(pattern)) {
    html += escape(text.slice(end, match.index)) + `<mark class="bg-warning/25 text-base-content rounded-sm">${escape(match[0])}</mark>`
    end = match.index + match[0].length
  }
  return html + escape(text.slice(end))
}
