import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import { migrateBrowserStorage } from './utils/storageMigration'
import { startBootstrap } from './utils/http'
import { prepareStorageScope } from './utils/storageScope'
import { installErrorHandler } from './utils/errorHandler'
import { useUiStore } from './stores/uiStore'

import App from './App.vue'

// Carry browser state over from the pre-rename key names before any store
// (Pinia or module-level) reads it.
migrateBrowserStorage()

/* global __CLIO_BUILD__ */
// Lineage, filled in at build time (frontend/vite.config.js). It is set on
// the document so a page built from this code identifies its origin even
// with the About dialog removed.
const build = __CLIO_BUILD__
document.documentElement.dataset.clioLineage = build.lineage
document.documentElement.dataset.clioBuild = `${build.version}+${build.commit.slice(0, 12)}`

const app = createApp(App)

app.use(createPinia())
installErrorHandler(app, () => useUiStore())

// Who is signed in decides which person's browser state is read
// (utils/storageScope.js), so it is asked before anything reads storage.
// Then one request for the reads every store makes on startup
// (utils/bootstrap.js). Both sent after Pinia is active: the http client's
// interceptors use the ui store.
prepareStorageScope().finally(() => {
  let openingCollection = 'default'
  try { openingCollection = localStorage.getItem('clio_current_collection') || 'default' } catch { /* private mode */ }
  startBootstrap(openingCollection)

  app.mount('#app')
})
