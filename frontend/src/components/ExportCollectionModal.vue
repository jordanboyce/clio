<template>
  <dialog :ref="modal.dialogRef" class="modal" aria-labelledby="export-collection-title" @close="modal.onClosed">
    <div class="modal-box max-w-md">
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
        Three files a static website can search by meaning with no server behind it: the passages, their vectors,
        and a manifest naming the model.
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
            <p class="text-xs text-base-content/65">
              The site must embed each query in the browser with <code>{{ site.browser_model }}</code>. The manifest
              says so, and carries a check that turns search by meaning off for any other model.
              About {{ formatBytes(site.passages_bytes) }} of text<span v-if="site.passage_files > 1"> in {{ site.passage_files }} files</span>
              and {{ formatBytes(site.vectors_bytes) }} of vectors.
            </p>
            <label v-if="site.site?.available" class="flex items-start gap-3 cursor-pointer">
              <input v-model="includeModel" name="include-model" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
              <span class="text-sm">
                Include the model, as a site ready to serve<span class="text-base-content/60"> (about {{ formatBytes(site.site.bytes) }} more)</span>
                <span class="block text-xs text-base-content/60">
                  The page, {{ site.site.model }} and the library that runs it, so the site loads nothing from
                  Hugging Face or a CDN. Works on a network that blocks both.
                </span>
              </span>
            </label>
            <p v-else-if="site.site?.reason" class="text-xs text-base-content/60">{{ site.site.reason }}</p>
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
            A Clio that embeds with the same model reuses the vectors as they are. Any other Clio embeds the
            text again with its own model.
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
              Needed to open originals or re-chunk later. Only share files you have the right to redistribute.
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

const siteHref = computed(() => site.value?.ready && confirmPublic.value && target.value
  ? `/api/collections/${encodeURIComponent(target.value.id)}/export/static?confirm_public=true` +
    (includeModel.value && site.value.site?.available ? '&site=true' : '')
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
