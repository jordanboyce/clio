import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

const nodeMajor = Number(process.versions.node.split('.')[0])

export default defineConfig({
  plugins: [vue()],
  define: {
    __CLIO_BUILD__: JSON.stringify({ version: 'test', commit: 'test', lineage: 'clio.helicon.58a48ab52f51', source: 'https://github.com/jordanboyce/clio' }),
  },
  test: {
    environment: 'jsdom',
    globals: false,
    // Node 22+ exposes an experimental global localStorage that shadows
    // jsdom's implementation unless the worker disables it explicitly.
    execArgv: nodeMajor >= 22 ? ['--no-experimental-webstorage'] : [],
  },
})
