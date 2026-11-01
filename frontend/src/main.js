import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import { migrateBrowserStorage } from './utils/storageMigration'

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

app.mount('#app')
