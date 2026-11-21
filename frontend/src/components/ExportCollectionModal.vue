<template>
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="export-collection-title" @close="modal.onClosed">
    <div class="modal-box" :class="mode === 'site' ? 'max-w-lg' : 'max-w-md'">
      <h3 id="export-collection-title" class="font-bold text-lg">Export {{ target?.name }}</h3>
      <div role="tablist" aria-label="What the export is for" class="tabs tabs-border tabs-sm mt-2">
        <button role="tab" class="tab" :class="{ 'tab-active': mode === 'bundle' }" :aria-selected="mode === 'bundle'" @click="setMode('bundle')">For another Clio</button>
        <button role="tab" class="tab" :class="{ 'tab-active': mode === 'site' }" :aria-selected="mode === 'site'" @click="setMode('site')">For a website</button>
      </div>
      <p v-if="mode === 'bundle'" class="text-sm text-base-content/70 mt-3 mb-4">
        One <code>.clio.zip</code> another Clio can import. Everything already read and indexed travels with it,
        so nothing is scanned or OCR'd again.
      </p>
      <p v-else class="text-sm text-base-content/70 mt-3 mb-4">
        Files a website can search with no server behind it, to put into the site's own pages or serve as a
        ready-made search page. Each result can link to the page its source is on.
      </p>

      <template v-if="mode === 'site'">
        <div v-if="siteLoading" class="py-6 text-center"><span class="loading loading-spinner loading-sm"></span></div>
        <div v-else-if="siteError" class="alert alert-error text-sm">{{ siteError }}</div>
        <div v-else-if="site" class="space-y-3">
          <ModelFact :embedding="site.embedding">
            <div class="text-xs text-base-content/60 mt-1.5">
              {{ site.documents }} source{{ site.documents === 1 ? '' : 's' }} ·
              {{ site.passages.toLocaleString() }} passages
              <span v-if="site.held_excluded"> · {{ site.held_excluded }} held source{{ site.held_excluded === 1 ? '' : 's' }} left out</span>
              <span v-if="site.not_public_excluded"> · {{ site.not_public_excluded }} not labelled Public left out</span>
            </div>
          </ModelFact>

          <div v-for="blocker in site.blockers" :key="blocker.code" class="alert alert-warning text-sm items-start" role="alert">
            <span>{{ blocker.message }}</span>
          </div>

          <template v-if="site.ready">
            <p v-if="site.search === 'keywords'" class="text-xs text-base-content/70 rounded-box bg-base-200 p-2.5" data-testid="keyword-search">
              {{ site.keywords_reason }}
              About {{ formatBytes(site.passages_bytes) }} of text<span v-if="site.passage_files > 1"> in {{ site.passage_files }} files</span>.
            </p>
            <p v-else class="text-xs text-base-content/65">
              The site must embed each query in the browser with <code>{{ site.browser_model }}</code>. The manifest
              says so, and carries a check that turns search by meaning off for any other model.
              About {{ formatBytes(site.passages_bytes) }} of text<span v-if="site.passage_files > 1"> in {{ site.passage_files }} files</span>
              and {{ formatBytes(site.vectors_bytes) }} of vectors.
            </p>

            <details class="rounded-box border border-base-300 text-sm" data-testid="result-links" @toggle="loadAddresses">
              <summary class="cursor-pointer px-3 py-2">
                <span class="font-medium">Where results link</span>
                <span class="text-base-content/60"> · {{ linkSummary }}</span>
              </summary>
              <div class="px-3 pb-3 space-y-2.5">
                <p class="text-xs text-base-content/60">
                  A result opens its source on your website: a PDF at the page, a web page at the passage. Sources
                  added by link, and files synced from a public repository, already know where they are.
                </p>
                <label class="block">
                  <span class="text-xs font-medium">Where the site keeps its copies of the files</span>
                  <input
                    v-model="baseUrl"
                    name="files-base-url"
                    type="text"
                    inputmode="url"
                    class="input input-sm w-full mt-1"
                    placeholder="https://www.example.org/files/handbook/"
                    aria-describedby="files-base-url-hint"
                  />
                  <span id="files-base-url-hint" class="block text-xs text-base-content/60 mt-1">
                    Each file's name is added to it. A path on the site, such as /files/handbook/, works too.
                  </span>
                </label>
                <div v-if="addressesLoading" class="py-2 text-center"><span class="loading loading-spinner loading-xs"></span></div>
                <ul v-else-if="addresses" class="max-h-48 overflow-y-auto space-y-1.5" aria-label="Each source's address">
                  <li v-for="row in addresses.documents" :key="row.id" class="text-xs">
                    <label class="block">
                      <span class="block truncate" :title="row.name">{{ row.name }}</span>
                      <input
                        v-model="ownUrls[row.id]"
                        type="text"
                        inputmode="url"
                        class="input input-xs w-full"
                        :placeholder="row.from && row.from !== 'own' ? row.url : 'No address'"
                        :aria-label="`Address of ${row.name}`"
                      />
                    </label>
                  </li>
                </ul>
                <p v-if="addressError" class="text-xs text-error" role="alert">{{ addressError }}</p>
                <div class="flex items-center gap-2">
                  <button type="button" class="btn btn-sm" :disabled="addressesSaving || !addressesChanged" @click="saveAddresses">
                    Save addresses
                  </button>
                  <span v-if="addressesSaved" class="text-xs text-success" role="status">Saved</span>
                </div>
              </div>
            </details>

            <label v-if="site.site?.available" class="flex items-start gap-3 cursor-pointer">
              <input v-model="includeModel" name="include-model" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
              <span v-if="site.site.model" class="text-sm">
                Include the model, as a site ready to serve<span class="text-base-content/60"> (about {{ formatBytes(site.site.bytes) }} more)</span>
                <span class="block text-xs text-base-content/60">
                  The page, the search box for your own pages, {{ site.site.model }} and the library that runs it,
                  so the site loads nothing from Hugging Face or a CDN. Works on a network that blocks both.
                </span>
              </span>
              <span v-else class="text-sm">
                As a site ready to serve
                <span class="block text-xs text-base-content/60">
                  The page, the search box for your own pages, and settings for common web servers.
                </span>
              </span>
            </label>
            <p v-else-if="site.site?.reason" class="text-xs text-base-content/60">{{ site.site.reason }}</p>
            <fieldset v-if="site.additions?.length" class="rounded-box border border-base-300 px-3 pb-2.5 pt-1" data-testid="additions">
              <legend class="text-sm font-medium px-1">Also include</legend>
              <label
                v-for="addition in site.additions"
                :key="addition.id"
                class="flex items-start gap-3 py-1"
                :class="additionOpen(addition) ? 'cursor-pointer' : 'opacity-70'"
              >
                <input
                  v-model="chosen[addition.id]"
                  :name="`include-${addition.id}`"
                  type="checkbox"
                  class="checkbox checkbox-sm mt-0.5"
                  :disabled="!additionOpen(addition)"
                />
                <span class="text-sm">
                  {{ addition.label }}<span v-if="addition.available" class="text-base-content/60"> (about {{ formatBytes(addition.bytes) }})</span>
                  <span v-if="!addition.available" class="block text-xs text-base-content/60">{{ addition.reason }}</span>
                  <span v-else-if="addition.site_only && !wholeSite" class="block text-xs text-base-content/60">Needs the site ready to serve.</span>
                  <span v-else-if="addition.note" class="block text-xs text-base-content/60">{{ addition.note }}</span>
                </span>
              </label>
            </fieldset>
            <label class="flex items-start gap-3 cursor-pointer">
              <input v-model="confirmPublic" name="confirm-public" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
              <span class="text-sm">
                Everything in this collection is cleared for public release
                <span class="block text-xs text-base-content/60">
                  The text of every passage goes to every visitor, and the vectors give away its gist.
                  This export is recorded in the audit trail.
                </span>
              </span>
            </label>
          </template>
        </div>
      </template>

      <div v-else-if="loading" class="py-6 text-center"><span class="loading loading-spinner loading-sm"></span></div>
      <div v-else-if="error" class="alert alert-error text-sm">{{ error }}</div>
      <div v-else-if="preview" class="space-y-3">
        <ModelFact :embedding="preview.embedding">
          <div class="text-xs text-base-content/60 mt-1.5">
            {{ preview.documents }} source{{ preview.documents === 1 ? '' : 's' }} ·
            {{ preview.chunks.toLocaleString() }} passages
            <span v-if="preview.quarantined_excluded"> · {{ preview.quarantined_excluded }} held source{{ preview.quarantined_excluded === 1 ? '' : 's' }} left out</span>
          </div>
        </ModelFact>

        <p class="text-xs text-base-content/65">
          <template v-if="preview.vectors_included">
            The vectors are included for older versions of Clio. A current Clio embeds the text again with its
            own model and never redoes extraction or OCR.
          </template>
          <template v-else>
            The vectors can't be vouched for, so they are left out and the importer embeds the text with its own model.
          </template>
        </p>

        <label class="flex items-start gap-3 cursor-pointer">
          <input v-model="includeSources" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
          <span class="text-sm">
            Include the original files<span v-if="preview.sources_bytes" class="text-base-content/60"> (about {{ formatBytes(preview.sources_bytes) }})</span>
            <span class="block text-xs text-base-content/60">
              Needed to open originals or re-index later. Only share files you have the right to redistribute.
            </span>
          </span>
        </label>
        <p class="text-xs text-base-content/60">
          The extracted text is readable by anyone you give the file to.
        </p>
      </div>

      <div class="modal-action">
        <button class="btn btn-ghost" @click="modal.close()">Cancel</button>
        <a
          v-if="mode === 'site'"
          class="btn btn-primary"
          :class="{ 'btn-disabled': !siteHref }"
          :aria-disabled="!siteHref"
          :href="siteHref || '#'"
          download
          @click="siteHref ? modal.close() : $event.preventDefault()"
        >
          <Download :size="16" />
          Download
        </a>
        <a v-else class="btn btn-primary" :class="{ 'btn-disabled': !preview }" :href="href" download @click="modal.close()">
          <Download :size="16" />
          Download
        </a>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop"><button aria-label="Close">close</button></form>
  </dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { Download } from 'lucide-vue-next'
