#!/usr/bin/env node
// Fetch the browser runtime a static-search site needs to embed queries
// itself: transformers.js and the ONNX Runtime WebAssembly build it loads.
//
//   node scripts/vendor_static_search.mjs <out-dir>
//
// Writes <out-dir>/vendor/ (the files a site serves, their licences and
// runtime.json) and <out-dir>/node_modules/<package>/package.json, so an SBOM
// scan of the folder names the packages the files came from.
//
// The image runs this at build time (Dockerfile, frontend stage) so the
// website export can carry the runtime into a network that reaches neither
// jsDelivr nor Hugging Face (docs/STATIC_SEARCH.md). A source install can run
// it once, with the npm registry reachable, into $DATA_DIR/static-search.
//
// Versions and checksums are pinned here. transformers.js bundles the
// JavaScript half of ONNX Runtime and fetches the matching WebAssembly at run
// time, so the two move together: when updating, take onnxruntime-web's
// version from @huggingface/transformers' package.json, and the checksums
// from `npm view <package>@<version> dist.integrity`.
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { gunzipSync } from 'node:zlib'

const PACKAGES = [
  {
    name: '@huggingface/transformers',
    version: '4.3.2',
    integrity: 'sha512-V3+dqf3iWlRYJ9I9GreM3zGQd0lAXnnoPeddmkOQYWAEE4l/+Fi6p7tx/qVUJlKQgLXGim2W76rTOzz+eC0i7g==',
    files: { 'dist/transformers.min.js': 'transformers.min.js' },
  },
  {
    name: 'onnxruntime-web',
    version: '1.31.0-dev.20260914-8d85527a0',
    integrity: 'sha512-Iy7rtadoBgxS/LLvDr3QW38DB1PNXRnr0GJMcL0TAt7c9qjgVQl83UlGCVyeAnK2InpmW8Uc3PL8XIuqtDeF6g==',
    // The package says MIT but ships no licence file; this is the
    // microsoft/onnxruntime repository's LICENSE, which the notice requires.
    licence: `MIT License

Copyright (c) Microsoft Corporation

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
`,
    // The only WebAssembly build transformers.min.js asks for. Its loader is
    // renamed from .mjs to .js: many web servers (older nginx, Python's on
    // Windows) send .mjs as text/plain, and a browser will not run a module
    // served that way. The page names both files itself (wasmPaths), so the
    // library never looks for the original name.
    files: {
      'dist/ort-wasm-simd-threaded.asyncify.mjs': 'ort-wasm-simd-threaded.asyncify.js',
      'dist/ort-wasm-simd-threaded.asyncify.wasm': 'ort-wasm-simd-threaded.asyncify.wasm',
    },
  },
]

// The regular files of a tar archive by path, npm's leading "package/"
// removed. Enough of the ustar format for an npm tarball, so no tar binary is
// needed (Git Bash's reads the "C:" of a Windows path as a remote host).
function untar(buffer) {
  const files = new Map()
  const cString = (bytes) => {
    const end = bytes.indexOf(0)
    return bytes.subarray(0, end === -1 ? bytes.length : end).toString('utf8')
  }
  let longName = null
  for (let at = 0; at + 512 <= buffer.length;) {
    const header = buffer.subarray(at, at + 512)
    if (header.every((b) => b === 0)) break
    const field = (start, length) => cString(header.subarray(start, start + length))
    const size = parseInt(field(124, 12).trim() || '0', 8)
    const type = field(156, 1) || '0'
    const prefix = field(345, 155)
    const name = longName ?? (prefix ? `${prefix}/${field(0, 100)}` : field(0, 100))
    longName = null
    const body = buffer.subarray(at + 512, at + 512 + size)
    if (type === 'L') longName = cString(body)
    else if (type === '0' || type === '7') files.set(name.replace(/^[^/]+\//, ''), Buffer.from(body))
    at += 512 + Math.ceil(size / 512) * 512
  }
  return files
}

// npm is a .cmd script on Windows, which only a shell can start.
const npm = (args) => process.platform === 'win32'
  ? execFileSync('cmd.exe', ['/d', '/c', 'npm', ...args], { encoding: 'utf8' })
  : execFileSync('npm', args, { encoding: 'utf8' })

const out = process.argv[2]
if (!out) {
  console.error('usage: node scripts/vendor_static_search.mjs <out-dir>')
  process.exit(2)
}
const outDir = resolve(out)
const vendor = join(outDir, 'vendor')
mkdirSync(vendor, { recursive: true })
const work = mkdtempSync(join(tmpdir(), 'clio-static-vendor-'))

try {
  const runtime = { library: '@huggingface/transformers', packages: {}, files: {} }
  for (const pkg of PACKAGES) {
    const spec = `${pkg.name}@${pkg.version}`
    // npm pack downloads the published tarball and runs none of its scripts.
    const made = npm(['pack', spec, '--silent', '--pack-destination', work]).trim().split(/\r?\n/).pop()
    const tarball = readFileSync(join(work, made))
    const got = 'sha512-' + createHash('sha512').update(tarball).digest('base64')
    if (got !== pkg.integrity) throw new Error(`${spec}: checksum ${got} is not the pinned ${pkg.integrity}`)

    const entries = untar(gunzipSync(tarball))
    const file = (name) => {
      const data = entries.get(name)
      if (!data) throw new Error(`${spec}: the package has no ${name}`)
      return data
    }
    const meta = join(outDir, 'node_modules', ...pkg.name.split('/'))
    mkdirSync(meta, { recursive: true })
    writeFileSync(join(meta, 'package.json'), file('package.json'))
    const shipped = ['LICENSE', 'LICENSE.md', 'LICENSE.txt'].find((f) => entries.has(f))
    const licence = shipped ? file(shipped) : pkg.licence
    if (!licence) throw new Error(`${spec}: no licence text to ship with it`)
    writeFileSync(join(vendor, `LICENSE.${pkg.name.replace(/^@/, '').replace(/\//g, '_')}.txt`), licence)

    for (const [from, to] of Object.entries(pkg.files)) {
      writeFileSync(join(vendor, to), file(from))
      runtime.files[to] = createHash('sha256').update(file(from)).digest('hex')
    }
    const { license } = JSON.parse(file('package.json').toString('utf8'))
    runtime.packages[pkg.name] = { version: pkg.version, license }
  }
  // What an export records about the runtime it ships (services/static_export.py).
  writeFileSync(join(vendor, 'runtime.json'), JSON.stringify(runtime, null, 2) + '\n')
  console.log(`static-search runtime ready in ${vendor}`)
} finally {
  rmSync(work, { recursive: true, force: true })
}
