// C7 (roadmap U4): one word per concept in everything a person reads. The
// glossary is in docs/ENGINEERING.md; this test reads every component's
// template (its text and its aria-label, title, placeholder and label
// attributes) and fails on a word the glossary retired.
import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const SRC = join(__dirname, '..', '..')

// Retired word → the word to use (the message a failure prints).
const BANNED = [
  [/\bchat tab\b/i, 'the Ask tab'],
  [/\bchat session\b/i, 'chat'],
  [/\bsearch sources\b/i, 'Find'],
  [/\bnew search\b/i, 'Start over'],
  [/\bartifacts?\b/i, 'report'],
  [/\bregistry\b/i, 'catalogue'],
  [/\bchunks?\b/i, 'passage'],
  [/\bindexed text\b/i, 'passages'],
  [/\bMCP server\b/i, 'Connect, or agent access (MCP)'],
  [/\bdocs\b/i, 'sources (or Documentation for a manual)'],
  [/\bdocuments?\b/i, 'sources'],
  [/\bexpertise\b/i, 'Saved instructions'],
]

// A word that means something else here, with the reason.
const ALLOWED = [
  // A custom file type's kind: documents as a format family beside data and code.
  ['components/CustomFileTypes.vue', 'Documents'],
]

function vueFiles(dir) {
  const out = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === '__tests__' || entry.name === 'node_modules') continue
    const path = join(dir, entry.name)
    if (entry.isDirectory()) out.push(...vueFiles(path))
    else if (entry.name.endsWith('.vue')) out.push(path)
  }
  return out
}

/** What a person reads in a template: text between tags and label attributes. */
function readable(source) {
  const start = source.indexOf('<template>')
  const end = source.lastIndexOf('</template>')
  if (start < 0 || end < 0) return []
  const template = source.slice(start, end).replace(/<!--[\s\S]*?-->/g, '')
  const out = []
  for (const m of template.matchAll(/>([^<>]+)</g)) {
    const text = m[1].replace(/\{\{[\s\S]*?\}\}/g, ' ').trim()
    if (text) out.push(text)
  }
  for (const m of template.matchAll(/\s(?:aria-label|title|placeholder|label)="([^"]*)"/g)) {
    out.push(m[1])
  }
  // Bound attributes holding a literal: :aria-label="'…'" or a template string.
  for (const m of template.matchAll(/\s:(?:aria-label|title|placeholder|label)="([^"]*)"/g)) {
    for (const lit of m[1].matchAll(/'([^']*)'|`([^`]*)`/g)) out.push((lit[1] ?? lit[2]).replace(/\$\{[^}]*\}/g, ' '))
  }
  return out
}

describe('one vocabulary (C7, U4)', () => {
  it('no component template uses a retired word', () => {
    const problems = []
    for (const file of vueFiles(join(SRC, 'components')).concat([join(SRC, 'App.vue')])) {
      const rel = file.slice(SRC.length + 1).replaceAll('\\', '/')
      for (const text of readable(readFileSync(file, 'utf8'))) {
        for (const [pattern, use] of BANNED) {
          const hit = text.match(pattern)
          if (!hit) continue
          if (ALLOWED.some(([f, word]) => f === rel && hit[0] === word)) continue
          problems.push(`${rel}: "${text.slice(0, 80)}" — use ${use}`)
        }
      }
    }
    expect(problems).toEqual([])
  })
})
