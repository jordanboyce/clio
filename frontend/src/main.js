import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import { migrateBrowserStorage } from './utils/storageMigration'

import Root from './Root.vue'

// Carry browser state over from the pre-rename key names before any store
// (Pinia or module-level) reads it.
migrateBrowserStorage()

// The workspace mounts inside the auth gate: with built-in accounts it shows
// the sign-in / first-run setup page until there is a session, and otherwise
// passes straight through.
const app = createApp(Root)

app.use(createPinia())

app.mount('#app')