import http from '../utils/http'
import { useModal } from '../composables/useModal'
import { formatBytes } from '../utils/format'
import ModelFact from './ModelFact.vue'

const modal = useModal()
const target = ref(null)
const preview = ref(null)
const loading = ref(false)
const error = ref('')
const includeSources = ref(false)

// "For a website": read only when the tab is opened.
const mode = ref('bundle')
const site = ref(null)
const siteLoading = ref(false)
const siteError = ref('')
const confirmPublic = ref(false)
// On whenever the server can include the model: a site that carries it works
// anywhere, and its visitors' browsers contact no one else.
const includeModel = ref(true)
// Additions to the export, by id (the preview's `additions`).
const chosen = ref({})
const wholeSite = computed(() => Boolean(includeModel.value && site.value?.site?.available))
const additionOpen = (addition) => addition.available && (!addition.site_only || wholeSite.value)
const included = computed(() => (site.value?.additions || [])
  .filter((a) => chosen.value[a.id] && additionOpen(a)).map((a) => a.id))

// "Where results link": read when opened, saved with one button.
const addresses = ref(null)
const addressesLoading = ref(false)
const addressesSaving = ref(false)
const addressesSaved = ref(false)
const addressError = ref('')
const baseUrl = ref('')
const ownUrls = ref({})

