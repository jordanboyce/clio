import { execSync } from 'node:child_process'
import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'
import tailwindcss from '@tailwindcss/vite'

// API server URL - can be overridden with VITE_API_URL env var
const apiTarget = process.env.VITE_API_URL || 'http://localhost:8473'

// Lineage. Mirrors services/provenance.py: the same id, source and notice go
// into the top of every built chunk and stylesheet (see lineageBanner) and
// into the page as data attributes, so a product built from this frontend
// still says where it came from. Do not change the id.
const LINEAGE_ID = 'clio.helicon.58a48ab52f51'
const SOURCE_URL = 'https://github.com/jordanboyce/clio'
const COPYRIGHT = 'Copyright (c) 2026 Jordan Boyce'

function git(args) {
  try {
    return execSync(`git ${args}`, { stdio: ['ignore', 'pipe', 'ignore'] }).toString().trim()
  } catch {
    return ''
  }
}
const envOr = (name, fallback) => {
  const v = (process.env[name] || '').trim()
  return v && v !== fallback ? v : ''
}
const buildVersion = envOr('CLIO_VERSION', 'dev') || git('describe --tags --always --dirty') || 'dev'
const buildCommit = envOr('CLIO_VCS_REF', 'unknown') || git('rev-parse HEAD') || 'unknown'
const banner =
  `/*! Clio ${buildVersion} (${buildCommit}) — ${SOURCE_URL} — ${COPYRIGHT}. ` +
  `MIT License. lineage:${LINEAGE_ID} */\n`

// Prepends the banner to every emitted JS chunk and CSS asset. Done in
// generateBundle rather than through rollupOptions.output.banner because the
// minifier runs in renderChunk and drops comments, including that banner.
function lineageBanner() {
  return {
    name: 'clio-lineage-banner',
    apply: 'build',
    generateBundle(_, bundle) {
      for (const file of Object.values(bundle)) {
        if (file.type === 'chunk') {
          file.code = banner + file.code
        } else if (file.fileName.endsWith('.css') && typeof file.source === 'string') {
          file.source = banner + file.source
        }
      }
    },
  }
}

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [
    vue(),
    // Dev-only: the devtools overlay must never ship in production builds.
    command === 'serve' && vueDevTools(),
    tailwindcss(),
    lineageBanner(),
  ].filter(Boolean),
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    },
  },
  define: {
    __CLIO_BUILD__: JSON.stringify({
      version: buildVersion,
      commit: buildCommit,
      lineage: LINEAGE_ID,
      source: SOURCE_URL,
    }),
  },
  build: {
    // Default Vite output (frontend/dist). Not committed to git — the backend
    // serves it directly and Docker builds it in a dedicated stage.
    outDir: 'dist',
    emptyOutDir: true,
  },
  server: {
    port: 5173,
    proxy: {
      // Proxy API requests to the backend server
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/documents': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/search': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/health': {
        target: apiTarget,
        changeOrigin: true,
      },
      // The page itself calls the MCP endpoint when it offers tools to a
      // browser agent (composables/useWebMcp.js).
      '/mcp': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
}))
