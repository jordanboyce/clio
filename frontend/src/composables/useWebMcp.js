// Offer Clio's read-only MCP tools to an agent in this browser (WebMCP).
//
// One registration per page: App.vue starts it on mount, the Connect tab
// shows its state and holds the switch. The tool list comes from the
// server's `tools/list`, so it is never stale and carries real JSON
// schemas; each call goes back through `/mcp` with the page's session
// (utils/mcpPageClient.js), scoped to the collection open at the moment of
// the call, so switching collections needs no re-registration.
//
// Only read-only tools are offered. A browser agent acts as the person
// without any confirmation step Clio controls, and the write tools
// (write_document, reindex_document, ...) stay behind an explicit token
// with `can_write`, as they do for desktop clients.
//
// Registration quirks, learned from the Browser AI Lab demos
// (github.com/jordanboyce/window-ai): `registerTool` returns a promise that
// some builds settle only when the abort signal fires, so it is not
// awaited; a re-registration under the same name throws "Duplicate tool
// name", which is treated as already registered; aborting the controller is
// the one way to unregister.

import { reactive, readonly } from 'vue'
import { getModelContext, describeBrowser, supportState, toWebMcpTool, isReadOnly } from '../utils/webmcp'
import { createMcpPageClient } from '../utils/mcpPageClient'
import { useCollectionStore } from '../stores/collectionStore'

export const WEBMCP_PREF_KEY = 'clio_webmcp_enabled'
const CALLS_KEPT = 20
const DUPLICATE_NAME = /duplicate tool name|already registered/i

/* global __CLIO_BUILD__ */
const buildVersion = () => (typeof __CLIO_BUILD__ !== 'undefined' && __CLIO_BUILD__?.version) || 'dev'

const readPref = () => {
  try {
    return localStorage.getItem(WEBMCP_PREF_KEY) !== '0'
  } catch {
    return true
  }
}

const state = reactive({
  // 'unknown' until start() has looked; then one of utils/webmcp supportState().
  support: 'unknown',
  browser: { chromium: false, name: 'This browser', version: null, flagsScheme: 'chrome' },
  enabled: readPref(),
  // idle | registering | ready | off | error | unsupported
  status: 'idle',
  tools: [],
  failed: [],
  error: '',
  // The most recent calls a browser agent made through this page.
  calls: [],
})

let controller = null
let started = false
let client = null

function invokeFactory(mcpClient) {
  return async (name, args, signal) => {
    let collectionId = ''
    try {
      collectionId = useCollectionStore().currentCollectionId || ''
    } catch {
      // No active Pinia (tests, or a call before the app mounted): the
      // server falls back to its default collection.
    }
    const entry = { name, at: Date.now(), collectionId, ok: null }
    state.calls = [entry, ...state.calls].slice(0, CALLS_KEPT)
    try {
      const result = await mcpClient.callTool(name, args, { collectionId, signal })
      entry.ok = !result?.isError
      return result
    } catch (err) {
      entry.ok = false
      throw err
    }
  }
}

async function register(modelContext) {
  controller = new AbortController()
  const { signal } = controller
  state.status = 'registering'
  state.error = ''
  state.failed = []
  state.tools = []

  await client.initialize({ signal }).catch(() => {})
  const tools = (await client.listTools({ signal })).filter(isReadOnly)
  if (signal.aborted) return

  const invoke = invokeFactory(client)
  const registered = []
  for (const tool of tools) {
    try {
      const outcome = modelContext.registerTool(toWebMcpTool(tool, invoke), { signal })
      if (outcome && typeof outcome.then === 'function') {
        // Settles with AbortError on teardown; anything else is reported.
        outcome.catch((err) => {
          if (err?.name === 'AbortError') return
          if (DUPLICATE_NAME.test(err?.message || '')) return
          state.failed = [...state.failed.filter((f) => f.name !== tool.name), { name: tool.name, message: err?.message || String(err) }]
          state.tools = state.tools.filter((t) => t.name !== tool.name)
        })
      }
      registered.push({ name: tool.name, title: tool.title || tool.name })
    } catch (err) {
      if (DUPLICATE_NAME.test(err?.message || '')) {
        registered.push({ name: tool.name, title: tool.title || tool.name })
      } else {
        state.failed.push({ name: tool.name, message: err?.message || String(err) })
      }
    }
  }
  state.tools = registered
  state.status = 'ready'
}

/**
 * Look for the API and, when it is there and the switch is on, offer the
 * tools. Idempotent: App.vue and the Connect tab may both call it.
 */
async function start({ modelContext, client: injected, win } = {}) {
  if (started && !injected && !modelContext) return
  started = true
  const target = win || globalThis
  state.browser = describeBrowser(target)
  const context = modelContext || getModelContext(target)
  state.support = context ? 'available' : supportState(target)
  if (!context) {
    state.status = 'unsupported'
    return
  }
  client = injected || createMcpPageClient({ clientVersion: buildVersion() })
  if (!state.enabled) {
    state.status = 'off'
    return
  }
  try {
    await register(context)
  } catch (err) {
    state.status = 'error'
    state.error = err?.message || String(err)
  }
}

/** Withdraw every tool. */
function stop() {
  if (controller) controller.abort()
  controller = null
  state.tools = []
  if (state.support === 'available') state.status = 'off'
}

/** The Connect tab's switch. Remembered per browser. */
async function setEnabled(value) {
  state.enabled = !!value
  try {
    localStorage.setItem(WEBMCP_PREF_KEY, value ? '1' : '0')
  } catch {
    // Private mode or blocked storage: the choice lasts for this page.
  }
  if (state.support !== 'available') return
  if (!value) {
    stop()
    return
  }
  const context = getModelContext()
  if (!context) return
  stop()
  client = client || createMcpPageClient({ clientVersion: buildVersion() })
  try {
    await register(context)
  } catch (err) {
    state.status = 'error'
    state.error = err?.message || String(err)
  }
}

/** Tests only: forget the page-level singleton. */
function _reset() {
  stop()
  started = false
  client = null
  state.support = 'unknown'
  state.status = 'idle'
  state.failed = []
  state.error = ''
  state.calls = []
  state.enabled = readPref()
}

export function useWebMcp() {
  return { state: readonly(state), start, stop, setEnabled, _reset }
}