const linkSummary = computed(() => {
  const counts = addresses.value || site.value?.addresses
  if (!counts) return ''
  if (!counts.total) return 'no sources'
  const line = `${counts.with_address} of ${counts.total} source${counts.total === 1 ? '' : 's'} link to a page`
  // The reader is a page in the export, not an address on the web: said apart.
  if (included.value.includes('reader') && counts.with_address < counts.total) {
    return `${line} on the web; the rest to their page in the export`
  }
  return line
})

const addressesChanged = computed(() => {
  if (!addresses.value) return false
  if ((baseUrl.value || '').trim() !== (addresses.value.files_base_url || '')) return true
  return addresses.value.documents.some((row) => (ownUrls.value[row.id] || '').trim() !== (row.own_url || ''))
})

function takeAddresses(data) {
  addresses.value = data
  baseUrl.value = data.files_base_url || ''
  ownUrls.value = Object.fromEntries(data.documents.map((row) => [row.id, row.own_url || '']))
}

async function loadAddresses(event) {
  if (!event?.target?.open || addresses.value || addressesLoading.value || !target.value) return
  addressesLoading.value = true
  addressError.value = ''
  try {
    takeAddresses((await http.get(`/api/collections/${encodeURIComponent(target.value.id)}/export/static/addresses`)).data)
  } catch (err) {
    addressError.value = err?.message || 'Could not read the addresses.'
  } finally {
    addressesLoading.value = false
  }
}

