import { ref } from 'vue'
import { defineStore } from 'pinia'
import { loadLayout, saveLayout } from '../utils/ribbon.js'

// How the shell is arranged for this person: the ribbon's items and order,
// whether it shows names, and whether the Sources panel is a folder tree or
// a detailed list. Per-browser preferences, edited from the ribbon itself,
// the Customize dialog and Settings → Layout, so they live in one place.
function readPref(key, fallback) {
  try { return localStorage.getItem(key) ?? fallback } catch { return fallback }
}
function writePref(key, value) {
  try { localStorage.setItem(key, value) } catch { /* a preference, not data */ }
}

// The Connect tab knows forty-odd MCP clients; most people use two. The
// ones hidden here are remembered as a list of keys, so a client added in a
// later release shows up by default instead of vanishing.
const HIDDEN_MCP_KEY = 'clio_mcp_hidden_clients'
function readHiddenMcpClients() {
  try {
    const parsed = JSON.parse(localStorage.getItem(HIDDEN_MCP_KEY) || '[]')
    return Array.isArray(parsed) ? parsed.filter((k) => typeof k === 'string') : []
  } catch { return [] }
}

export const useLayoutStore = defineStore('layout', () => {
  const ribbon = ref(loadLayout())
  const customizeOpen = ref(false)
  const sourcesView = ref(readPref('clio_sources_view', 'tree') === 'list' ? 'list' : 'tree')
  const hiddenMcpClients = ref(readHiddenMcpClients())

  function setRibbon(layout) {
    ribbon.value = { ...layout }
    saveLayout(ribbon.value)
  }
  function setRibbonLabels(on) {
    setRibbon({ ...ribbon.value, labels: Boolean(on) })
  }
  function setSourcesView(view) {
    sourcesView.value = view === 'list' ? 'list' : 'tree'
    writePref('clio_sources_view', sourcesView.value)
  }
  function setHiddenMcpClients(keys) {
    hiddenMcpClients.value = [...new Set(keys.filter((k) => typeof k === 'string'))]
    writePref(HIDDEN_MCP_KEY, JSON.stringify(hiddenMcpClients.value))
  }
  function setMcpClientHidden(key, hidden) {
    const rest = hiddenMcpClients.value.filter((k) => k !== key)
    setHiddenMcpClients(hidden ? [...rest, key] : rest)
  }

  return {
    ribbon, customizeOpen, sourcesView, hiddenMcpClients,
    setRibbon, setRibbonLabels, setSourcesView, setHiddenMcpClients, setMcpClientHidden,
  }
})
