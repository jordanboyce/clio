import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import { migrateBrowserStorage } from './utils/storageMigration'

import App from './App.vue'

// Carry browser state over from the pre-rename key names before any store
// (Pinia or module-level) reads it.
migrateBrowserStorage()

const app = createApp(App)

app.use(createPinia())

app.mount('#app')
