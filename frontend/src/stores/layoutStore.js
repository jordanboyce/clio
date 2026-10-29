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

export const useLayoutStore = defineStore('layout', () => {
  const ribbon = ref(loadLayout())
  const customizeOpen = ref(false)
  const sourcesView = ref(readPref('clio_sources_view', 'tree') === 'list' ? 'list' : 'tree')

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

  return { ribbon, customizeOpen, sourcesView, setRibbon, setRibbonLabels, setSourcesView }
})
