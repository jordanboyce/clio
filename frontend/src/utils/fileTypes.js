// What a source is, at a glance: the family it belongs to (code, docs, data,
// media, web), a short label for the tile, and the icon. The server owns the
// authoritative family boundaries (services/file_kinds.py) for counting and
// filtering; this table only decides how a known file is drawn.
import { shallowRef } from 'vue'
import {
  FileText, FileCode, Table2, Braces, Image, Mic, Globe, Terminal, Database,
  FileJson, Settings2, FileType, Hash, Layers,
} from 'lucide-vue-next'

// Types an administrator registered (Settings → Custom file types), keyed by
// bare lowercase extension: { acme: { kind: 'data', label: 'Acme export' } }.
// Reactive, so anything that draws a file with describeFile() updates when the
// list loads or changes. Filled by stores/customFileTypesStore from
// GET /api/custom-file-types; the server's kind is authoritative.
export const customFileTypes = shallowRef({})
export function setCustomFileTypes(list) {
  const next = {}
  for (const t of list || []) {
    const ext = String(t?.extension || '').toLowerCase().replace(/^\./, '')
    if (ext) next[ext] = { kind: t.kind, label: t.label || '', enabled: t.enabled !== false }
  }
  customFileTypes.value = next
}

const LANG = {
  // label, icon override
  py: ['py'], pyw: ['py'], pyi: ['pyi'],
  js: ['js'], jsx: ['jsx'], mjs: ['js'], cjs: ['js'],
  ts: ['ts'], tsx: ['tsx'], mts: ['ts'], cts: ['ts'],
  vue: ['vue', Layers], svelte: ['svelte', Layers], astro: ['astro', Layers],
  cs: ['c#'], java: ['java'], kt: ['kt'], kts: ['kt'], scala: ['scala'], sc: ['scala'], groovy: ['groovy'], gradle: ['gradle'],
  go: ['go'], rs: ['rs'], zig: ['zig'], nim: ['nim'], jl: ['jl'], dart: ['dart'],
  c: ['c'], h: ['h'], cpp: ['c++'], cxx: ['c++'], cc: ['c++'], hpp: ['h++'], hxx: ['h++'], hh: ['h++'], m: ['objc'], mm: ['objc'],
  php: ['php'], phtml: ['php'], rb: ['rb'], rake: ['rb'], gemspec: ['rb'], pl: ['perl'], pm: ['perl'], t: ['perl'],
  swift: ['swift'], r: ['R'], rmd: ['Rmd'], lua: ['lua'], ex: ['ex'], exs: ['ex'], erl: ['erl'], hrl: ['erl'], hs: ['hs'],
  clj: ['clj'], cljs: ['cljs'], cljc: ['clj'], edn: ['edn'], fs: ['f#'], fsx: ['f#'], vb: ['vb'], vbs: ['vbs'], bas: ['bas'],
  f: ['f'], f90: ['f90'], f95: ['f95'], for: ['f'], cbl: ['cobol'], cob: ['cobol'], cpy: ['cobol'],
  sh: ['sh', Terminal], bash: ['sh', Terminal], zsh: ['zsh', Terminal], fish: ['fish', Terminal], ksh: ['sh', Terminal],
  ps1: ['ps1', Terminal], psm1: ['ps1', Terminal], psd1: ['ps1', Terminal], bat: ['bat', Terminal], cmd: ['cmd', Terminal],
  sql: ['sql', Database], psql: ['sql', Database], ddl: ['sql', Database],
  pas: ['pas'], dpr: ['dpr'], dpk: ['dpk'], pp: ['pas'], inc: ['inc'], dfm: ['dfm'], mod: ['mod'], def: ['def'], mi: ['mi'],
  asm: ['asm'], s: ['asm'],
  css: ['css', Hash], scss: ['scss', Hash], sass: ['sass', Hash], less: ['less', Hash], styl: ['styl', Hash],
  yaml: ['yaml', Settings2], yml: ['yaml', Settings2], toml: ['toml', Settings2], ini: ['ini', Settings2], cfg: ['cfg', Settings2],
  conf: ['conf', Settings2], properties: ['props', Settings2], xml: ['xml', FileCode], xsd: ['xsd', FileCode], xsl: ['xsl', FileCode],
  plist: ['plist', Settings2], csproj: ['csproj', Settings2], vbproj: ['vbproj', Settings2], sln: ['sln', Settings2], cmake: ['cmake', Settings2],
  tf: ['tf', Settings2], tfvars: ['tf', Settings2], hcl: ['hcl', Settings2], proto: ['proto'], graphql: ['gql'], gql: ['gql'],
  mk: ['make', Terminal], ninja: ['ninja', Terminal], dockerfile: ['docker', Terminal], make: ['make', Terminal],
}