async function saveAddresses() {
  addressesSaving.value = true
  addressesSaved.value = false
  addressError.value = ''
  const documents = Object.fromEntries(addresses.value.documents
    .filter((row) => (ownUrls.value[row.id] || '').trim() !== (row.own_url || ''))
    .map((row) => [row.id, (ownUrls.value[row.id] || '').trim() || null]))
  try {
    const { data } = await http.put(`/api/collections/${encodeURIComponent(target.value.id)}/export/static/addresses`,
      { files_base_url: (baseUrl.value || '').trim() || null, documents })
    takeAddresses(data)
    addressesSaved.value = true
  } catch (err) {
    addressError.value = err?.message || 'The addresses were not saved.'
  } finally {
    addressesSaving.value = false
  }
}

const siteHref = computed(() => site.value?.ready && confirmPublic.value && target.value
  ? `/api/collections/${encodeURIComponent(target.value.id)}/export/static?confirm_public=true` +
    (wholeSite.value ? '&site=true' : '') +
    (included.value.length ? `&include=${encodeURIComponent(included.value.join(','))}` : '')
  : '')

async function setMode(next) {
  mode.value = next
  if (next !== 'site' || site.value || siteLoading.value) return
  siteError.value = ''
  siteLoading.value = true
  try {
    site.value = (await http.get(`/api/collections/${encodeURIComponent(target.value.id)}/export/static/preview`)).data
  } catch (err) {
    siteError.value = err?.message || 'Could not read this collection.'
  } finally {
    siteLoading.value = false
  }
}

const href = computed(() => preview.value && target.value
  ? `/api/collections/${encodeURIComponent(target.value.id)}/export?include_sources=${includeSources.value}`
  : '#')

async function open(collection) {
  target.value = collection
  includeSources.value = false
  mode.value = 'bundle'
  site.value = null
  siteError.value = ''
  confirmPublic.value = false
  includeModel.value = true
  chosen.value = {}
  addresses.value = null
  addressError.value = ''
  addressesSaved.value = false
  preview.value = null
  error.value = ''
  loading.value = true
  modal.open()
  try {
    preview.value = (await http.get(`/api/collections/${encodeURIComponent(collection.id)}/export/preview`)).data
  } catch (err) {
    error.value = err?.message || 'Could not read this collection.'
  } finally {
    loading.value = false
  }
}

defineExpose({ open })
</script>