const NAMED = {
  makefile: ['make', Terminal], gnumakefile: ['make', Terminal], dockerfile: ['docker', Terminal], containerfile: ['docker', Terminal],
  jenkinsfile: ['jenkins', Terminal], vagrantfile: ['vagrant', Terminal], rakefile: ['rb'], gemfile: ['rb'], procfile: ['proc', Terminal],
  'cmakelists.txt': ['cmake', Settings2], brewfile: ['brew', Terminal], justfile: ['just', Terminal], pipfile: ['pipfile', Settings2],
  '.gitignore': ['git', Settings2], '.dockerignore': ['docker', Settings2], '.editorconfig': ['editor', Settings2],
  '.bashrc': ['sh', Terminal], '.zshrc': ['zsh', Terminal], '.profile': ['sh', Terminal], '.env.example': ['env', Settings2],
}

const DOCS = { pdf: ['pdf'], txt: ['txt'], docx: ['docx'], md: ['md'], html: ['html'], htm: ['html'] }
const DATA = { csv: ['csv', Table2], xlsx: ['xlsx', Table2], xls: ['xls', Table2], json: ['json', FileJson], jsonl: ['jsonl', FileJson] }
const MEDIA = {
  png: ['png', Image], jpg: ['jpg', Image], jpeg: ['jpg', Image], gif: ['gif', Image], webp: ['webp', Image], bmp: ['bmp', Image], tif: ['tiff', Image], tiff: ['tiff', Image],
  mp3: ['mp3', Mic], wav: ['wav', Mic], m4a: ['m4a', Mic], ogg: ['ogg', Mic], flac: ['flac', Mic], webm: ['webm', Mic], mp4: ['mp4', Mic], aac: ['aac', Mic], wma: ['wma', Mic],
}

export const FAMILIES = {
  code: { label: 'Code', icon: FileCode, tile: 'bg-primary/15 text-primary' },
  docs: { label: 'Docs', icon: FileText, tile: 'bg-base-content/10 text-base-content/75' },
  data: { label: 'Data', icon: Table2, tile: 'bg-success/15 text-success' },
  media: { label: 'Media', icon: Image, tile: 'bg-secondary/15 text-secondary' },
  web: { label: 'Web', icon: Globe, tile: 'bg-info/15 text-info' },
  other: { label: 'Other', icon: FileType, tile: 'bg-base-content/10 text-base-content/60' },
}

const lower = (s) => String(s || '').toLowerCase()
const baseName = (name) => lower(name).split(/[\\/]/).pop()
export const extOf = (name) => {
  const b = baseName(name)
  const i = b.lastIndexOf('.')
  return i > 0 ? b.slice(i + 1) : ''
}

// { family, label, icon, tile } for any filename; web sources pass isWeb.
export function describeFile(name, { isWeb = false } = {}) {
  if (isWeb) return { family: 'web', label: 'web', icon: Globe, tile: FAMILIES.web.tile }
  const b = baseName(name)
  const ext = extOf(name)
  const named = NAMED[b]
  if (named) return { family: 'code', label: named[0], icon: named[1] || FileCode, tile: FAMILIES.code.tile }
  if (DOCS[ext]) return { family: 'docs', label: DOCS[ext][0], icon: DOCS[ext][1] || FileText, tile: FAMILIES.docs.tile }
  if (DATA[ext]) return { family: 'data', label: DATA[ext][0], icon: DATA[ext][1] || Table2, tile: FAMILIES.data.tile }
  if (MEDIA[ext]) return { family: 'media', label: MEDIA[ext][0], icon: MEDIA[ext][1] || Image, tile: FAMILIES.media.tile }
  if (LANG[ext]) return { family: 'code', label: LANG[ext][0], icon: LANG[ext][1] || FileCode, tile: FAMILIES.code.tile }
  if (ext === 'ipynb') return { family: 'code', label: 'nb', icon: Braces, tile: FAMILIES.code.tile }
  // A registered custom type is drawn as its profile's kind, never as "other".
  // Built-ins win above, matching the server (a profile cannot take over .pdf).
  const custom = customFileTypes.value[ext]
  if (custom && FAMILIES[custom.kind] && custom.kind !== 'other') {
    const icon = custom.kind === 'data' ? FileJson : custom.kind === 'code' ? FileCode : FileText
    return { family: custom.kind, label: ext, icon, tile: FAMILIES[custom.kind].tile }
  }
  return { family: 'other', label: ext || '', icon: FileType, tile: FAMILIES.other.tile }
}

export const isCodeFile = (name) => describeFile(name).family === 'code'
export const isTabularFile = (name) => ['csv', 'xlsx', 'xls'].includes(extOf(name))

// Where a cited passage sits, in the unit the reader would look for. The
// index calls every part a "page", but only a PDF has pages: text formats
// are split at headings, decks by slide, spreadsheets by row groups.
export function locationLabel(name, page) {
  if (!page) return ''
  const ext = extOf(name)
  if (ext === 'pdf') return `p.${page}`
  if (ext === 'pptx') return `slide ${page}`
  if (isTabularFile(name)) return `part ${page}`
  return `§${page}`
}
