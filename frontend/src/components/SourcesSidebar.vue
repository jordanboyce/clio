<template>
  <div class="flex flex-col h-full">

    <!-- Control rail. Bare icon buttons, no heading bar: the panel names
         itself through the section labels below, the way every other
         document sidebar does. -->
    <div class="side-rail border-b border-base-300 flex-shrink-0 bg-base-100" role="toolbar" aria-label="Sources">
      <!-- The file explorer's toolbar, as in Obsidian: add, filter, sort,
           fold, and how to show the list. -->
      <button
        v-if="collectionStore.canEditCurrent"
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        :class="{ 'is-active': addSectionOpen }"
        @click="toggleAddSection"
        :title="`Add sources  (${modKeyLabel} U)`"
        aria-label="Add sources"
      >
        <FilePlus :size="16" />
      </button>
      <button
        v-if="collectionStore.canEditCurrent && webLinksAvailable"
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        @click="openAddLink"
        title="Add a link"
        aria-label="Add a link"
      >
        <Link :size="15" />
      </button>
      <button
        v-if="collectionStore.canEditCurrent"
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        :class="{ 'is-active': textPanelOpen }"
        @click="openAddText"
        title="Paste text"
        aria-label="Paste text"
      >
        <ClipboardPaste :size="15" />
      </button>
      <button
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        :class="{ 'is-active': filterOpen || docSearch }"
        :aria-pressed="filterOpen || !!docSearch"
        @click="toggleFilter"
        title="Filter sources  ( / )"
        aria-label="Filter sources"
      >
        <Search :size="15" />
      </button>
      <div class="dropdown dropdown-bottom">
        <button tabindex="0" class="side-icon-btn text-base-content/55 hover:text-base-content" title="Sort" aria-label="Sort sources" aria-haspopup="menu">
          <ArrowUpDown :size="15" />
        </button>
        <ul tabindex="0" class="dropdown-content menu menu-sm z-[70] w-44 rounded-box border border-base-300 bg-base-100 p-1.5 shadow">
          <li><button :class="{ 'menu-active': treeSort === 'name' }" @click="setTreeSort('name')">File name (A to Z)</button></li>
          <li><button :class="{ 'menu-active': treeSort === 'recent' }" @click="setTreeSort('recent')">Recently added</button></li>
        </ul>
      </div>
      <button
        v-if="viewMode === 'tree'"
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        @click="toggleCollapseAll"
        :title="allCollapsed ? 'Expand all folders' : 'Collapse all folders'"
        :aria-label="allCollapsed ? 'Expand all folders' : 'Collapse all folders'"
      >
        <component :is="allCollapsed ? ChevronsUpDown : ChevronsDownUp" :size="15" />
      </button>
      <button
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        @click="setViewMode(viewMode === 'tree' ? 'list' : 'tree')"
        :title="viewMode === 'tree' ? 'Show as a detailed list' : 'Show as folders'"
        :aria-label="viewMode === 'tree' ? 'Show as a detailed list' : 'Show as folders'"
      >
        <component :is="viewMode === 'tree' ? Rows3 : FolderTree" :size="15" />
      </button>
      <div class="flex-1"></div>
      <button
        class="side-icon-btn text-base-content/55 hover:text-base-content"
        @click="refreshAll"
        title="Refresh sources"
        aria-label="Refresh sources list"
      >
        <RefreshCw :size="14" :class="loading ? 'animate-spin' : ''" />
      </button>
      <span
        v-if="docTotal > 0"
        class="side-kbd tabular-nums text-base-content/40 pr-1.5"
        :aria-label="`${docTotal} source${docTotal === 1 ? '' : 's'}`"
      >{{ docTotal.toLocaleString() }}</span>
    </div>

    <!-- Filter field: opens from the rail (or `/`), Esc puts it away again -->
    <div v-show="filterOpen || docSearch" class="px-2 pt-2 pb-1.5 border-b border-base-300 flex-shrink-0">
      <div class="side-field">
        <Search :size="13" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
        <input
          ref="filterInput"
          v-model="docSearch"
          type="search"
          placeholder="Filter sources…"
          aria-label="Filter sources by filename"
          @keydown.esc.prevent="closeFilter"
        />
        <button
          v-if="docSearch"
          class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
          @click="docSearch = ''"
          aria-label="Clear filter"
        >
          <X :size="12" />
        </button>
      </div>
    </div>

    <!-- A model that no longer fits this collection's index blocks every new
         source; say so here, before someone tries to add one. -->
    <EmbeddingMismatchNotice
      v-if="collectionStore.currentCollectionId && collectionStore.canEditCurrent"
      class="m-2 flex-shrink-0"
      :collection-id="collectionStore.currentCollectionId"
    />
    <SupersedeSuggestions
      v-if="collectionStore.currentCollectionId && collectionStore.canEditCurrent"
      ref="supersedeSuggestions"
      class="flex-shrink-0"
      :collection-id="collectionStore.currentCollectionId"
      @changed="onSupersedeChanged"
    />

    <!-- Storage against the per-collection cap. Hidden until half the cap is
         used: a near-empty meter above every source list told people nothing
         they needed. Warning tone in the last 10%, error when full. -->
    <div
      v-if="statsStore.storageLimitBytes > 0 && collectionStore.canEditCurrent && statsStore.storagePercent >= 50"
      class="px-3 py-1.5 border-b border-base-300 flex-shrink-0"
      :title="storageTitle"
    >
      <div class="flex items-baseline justify-between text-[11px] leading-none">
        <span class="text-base-content/50">Storage</span>
        <span class="tabular-nums" :class="storageTone === 'error' ? 'text-error font-medium' : storageTone === 'warning' ? 'text-warning font-medium' : 'text-base-content/60'">
          {{ formatBytes(statsStore.storageBytes) }}<span class="text-base-content/40"> / {{ formatBytes(statsStore.storageLimitBytes) }}</span>
        </span>
      </div>
      <progress
        class="progress w-full h-1 mt-1.5"
        :class="storageTone === 'error' ? 'progress-error' : storageTone === 'warning' ? 'progress-warning' : 'progress-primary'"
        :value="Math.min(100, statsStore.storagePercent)"
        max="100"
        :aria-label="storageTitle"
      ></progress>
    </div>

    <!-- Scrollable body -->
    <div class="flex-1 overflow-y-auto">

      <!-- Add Sources section (collapsible) -->
      <div class="border-b border-base-300">
        <div class="p-1.5">
          <button
            class="side-row font-medium text-base-content/80 hover:text-base-content"
            :class="{ 'is-active': addSectionOpen, 'animate-pulse': ui.highlightAddSources }"
            :aria-expanded="addSectionOpen"
            @click="toggleAddSection"
          >
            <Plus :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Add sources</span>
            <span v-if="!addSectionOpen" class="side-kbd text-base-content/35">{{ modKeyLabel }} U</span>
            <ChevronDown v-else :size="13" class="flex-shrink-0 text-base-content/35" aria-hidden="true" />
          </button>
        </div>

        <!-- Read-only share: say so instead of offering controls that 403 -->
        <div
          v-if="addSectionOpen && !collectionStore.canEditCurrent"
          class="px-3 pb-3 text-xs text-base-content/55 space-y-2"
        >
          <div class="flex items-start gap-2">
            <Eye :size="12" class="mt-0.5 flex-shrink-0" aria-hidden="true" />
            <span v-if="collectionStore.currentIsPublished">
              This collection has been shared with everyone to read. Its owner keeps it up to date.
            </span>
            <span v-else>This collection is shared with you read-only. Its owner manages the sources.</span>
          </div>
          <button class="btn btn-outline btn-xs w-full gap-1" @click="$emit('clone-collection')">
            <Copy :size="11" aria-hidden="true" />
            Make my own copy
          </button>
        </div>

        <div v-show="addSectionOpen && collectionStore.canEditCurrent" class="px-3 pb-3 space-y-2">
          <!-- Governance reminder: sources are scanned and attributed -->
          <p v-if="userStore.aup.enabled || userStore.contentPolicyAction !== 'off'" class="text-[10px] leading-snug text-base-content/50">
            <template v-if="userStore.contentPolicyAction !== 'off'">Sources are scanned when added</template>
            <template v-if="userStore.contentPolicyAction !== 'off' && userStore.privateCollections"> and recorded against your identity</template>
            <template v-else-if="userStore.privateCollections">Sources are recorded against your identity</template>.
            <button v-if="userStore.aup.enabled" type="button" class="link link-hover" @click="userStore.openAup()">Acceptable use</button>
          </p>
          <!-- File/folder/link/text/record buttons -->
          <div class="flex flex-wrap gap-1.5">
            <template v-if="capabilities.native_file_picker === false">
              <!-- Headless/Docker: labels directly trigger hidden inputs (no JS click chain needed) -->
              <label
                for="sb-file-input"
                class="btn btn-primary btn-xs flex-1 gap-1 cursor-pointer"
                :class="{ 'btn-disabled pointer-events-none': indexing || isRecording }"
              >
                <FileText :size="12" />
                Files
              </label>
              <label
                for="sb-folder-input"
                class="btn btn-outline btn-xs flex-1 gap-1 cursor-pointer"
                :class="{ 'btn-disabled pointer-events-none': indexing || isRecording }"
              >
                <FolderOpen :size="12" />
                Folder
              </label>
              <input id="sb-file-input" type="file" multiple class="sr-only" @change="handleBrowserFiles" :disabled="indexing || isRecording" />
              <input id="sb-folder-input" type="file" webkitdirectory class="sr-only" @click="folderScanning = true" @change="handleBrowserFolder" @cancel="folderScanning = false" :disabled="indexing || isRecording" />
            </template>
            <template v-else>
              <!-- Desktop: buttons call native OS file picker via backend -->
              <button @click="openFilePicker" class="btn btn-primary btn-xs flex-1 gap-1" :disabled="indexing || isRecording">
                <FileText :size="12" />
                Files
              </button>
              <button @click="openFolderPicker" class="btn btn-outline btn-xs flex-1 gap-1" :disabled="indexing || isRecording">
                <FolderOpen :size="12" />
                Folder
              </button>
            </template>
            <button
              v-if="webLinksAvailable || gitAvailable"
              @click="toggleLinkPanel"
              class="btn btn-xs flex-1 gap-1"
              :class="linkPanelOpen ? 'btn-secondary' : 'btn-outline'"
              :disabled="indexing || isRecording"
              :aria-expanded="linkPanelOpen"
              :title="gitAvailable ? 'Index a web link or a git repository' : 'Index the content behind a link'"
            >
              <Link :size="12" />
              Link
            </button>
            <button
              @click="toggleTextPanel"
              class="btn btn-xs flex-1 gap-1"
              :class="textPanelOpen ? 'btn-secondary' : 'btn-outline'"
              :disabled="indexing || isRecording"
              :aria-expanded="textPanelOpen"
              title="Type or paste text as a source"
            >
              <ClipboardPaste :size="12" />
              Text
            </button>
            <button
              @click="toggleRecording"
              class="btn btn-xs flex-1 gap-1"
              :class="isRecording ? 'btn-error' : 'btn-outline btn-secondary'"
              :disabled="indexing || transcribing"
              :title="isRecording ? 'Stop recording' : 'Record a meeting'"
            >
              <Square v-if="isRecording" :size="10" class="fill-current" />
              <Mic v-else :size="12" />
              {{ isRecording ? 'Stop' : 'Record' }}
            </button>
          </div>

          <!-- Recording / transcription panel -->
          <div
            v-if="isRecording || transcribing || recordError"
            class="rounded border text-xs px-2 py-1.5"
            :class="recordError ? 'border-error/40 bg-error/10' : (isRecording ? 'border-error/40 bg-error/5' : 'border-base-300 bg-base-200')"
          >
            <div v-if="isRecording" class="flex items-center gap-2">
              <span class="inline-block w-2 h-2 rounded-full bg-error animate-pulse" aria-hidden="true"></span>
              <span class="flex-1 font-medium">Recording — {{ formattedElapsed }}</span>
              <button class="btn btn-ghost btn-xs" @click="cancelRecording" :aria-label="'Cancel recording'">Cancel</button>
            </div>
            <div v-else-if="transcribing" class="flex items-center gap-2">
              <span class="loading loading-spinner loading-xs"></span>
              <span class="flex-1">{{ transcribeStatus || 'Transcribing recording…' }}</span>
            </div>
            <div v-else-if="recordError" class="flex items-start gap-2">
              <span class="flex-1 text-error">{{ recordError }}</span>
              <button class="btn btn-ghost btn-xs" @click="recordError = ''">Dismiss</button>
            </div>
          </div>

          <!-- Link panel: paste one or more URLs; the server fetches each one
               and indexes the page (or the PDF/file behind it) in the background -->
          <div v-if="linkPanelOpen" class="space-y-1.5">
            <div v-if="webLinksAvailable && gitAvailable" class="join w-full" role="group" aria-label="What to add">
              <button type="button" class="btn btn-xs join-item flex-1" :class="activeLinkKind === 'web' ? 'btn-primary' : 'btn-ghost border border-base-300'" :aria-pressed="activeLinkKind === 'web'" @click="linkKind = 'web'">Web page</button>
              <button type="button" class="btn btn-xs join-item flex-1" :class="activeLinkKind === 'git' ? 'btn-primary' : 'btn-ghost border border-base-300'" :aria-pressed="activeLinkKind === 'git'" @click="linkKind = 'git'">Git repository</button>
            </div>
            <div v-if="activeLinkKind === 'git'" class="space-y-1.5" data-testid="git-panel">
              <input
                v-model="gitUrl"
                type="text"
                class="input input-bordered input-xs w-full font-mono"
                placeholder="https://git.example.com/team/repo.git"
                aria-label="Git repository address"
                :disabled="gitSubmitting"
                @keydown.enter.prevent="indexGit"
              />
              <div class="flex items-center gap-2">
                <input
                  v-model="gitBranch"
                  type="text"
                  class="input input-bordered input-xs flex-1 min-w-0 font-mono"
                  placeholder="Branch (default)"
                  aria-label="Branch"
                  :disabled="gitSubmitting"
                />
                <label class="flex items-center gap-1.5 text-xs cursor-pointer select-none" title="Check the repository every 15 minutes and tell me when it changes">
                  <input v-model="gitWatch" type="checkbox" class="toggle toggle-xs toggle-primary" :disabled="gitSubmitting" />
                  Watch
                </label>
              </div>
              <div class="flex items-center gap-2 text-xs">
                <span class="flex-1 text-base-content/50 leading-tight">Cloned on the server (read-only). Files in .gitignore are skipped. Private repositories use the server's git credentials.</span>
                <button class="btn btn-primary btn-xs gap-1 flex-shrink-0" @click="indexGit" :disabled="gitSubmitting || !gitUrl.trim()">
                  <span v-if="gitSubmitting" class="loading loading-spinner loading-xs"></span>
                  <GitBranch v-else :size="11" />
                  {{ gitSubmitting ? 'Cloning…' : 'Add repository' }}
                </button>
              </div>
            </div>
            <textarea
              v-else
              v-model="linkInput"
              class="textarea textarea-bordered textarea-xs w-full leading-snug font-mono"
              rows="3"
              placeholder="https://example.com/page&#10;One link per line"
              aria-label="Links to index, one per line"
              :disabled="linkSubmitting"
              @keydown.ctrl.enter.prevent="indexLinks"
              @keydown.meta.enter.prevent="indexLinks"
            ></textarea>
            <div v-if="activeLinkKind === 'web'" class="flex items-center gap-2 text-xs">
              <span class="flex-1 text-base-content/50 leading-tight">Public web pages, PDFs and files. Pages are saved and indexed as they are now.</span>
              <button class="btn btn-primary btn-xs gap-1 flex-shrink-0" @click="indexLinks" :disabled="linkSubmitting || parsedLinks.length === 0">
                <span v-if="linkSubmitting" class="loading loading-spinner loading-xs"></span>
                <Link v-else :size="11" />
                {{ linkSubmitting ? 'Adding…' : (parsedLinks.length > 1 ? `Add ${parsedLinks.length} links` : 'Add link') }}
              </button>
            </div>
          </div>

          <!-- Text panel: type or paste; the server keeps the formatting that
               matters and indexes the note like an uploaded HTML file -->
          <PastedTextEditor
            v-if="textPanelOpen"
            ref="textEditor"
            :busy="textSubmitting"
            @submit="addPastedText"
          />

          <!-- Folder enumeration hint: the browser walks the whole directory
               before the change event fires, which takes a while on huge trees -->
          <div v-if="folderScanning && selectedPaths.length === 0" class="flex items-center gap-2 text-xs text-base-content/60">
            <span class="loading loading-spinner loading-xs"></span>
            Reading folder…
          </div>

          <!-- Folders synced before: one click brings them up to date.
               Unchanged files are skipped; "prune" also drops documents whose
               file has gone from disk. -->
          <div v-if="syncFolders.length > 0 && selectedPaths.length === 0" class="space-y-0.5" data-testid="synced-folders">
            <div class="flex items-center justify-between px-0.5">
              <span class="side-label text-base-content/50">Synced sources</span>
            </div>
            <div
              v-for="folder in syncFolders"
              :key="folder.path"
              class="group flex items-center gap-1.5 text-xs rounded-md px-1 py-1 hover:bg-base-content/[0.05]"
            >
              <GitBranch v-if="folder.git" :size="12" class="flex-shrink-0 text-base-content/45" aria-hidden="true" />
              <FolderSync v-else :size="12" class="flex-shrink-0 text-base-content/45" aria-hidden="true" />
              <span class="flex-1 min-w-0">
                <span class="block truncate text-base-content/80" :title="folder.git_url || folder.path">{{ folder.label || folderLabel(folder.path) }}</span>
                <span class="block text-[10px] text-base-content/45 truncate" data-testid="source-state">
                  <template v-if="!folder.exists && !folder.git_url">Folder not found</template>
                  <template v-else-if="folder.last_error"><span class="text-error" :title="folder.last_error">Check failed: {{ folder.last_error }}</span></template>
                  <template v-else-if="folder.indexing">Indexing…</template>
                  <template v-else-if="folder.out_of_date"><span class="text-warning" :title="`${folder.stale_reason}. Sync brings the index up to date.`">Out of date: {{ folder.stale_reason }}</span><template v-if="folder.last_synced_at"> · synced {{ formatSyncTime(folder.last_synced_at) }}</template></template>
                  <template v-else-if="folder.last_synced_at">
                    <template v-if="folder.git && folder.git_branch">{{ folder.git_branch }}<template v-if="folder.git_head"> @ {{ folder.git_head.slice(0, 7) }}</template> · </template>Synced {{ formatSyncTime(folder.last_synced_at) }}<template v-if="checkedAfterSync(folder)"> · checked {{ formatSyncTime(folder.last_checked_at) }}</template><template v-if="folder.pruned_count"> · {{ folder.pruned_count }} pruned</template>
                  </template>
                </span>
              </span>
              <button
                class="btn btn-ghost btn-xs h-6 min-h-0 px-1.5"
                :class="folder.watch ? 'text-primary' : 'hover-reveal text-base-content/60'"
                :disabled="watchingPath === folder.path"
                :aria-pressed="!!folder.watch"
                :title="folder.watch ? 'Watching: checked automatically and changes appear under source updates. Click to stop.' : 'Watch this source: check it automatically and tell me when it changes'"
                :aria-label="folder.watch ? `Stop watching ${folder.label || folderLabel(folder.path)}` : `Watch ${folder.label || folderLabel(folder.path)}`"
                @click="toggleWatch(folder)"
              >
                <Eye v-if="folder.watch" :size="12" />
                <EyeOff v-else :size="12" />
              </button>
              <button
                class="btn btn-ghost btn-xs h-6 min-h-0 px-1.5"
                :class="folder.out_of_date ? 'text-warning' : 'hover-reveal'"
                :disabled="indexing || syncingPath === folder.path || (!folder.exists && !folder.git_url)"
                :title="folder.git_url ? 'Fetch the latest commits and index what changed' : `Index new and changed files in ${folder.path}`"
                data-testid="source-sync"
                @click="runFolderSync(folder, false)"
              >
                <span v-if="syncingPath === folder.path" class="loading loading-spinner loading-xs"></span>
                <template v-else>Sync</template>
              </button>
              <button
                v-if="!folder.git_url"
                class="btn btn-ghost btn-xs h-6 min-h-0 px-1.5 hover-reveal text-base-content/60"
                :disabled="indexing || syncingPath === folder.path || !folder.exists"
                title="Sync, and remove documents whose file was deleted from this folder"
                @click="runFolderSync(folder, true)"
              >Prune</button>
            </div>
          </div>

          <!-- Selected paths -->
          <div v-if="selectedPaths.length > 0" class="space-y-1.5">
            <div class="flex items-center justify-between">
              <span class="text-xs text-base-content/60">{{ selectedPaths.length.toLocaleString() }} selected</span>
              <button class="btn btn-ghost btn-xs" @click="clearAllPaths" :disabled="indexing">Clear</button>
            </div>

            <!-- compact path list. Only a preview renders: a 75k-file folder
                 selection must not become 75k DOM rows. -->
            <div class="max-h-24 overflow-y-auto space-y-0.5">
              <div v-for="(item, idx) in visibleSelectedPaths" :key="idx" class="flex items-center gap-1 text-xs">
                <span class="flex-1 truncate text-base-content/70" :title="item.path">{{ item.name }}</span>
                <button
                  class="btn btn-ghost btn-xs btn-circle p-0 w-5 h-5 min-h-0"
                  @click="removePath(idx)"
                  :disabled="indexing"
                  :aria-label="`Remove ${item.name} from selection`"
                >
                  <X :size="10" />
                </button>
              </div>
              <div v-if="selectedPaths.length > SELECTED_PREVIEW" class="text-xs text-base-content/45 px-1 py-0.5">
                …and {{ (selectedPaths.length - SELECTED_PREVIEW).toLocaleString() }} more
              </div>
            </div>

            <!-- Add button -->
            <div class="flex items-center text-xs">
              <button class="btn btn-primary btn-xs ml-auto gap-1" @click="indexFiles" :disabled="indexing || selectedPaths.length === 0">
                <span v-if="indexing" class="loading loading-spinner loading-xs"></span>
                <FileSearch v-else :size="11" />
                {{ indexing ? 'Adding...' : 'Add to Collection' }}
              </button>
            </div>

            <!-- Progress -->
            <div v-if="indexing" class="space-y-1">
              <template v-if="uploading">
                <div class="flex items-center gap-2 text-xs text-base-content/60">
                  <span class="loading loading-spinner loading-xs"></span>
                  Uploading {{ uploadingCount }} file{{ uploadingCount !== 1 ? 's' : '' }}…
                </div>
              </template>
              <template v-else>
                <progress class="progress progress-primary w-full h-1.5" :value="indexProgressPercent" max="100"></progress>
                <div v-if="currentIndexingFile" class="text-xs text-base-content/50 truncate">{{ currentIndexingFile }}</div>
              </template>
            </div>
          </div>

          <!-- Success -->
          <div v-if="indexSuccess" class="flex items-center gap-1.5 text-xs text-success bg-success/10 rounded px-2 py-1.5" role="status">
            <CheckCircle :size="12" aria-hidden="true" />
            <span v-if="indexResult.background">
              {{ indexResult.count.toLocaleString() }} {{ indexResult.noun || 'file' }}(s) queued — indexing runs in the background, you can close this tab
            </span>
            <span v-else>{{ indexResult.count }} file(s), {{ indexResult.chunks }} chunks</span>
            <button
              class="ml-auto btn btn-ghost btn-xs p-0 h-4 min-h-0"
              @click="indexSuccess = false"
              aria-label="Dismiss success message"
            >✕</button>
          </div>

          <!-- Error -->
          <div v-if="indexError" class="text-xs text-error bg-error/10 rounded px-2 py-1.5">
            {{ indexError }}
            <button class="ml-1 underline" @click="indexError = ''">Dismiss</button>
          </div>

        </div>
      </div>

      <!-- Applied Expertise section (collapsible) -->
      <div class="border-b border-base-300">
        <div class="p-1.5">
          <button
            class="side-row text-base-content/75 hover:text-base-content"
            :class="{ 'is-active': expertiseSectionOpen }"
            :aria-expanded="expertiseSectionOpen"
            aria-label="Toggle saved instructions section"
            @click="expertiseSectionOpen = !expertiseSectionOpen"
          >
            <BookOpen :size="15" class="flex-shrink-0" aria-hidden="true" />
            <span class="flex-1">Instructions</span>
            <span v-if="attachedPackIds.length > 0" class="side-kbd tabular-nums text-base-content/40">{{ attachedPackIds.length }}</span>
            <ChevronDown :size="13" class="flex-shrink-0 text-base-content/35 transition-transform" :class="expertiseSectionOpen ? 'rotate-180' : ''" aria-hidden="true" />
          </button>
        </div>

        <div v-show="expertiseSectionOpen" class="px-3 pb-3 space-y-2">
          <!-- Attached packs -->
          <div v-if="attachedPackIds.length === 0" class="text-xs text-base-content/50 py-1">
            None applied. Saved instructions shape every answer in this collection.
          </div>
          <div v-else class="space-y-1">
            <div
              v-for="pack in attachedPacks"
              :key="pack.id"
              class="flex items-center gap-1.5 bg-accent/10 border border-accent/20 rounded px-2 py-1.5"
            >
              <FileText :size="11" class="text-accent shrink-0" aria-hidden="true" />
              <span class="text-xs flex-1 truncate" :title="pack.name">{{ pack.name }}</span>
              <button
                v-if="collectionStore.canConfigureCurrent"
                class="btn btn-ghost btn-xs btn-circle text-error"
                :title="`Remove ${pack.name}`"
                :disabled="expertiseLoading"
                @click.stop="removeExpertisePack(pack.id)"
              >
                <X :size="11" />
              </button>
            </div>
          </div>

          <!-- Add pack dropdown -->
          <div v-if="availablePacks.length > 0 && collectionStore.canConfigureCurrent" class="dropdown w-full">
            <label
              tabindex="0"
              class="btn btn-outline btn-xs w-full gap-1"
              :class="{ 'btn-disabled': expertiseLoading }"
            >
              <Plus :size="11" />
              Apply instructions…
              <ChevronDown :size="11" class="ml-auto" />
            </label>
            <ul tabindex="0" class="dropdown-content z-[50] menu p-1 shadow-lg bg-base-100 border border-base-300 rounded-box w-full max-h-48 overflow-y-auto flex-nowrap">
              <li v-for="pack in availablePacks" :key="pack.id">
                <a class="text-xs py-1.5" @click.prevent="addExpertisePack(pack.id)">
                  <FileText :size="11" class="shrink-0" />
                  <span class="truncate">{{ pack.name }}</span>
                </a>
              </li>
            </ul>
          </div>
          <p v-else-if="!collectionStore.canConfigureCurrent" class="text-xs text-base-content/40">
            Only the owner of this collection can change its instructions.
          </p>
          <p v-else-if="expertiseStore.packs.length === 0" class="text-xs text-base-content/40">
            Nothing saved yet. Create instructions from More, then Saved instructions.
          </p>
          <p v-else class="text-xs text-base-content/40">
            Everything saved is already applied.
          </p>
        </div>
      </div>

      <!-- List header: a quiet label whose controls only appear on hover,
           which turns into the bulk bar once sources are selected. -->
      <div class="group flex items-center flex-wrap gap-x-1.5 gap-y-1.5 px-2.5 pt-2.5 pb-1 flex-shrink-0">
        <input
          v-if="documents.length > 0"
          type="checkbox"
          class="checkbox checkbox-xs flex-shrink-0"
          :class="{ 'hover-reveal': selectedDocuments.length === 0 }"
          :checked="isAllSelected"
          :indeterminate="selectedDocuments.length > 0 && !isAllSelected"
          @change="toggleSelectAll"
          :disabled="deleting"
          title="Select all sources"
          aria-label="Select all sources"
        />
        <span class="side-label text-base-content/45 flex-1 min-w-0 whitespace-nowrap" id="sources-list-heading">
          <template v-if="selectedDocuments.length > 0">
            <span class="text-base-content/80 tabular-nums">{{ selectedDocuments.length }} selected</span>
          </template>
          <template v-else>Sources</template>
        </span>
        <button
          v-if="selectedDocuments.length > 0"
          class="btn btn-ghost btn-xs px-1.5 font-normal"
          @click="selectedDocuments = []"
          :disabled="deleting"
          title="Clear selection — chat goes back to every source"
          aria-label="Clear selection"
        >Clear</button>
        <template v-if="selectedDocuments.length === 0">
          <button
            class="side-icon-btn side-icon-btn-sm hover-reveal text-base-content/50 hover:text-base-content"
            @click="openAddSources"
            title="Add sources"
            aria-label="Add sources"
          >
            <Plus :size="14" />
          </button>
          <button
            class="side-icon-btn side-icon-btn-sm hover-reveal text-base-content/50 hover:text-base-content"
            :class="{ 'is-active': filterOpen || docSearch }"
            @click="toggleFilter"
            title="Filter sources"
            aria-label="Filter sources"
          >
            <ListFilter :size="14" />
          </button>
        </template>
        <button
          v-if="selectedDocuments.length > 0"
          class="btn btn-xs btn-error gap-1"
          @click="confirmBulkDelete"
          :disabled="deleting"
          :aria-label="`Delete ${selectedDocuments.length} selected source${selectedDocuments.length === 1 ? '' : 's'}`"
        >
          <Trash2 :size="11" aria-hidden="true" />
          {{ selectedDocuments.length }}
        </button>
        <!-- Bulk sensitivity label for the selection -->
        <select
          v-if="selectedDocuments.length > 0 && collectionStore.canEditCurrent"
          class="select select-bordered select-xs w-28"
          :disabled="labelling"
          aria-label="Set sensitivity label for selected sources"
          @change="applyBulkLabel($event)"
        >
          <option value="">Label…</option>
          <option value="__inherit__">Use collection default</option>
          <option v-for="level in userStore.sensitivityLevels" :key="level" :value="level">{{ level }}</option>
        </select>
        <!-- What a selection means, on its own line so the row above stays one line -->
        <p
          v-if="selectedDocuments.length > 0"
          class="basis-full text-[10px] leading-snug text-base-content/50"
        >Chat and reports use only the selected sources.</p>
      </div>

      <!-- Type chips: what kinds of sources are here, and a one-tap filter.
           Counts come from the server so they describe the whole collection,
           not just the loaded page. Shown once there is more than one kind. -->
      <div
        v-if="kindChips.length > 1 || docKind"
        class="flex items-center gap-1 px-2.5 pb-1.5 flex-wrap"
        role="group"
        aria-label="Filter sources by type"
        data-testid="kind-chips"
      >
        <button
          type="button"
          class="btn btn-xs h-5 min-h-0 px-1.5 rounded-full font-normal gap-1"
          :class="!docKind ? 'btn-neutral' : 'btn-ghost text-base-content/60'"
          :aria-pressed="!docKind"
          @click="setDocKind('')"
        >All <span class="tabular-nums opacity-70">{{ kindTotal.toLocaleString() }}</span></button>
        <button
          v-for="chip in kindChips"
          :key="chip.kind"
          type="button"
          class="btn btn-xs h-5 min-h-0 px-1.5 rounded-full font-normal gap-1"
          :class="docKind === chip.kind ? 'btn-neutral' : 'btn-ghost text-base-content/60'"
          :aria-pressed="docKind === chip.kind"
          @click="setDocKind(chip.kind)"
        >
          <component :is="FAMILIES[chip.kind].icon" :size="10" aria-hidden="true" />
          {{ FAMILIES[chip.kind].label }} <span class="tabular-nums opacity-70">{{ chip.count.toLocaleString() }}</span>
        </button>
      </div>

      <!-- Loading spinner -->
      <div v-if="loading" class="flex justify-center py-8">
        <span class="loading loading-spinner loading-sm"></span>
      </div>

      <!-- Empty state -->
      <div v-else-if="documents.length === 0" class="py-8 px-4 text-center">
        <template v-if="justIndexed">
          <span class="loading loading-spinner loading-sm"></span>
          <p class="text-xs text-base-content/50 mt-2">Syncing sources…</p>
        </template>
        <p v-else-if="docSearch" class="text-xs text-base-content/50">No sources match “{{ docSearch }}”.</p>
        <p v-else-if="docKind" class="text-xs text-base-content/50">No {{ FAMILIES[docKind]?.label.toLowerCase() || docKind }} sources here.</p>
        <p v-else class="text-xs text-base-content/50">No sources yet. Use Add Sources above to get started.</p>
      </div>

      <!-- Folder tree (Obsidian / VS Code style) -->
      <div v-else-if="viewMode === 'tree'">
        <SourcesTree
          :tree="sourceTree"
          :collapsed="collapsedFolders"
          :selected-ids="selectedDocuments"
          :can-edit="collectionStore.canEditCurrent"
          @toggle="toggleSelect"
          @select="selectMany"
          @select-only="(ids) => (selectedDocuments = ids)"
          @toggle-folder="toggleFolder"
          @collapse-others="collapseOthers"
          @open="openOriginal"
          @chunks="openChunks"
          @replace="(doc) => (supersedeDoc = doc)"
          @report="openReport"
          @delete="confirmDelete"
          @injection="openInjectionWarnings"
          @policy="openPolicyFlags"
        />
        <p v-if="loadingMore && documents.length < docTotal" class="px-3 py-1.5 text-[11px] text-base-content/50 flex items-center gap-1.5">
          <span class="loading loading-spinner loading-xs"></span>
          Loading {{ documents.length.toLocaleString() }} of {{ docTotal.toLocaleString() }}…
        </p>
        <p v-if="treeTruncated" class="px-3 py-1.5 text-[11px] text-base-content/55" data-testid="tree-truncated">
          Showing the first {{ documents.length.toLocaleString() }} of {{ docTotal.toLocaleString() }} sources —
          filter to narrow it down, or switch to the detailed list.
        </p>
      </div>

      <!-- Document list -->
      <div v-else class="px-1.5 pb-2">
        <div
          v-for="doc in documents"
          :key="doc.document_id"
          class="group relative flex items-start gap-2 px-2 py-2 rounded-lg transition-colors hover:bg-base-content/5"
          :class="{ 'bg-base-content/[0.07]': isSelected(doc.document_id) }"
        >
          <!-- Checkbox -->
          <input
            type="checkbox"
            class="checkbox checkbox-xs mt-1 flex-shrink-0"
            :checked="isSelected(doc.document_id)"
            @change="toggleSelect(doc.document_id)"
            :disabled="deleting"
            :aria-label="`Select ${doc.filename}`"
          />

          <!-- File icon -->
          <div
            class="relative flex items-center justify-center w-7 h-7 rounded-md flex-shrink-0 mt-0.5"
            :class="fileInfo(doc).tile"
            :title="`${FAMILIES[fileInfo(doc).family].label}${fileInfo(doc).label ? ' · ' + fileInfo(doc).label : ''}`"
          >
            <component :is="fileInfo(doc).icon" :size="14" aria-hidden="true" />
          </div>

          <!-- Info -->
          <div class="flex-1 min-w-0">
            <div class="text-xs font-semibold truncate leading-tight" :title="isLinkDoc(doc) ? doc.source_path : doc.filename">{{ isNoteDoc(doc) ? noteTitle(doc) : doc.filename }}</div>
            <div class="flex items-center gap-1 mt-0.5 flex-wrap">
              <span
                v-if="fileInfo(doc).label"
                class="font-mono text-[10px] leading-none px-1 py-0.5 rounded bg-base-content/[0.07] text-base-content/70"
                :aria-label="`File type ${fileInfo(doc).label}`"
              >{{ fileInfo(doc).label }}</span>
              <span v-if="isCodeDoc(doc)" class="text-xs text-base-content/50 tabular-nums">{{ doc.total_chunks }} {{ doc.total_chunks === 1 ? 'symbol' : 'symbols' }}</span>
              <!-- Pages read the way people count them; the chunk count is an
                   indexing detail, kept in the tooltip for whoever wants it. -->
              <span
                v-else-if="sizeLabel(doc)"
                class="text-xs text-base-content/50 tabular-nums"
                :title="`${doc.total_chunks} indexed ${doc.total_chunks === 1 ? 'passage' : 'passages'}`"
              >{{ sizeLabel(doc) }}</span>
              <!-- Where it came from, only when that is not the usual upload:
                   a link already has the globe tile, so it needs no badge. -->
              <span
                v-if="doc.source_type === 'local_reference'"
                class="text-xs text-base-content/50"
                title="Read in place from a folder on the server. Clio keeps a reference, not a copy."
              >· folder</span>
              <span
                v-if="isNoteDoc(doc)"
                class="text-xs text-base-content/50"
                title="Typed or pasted into this collection. Click the eye to read it."
              >· pasted</span>
              <span
                v-if="isTabularFile(doc.filename)"
                class="badge badge-xs badge-success gap-0.5"
                title="Queryable as a typed SQL table — numeric questions run real SQL against this file"
              >
                <Table2 :size="9" />
                table
              </span>
              <button
                v-if="injectionBadge(doc)"
                class="badge badge-xs gap-0.5 cursor-pointer"
                :class="injectionBadge(doc).cls"
                @click.stop="openInjectionWarnings(doc)"
                :title="injectionBadge(doc).title"
              >
                <ShieldAlert :size="9" />
                {{ injectionBadge(doc).text }}
              </button>
              <!-- Sensitivity label in force (document override, else collection) -->
              <span
                v-if="showLabel(doc.sensitivity_effective)"
                class="badge badge-xs"
                :class="labelBadgeClass(doc.sensitivity_effective)"
                :title="`Sensitivity: ${doc.sensitivity_effective}${doc.sensitivity ? ' (set on this document)' : ' (collection default)'}`"
              >{{ doc.sensitivity_effective }}</span>
              <!-- Replaced by a newer document: quiet, informational -->
              <span
                v-if="replacedBadge(doc)"
                class="badge badge-xs badge-ghost gap-0.5 max-w-[10rem]"
                :title="replacedBadge(doc).title"
                data-testid="replaced-badge"
              >
                <Replace :size="9" aria-hidden="true" />
                <span class="truncate">{{ replacedBadge(doc).text }} · {{ replacedBadge(doc).name }}</span>
              </span>
              <!-- Content-policy outcome -->
              <button
                v-if="policyBadge(doc.policy_status)"
                class="badge badge-xs gap-0.5 cursor-pointer"
                :class="policyBadge(doc.policy_status).cls"
                @click.stop="openPolicyFlags(doc)"
                :title="policyBadge(doc.policy_status).title + ' Click for details.'"
              >
                <ShieldAlert :size="9" />
                {{ policyBadge(doc.policy_status).text }}
              </button>
            </div>
            <!-- Attribution: who added it (identity deployments only) -->
            <div
              v-if="userStore.privateCollections && doc.uploaded_by"
              class="text-[10px] text-base-content/40 truncate mt-0.5"
              :title="`Added by ${doc.uploaded_by}`"
            >by {{ doc.uploaded_by }}</div>
          </div>

          <!-- Action buttons (revealed on hover; always shown on touch) -->
          <div class="row-actions flex items-center flex-shrink-0 gap-0.5 hover-reveal">
            <button
              class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
              @click="openChunks(doc)"
              :disabled="deleting"
              title="View indexed chunks"
              :aria-label="`View indexed chunks for ${doc.filename}`"
            >
              <FileSearch :size="12" />
            </button>
            <button
              v-if="isNoteDoc(doc)"
              class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
              @click="openNote(doc)"
              title="Read the text"
              :aria-label="`Read ${noteTitle(doc)}`"
            >
              <Eye :size="12" />
            </button>
            <a
              v-else
              :href="isLinkDoc(doc) ? doc.source_path : `/documents/${doc.document_id}/pdf?collection_id=${collectionStore.currentCollectionId}`"
              target="_blank"
              rel="noopener noreferrer"
              class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
              :title="isLinkDoc(doc) ? 'Open the original link' : 'Open source document'"
              :aria-label="isLinkDoc(doc) ? `Open the original link for ${doc.filename} in a new tab` : `Open source document ${doc.filename} in a new tab`"
            >
              <Eye :size="12" />
            </a>
            <button
              v-if="collectionStore.canEditCurrent"
              class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
              @click="supersedeDoc = doc"
              :disabled="deleting"
              :title="doc.superseded_by ? 'Change or clear the replacement' : 'Mark as replaced by…'"
              :aria-label="`Mark ${doc.filename} as replaced by another source`"
              data-testid="replace-action"
            >
              <Replace :size="12" />
            </button>
            <button
              class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content"
              @click="openReport(doc)"
              :disabled="deleting"
              title="Report this source to an administrator"
              :aria-label="`Report ${doc.filename}`"
            >
              <Flag :size="12" />
            </button>
            <button
              class="side-icon-btn side-icon-btn-sm text-error/70 hover:text-error"
              @click="confirmDelete(doc)"
              :disabled="deleting || !collectionStore.canEditCurrent"
              :title="collectionStore.canEditCurrent ? 'Delete source' : 'This collection is shared with you read-only'"
              :aria-label="`Delete source ${doc.filename}`"
            >
              <Trash2 :size="12" />
            </button>
          </div>
        </div>

        <!-- Load more (paged: only the first pages are in the DOM) -->
        <div v-if="documents.length < docTotal" class="p-2 text-center">
          <button class="btn btn-xs btn-ghost" @click="loadMoreDocuments" :disabled="loadingMore">
            <span v-if="loadingMore" class="loading loading-spinner loading-xs"></span>
            Load more ({{ documents.length.toLocaleString() }} of {{ docTotal.toLocaleString() }})
          </button>
        </div>
      </div>

      <!-- Error -->
      <div v-if="error" class="mx-3 my-2 text-xs text-error bg-error/10 rounded px-2 py-1.5">{{ error }}</div>

    </div>

    <!-- Delete confirmation modal -->
    <dialog ref="deleteModal" class="modal" aria-labelledby="sidebar-delete-title">
      <div class="modal-box">
        <h3 id="sidebar-delete-title" class="font-bold text-lg">Confirm Delete</h3>
        <p v-if="documentToDelete" class="py-4">
          Delete <strong>{{ documentToDelete.filename }}</strong>?
          <span v-if="documentToDelete.source_type === 'local_reference'" class="block text-sm text-base-content/70 mt-2">
            Note: The original file will not be deleted, only the index entry.
          </span>
        </p>
        <p v-else class="py-4">Delete <strong>{{ selectedDocuments.length }} source(s)</strong>?</p>
        <div class="modal-action">
          <button class="btn" @click="closeDeleteModal" :disabled="deleting">Cancel</button>
          <button class="btn btn-error" @click="documentToDelete ? deleteDocument() : deleteBulk()" :disabled="deleting">
            <span v-if="deleting" class="loading loading-spinner"></span>
            {{ deleting ? 'Deleting...' : 'Delete' }}
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeDeleteModal">close</button></form>
    </dialog>

    <!-- Chunks viewer modal -->
    <dialog ref="chunksModal" class="modal" aria-labelledby="sidebar-chunks-title">
      <div class="modal-box max-w-6xl">
        <h3 id="sidebar-chunks-title" class="font-bold text-lg">Indexed Chunks</h3>
        <p v-if="chunkDocument" class="text-sm text-base-content/70 mt-1">{{ chunkDocument.filename }}</p>
        <div v-if="chunksLoading" class="flex justify-center py-10"><span class="loading loading-spinner loading-lg"></span></div>
        <div v-else-if="chunksError" class="alert alert-error mt-4"><XCircle :size="20" /><span>{{ chunksError }}</span></div>
        <div v-else class="mt-4 space-y-4">
          <div class="flex items-center justify-between gap-3 flex-wrap">
            <div class="flex gap-2 flex-wrap">
              <span class="badge badge-outline">{{ chunkResponse.returned_chunks }} shown</span>
              <span class="badge badge-outline">{{ chunkResponse.total_chunks }} total</span>
              <span class="badge badge-info">{{ chunkResponse.extraction_method || 'text' }}</span>
              <span class="badge badge-success">{{ chunksWithFieldsCount }} with fields</span>
              <span v-if="boostedChunkCount" class="badge badge-primary">{{ boostedChunkCount }} boosted</span>
            </div>
            <label class="label cursor-pointer gap-2 py-0">
              <span class="label-text text-sm">Only chunks with fields</span>
              <input type="checkbox" class="toggle toggle-sm" v-model="showOnlyChunksWithFields" />
            </label>
          </div>
          <div v-if="filteredChunks.length === 0" class="alert alert-info"><span>No chunks match the current filter.</span></div>
          <div v-else class="space-y-3 max-h-[60vh] overflow-y-auto pr-1">
            <div v-for="chunk in filteredChunks" :key="chunk.chunk_id" class="card bg-base-200 border border-base-300">
              <div class="card-body p-4">
                <div class="flex items-center justify-between flex-wrap gap-2">
                  <div class="font-mono text-xs text-base-content/60">{{ chunk.chunk_id }}</div>
                  <div class="flex gap-2">
                    <template v-if="chunk.symbol_name || chunk.line_start">
                      <span v-if="chunk.symbol_name" class="badge badge-sm badge-primary badge-outline font-mono gap-1" :title="chunk.symbol_type || 'symbol'">
                        <span class="opacity-60 font-sans">{{ chunk.symbol_type || 'symbol' }}</span>{{ chunk.symbol_name }}
                      </span>
                      <span v-else-if="chunk.symbol_type" class="badge badge-sm badge-ghost">{{ chunk.symbol_type.replace(/_/g, ' ') }}</span>
                      <span v-if="chunk.line_start" class="badge badge-sm badge-ghost tabular-nums">L{{ chunk.line_start }}<template v-if="chunk.line_end && chunk.line_end !== chunk.line_start">–{{ chunk.line_end }}</template></span>
                      <span v-if="chunk.language" class="badge badge-sm badge-ghost">{{ chunk.language }}</span>
                    </template>
                    <span v-else class="badge badge-sm">p{{ chunk.page_number }} c{{ chunk.chunk_index }}</span>
                    <span v-if="chunk.extraction_method === 'ocr'" class="badge badge-warning badge-sm">OCR</span>
                    <span v-else-if="chunk.extraction_method === 'hybrid'" class="badge badge-info badge-sm">Hybrid</span>
                    <span v-if="chunkFieldCount(chunk) > 0" class="badge badge-success badge-sm">{{ chunkFieldCount(chunk) }} fields</span>
                  </div>
                </div>
                <div v-if="chunk.entities && chunk.entities.length > 0" class="mt-2">
                  <span class="text-[10px] uppercase tracking-wide text-base-content/40">Entities</span>
                  <div class="flex flex-wrap gap-1.5 mt-1">
                    <span
                      v-for="entity in chunk.entities"
                      :key="entity"
                      class="badge badge-xs badge-outline text-base-content/70"
                    >{{ entity }}</span>
                  </div>
                </div>
                <div class="flex items-center gap-2 mt-2 pt-2 border-t border-base-300/50">
                  <span class="text-xs text-base-content/45 shrink-0">Rank</span>
                  <div class="flex items-center gap-0.5" role="group" aria-label="Chunk ranking bias">
                    <button class="btn btn-xs btn-ghost h-6 w-6 min-h-0 p-0" :disabled="boostingChunkId === chunk.chunk_id || (chunk.boost ?? 0) <= -1" title="Lower this chunk in retrieval" aria-label="Lower ranking bias" @click="nudgeChunkBoost(chunk, -0.25)"><ArrowDown :size="13" /></button>
                    <span class="inline-flex min-w-[3.25rem] justify-center font-mono text-xs tabular-nums" :class="boostTone(chunk.boost)">{{ formatBoost(chunk.boost) }}</span>
                    <button class="btn btn-xs btn-ghost h-6 w-6 min-h-0 p-0" :disabled="boostingChunkId === chunk.chunk_id || (chunk.boost ?? 0) >= 1" title="Raise this chunk in retrieval" aria-label="Raise ranking bias" @click="nudgeChunkBoost(chunk, 0.25)"><ArrowUp :size="13" /></button>
                  </div>
                  <button v-if="chunk.boost" class="btn btn-xs btn-ghost text-base-content/50" :disabled="boostingChunkId === chunk.chunk_id" title="Reset ranking bias" @click="nudgeChunkBoost(chunk, -(chunk.boost ?? 0))">Reset</button>
                  <span v-if="boostingChunkId === chunk.chunk_id" class="loading loading-spinner loading-xs"></span>
                </div>
                <div v-if="chunk.extracted_fields && Object.keys(chunk.extracted_fields).length > 0" class="overflow-x-auto">
                  <table class="table table-xs table-zebra">
                    <thead><tr><th>Field</th><th>Value</th></tr></thead>
                    <tbody>
                      <tr v-for="(value, field) in chunk.extracted_fields" :key="`${chunk.chunk_id}_${field}`">
                        <td class="font-semibold">{{ field }}</td><td class="break-all">{{ value }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <details>
                  <summary class="cursor-pointer text-sm font-medium text-primary">Show chunk text</summary>
                  <pre class="mt-2 text-xs whitespace-pre-wrap bg-base-100 p-3 rounded border border-base-300">{{ chunk.text }}</pre>
                </details>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-action"><button class="btn" @click="closeChunksModal">Close</button></div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeChunksModal">close</button></form>
    </dialog>

    <!-- Injection warnings modal -->
    <dialog ref="injectionModal" class="modal" aria-labelledby="injection-warnings-title">
      <div class="modal-box max-w-2xl">
        <h3 id="injection-warnings-title" class="font-bold text-lg flex items-center gap-2">
          <ShieldAlert :size="18" class="text-warning" aria-hidden="true" />
          Possible prompt injection
        </h3>
        <p v-if="injectionDoc" class="text-sm text-base-content/70 mt-1">{{ injectionDoc.filename }}</p>
        <p v-if="injectionDoc" class="text-sm mt-3">
          <template v-if="injectionDoc.injection_review">
            Someone already looked at this. The note stays on the source so the decision is reviewable, and it is no longer in the admin queue.
          </template>
          <template v-else>
            Some wording here resembles text that tries to steer an AI reading the document. The document stays searchable. A low-confidence match is usually ordinary prose or a quoted example.
          </template>
        </p>
        <div v-if="injectionDoc" class="mt-4 space-y-3 max-h-[50vh] overflow-y-auto pr-1">
          <div
            v-for="entry in injectionWarningPages(injectionDoc)"
            :key="entry.page"
            class="card bg-base-200 border border-warning/40"
          >
            <div class="card-body p-3">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-semibold text-sm">Page {{ entry.page }}</span>
                <span class="badge badge-sm badge-warning">score {{ entry.risk_score }}</span>
                <span class="badge badge-sm badge-outline">{{ entry.finding_count }} finding{{ entry.finding_count !== 1 ? 's' : '' }}</span>
              </div>
              <div class="space-y-2 mt-2">
                <div
                  v-for="(finding, idx) in entry.findings"
                  :key="idx"
                  class="rounded bg-base-100 border border-base-300 p-2 text-xs space-y-1"
                >
                  <div class="flex items-center gap-2 flex-wrap">
                    <span class="font-semibold capitalize">{{ finding.category.replace(/_/g, ' ') }}</span>
                    <span class="badge badge-xs" :class="{ 'badge-error': finding.severity === 'high', 'badge-warning': finding.severity === 'medium', 'badge-info': finding.severity === 'low' }">{{ finding.severity }}</span>
                    <span class="text-base-content/50 font-mono">{{ finding.pattern_name }}</span>
                  </div>
                  <div v-if="finding.matched_text" class="font-mono text-base-content/70 bg-base-200 rounded px-2 py-1 break-all">{{ finding.matched_text }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>
        <div class="modal-action">
          <button
            v-if="userStore.adminConsole && injectionDoc && !injectionDoc.injection_review"
            class="btn btn-sm btn-success btn-outline"
            :disabled="dismissingInjection"
            :title="'Dismisses the injection note only. A content hold or a report, if there is one, stays open.'"
            @click="dismissInjection"
          >
            <span v-if="dismissingInjection" class="loading loading-spinner loading-xs"></span>
            Looks fine
          </button>
          <button class="btn" @click="closeInjectionModal">Close</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeInjectionModal">close</button></form>
    </dialog>

    <!-- Content-policy findings modal -->
    <dialog ref="policyModal" class="modal" aria-labelledby="policy-flags-title">
      <div class="modal-box max-w-2xl">
        <h3 id="policy-flags-title" class="font-bold text-lg flex items-center gap-2">
          <ShieldAlert :size="18" :class="policyDoc?.policy_status === 'quarantined' ? 'text-error' : 'text-warning'" aria-hidden="true" />
          Content policy
        </h3>
        <template v-if="policyDoc">
          <p class="text-sm text-base-content/70 mt-1">{{ policyDoc.filename }}</p>
          <p class="text-sm mt-3">
            <template v-if="policyDoc.policy_status === 'quarantined'">
              This source is <strong>held for review</strong>. It stays indexed but is hidden from search, chat and MCP until an administrator approves it.
            </template>
            <template v-else-if="policyDoc.policy_status === 'flagged'">
              The scan <strong>flagged</strong> this source. It is still searchable; an administrator may review it.
            </template>
            <template v-else>
              This source was flagged and later <strong>approved</strong> by an administrator.
            </template>
          </p>
          <div v-if="policyDoc.policy_flags" class="mt-3 flex flex-wrap gap-1">
            <span
              v-for="(count, cat) in policyDoc.policy_flags.categories || {}"
              :key="cat"
              class="badge badge-sm badge-outline"
            >{{ categoryLabel(cat) }} · {{ count }}</span>
            <span v-if="policyDoc.policy_flags.critical" class="badge badge-sm badge-error">critical</span>
            <span v-if="policyDoc.policy_flags.llm?.categories?.length" class="badge badge-sm badge-info badge-outline">
              LLM: {{ policyDoc.policy_flags.llm.categories.map(categoryLabel).join(', ') }}
            </span>
          </div>
          <div class="mt-4 space-y-3 max-h-[50vh] overflow-y-auto pr-1">
            <div
              v-for="entry in policyFlagPages(policyDoc.policy_flags)"
              :key="entry.page"
              class="card bg-base-200 border border-base-300"
            >
              <div class="card-body p-3">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="font-semibold text-sm">Page {{ entry.page }}</span>
                  <span class="badge badge-sm badge-outline">score {{ entry.risk_score }}</span>
                </div>
                <div class="space-y-1 mt-2">
                  <div
                    v-for="(finding, idx) in entry.findings"
                    :key="idx"
                    class="rounded bg-base-100 border border-base-300 p-2 text-xs"
                  >
                    <span class="font-semibold capitalize">{{ categoryLabel(finding.category) }}</span>
                    <span class="badge badge-xs ml-1" :class="{ 'badge-error': finding.severity === 'critical' || finding.severity === 'high', 'badge-warning': finding.severity === 'medium', 'badge-info': finding.severity === 'low' }">{{ finding.severity }}</span>
                    <span class="text-base-content/50 font-mono ml-1">{{ finding.pattern_name }}</span>
                    <div v-if="finding.matched_text" class="font-mono text-base-content/70 bg-base-200 rounded px-2 py-1 mt-1 break-all">{{ finding.matched_text }}</div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>
        <div class="modal-action"><button class="btn" @click="closePolicyModal">Close</button></div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closePolicyModal">close</button></form>
    </dialog>

    <!-- Report modal -->
    <dialog ref="reportModal" class="modal" aria-labelledby="report-title">
      <div class="modal-box">
        <h3 id="report-title" class="font-bold text-lg flex items-center gap-2">
          <Flag :size="18" aria-hidden="true" />
          Report a source
        </h3>
        <p v-if="reportDoc" class="text-sm text-base-content/70 mt-1">{{ reportDoc.filename }}</p>
        <p class="text-sm mt-3">
          An administrator will be asked to review this source. Say what is wrong with it.
        </p>
        <textarea
          v-model="reportReason"
          class="textarea textarea-bordered w-full mt-3"
          rows="3"
          placeholder="e.g. Contains someone else's personal data / not something we should hold"
          aria-label="Reason for the report"
        ></textarea>
        <div class="modal-action">
          <button class="btn" @click="closeReportModal" :disabled="reporting">Cancel</button>
          <button class="btn btn-warning" @click="submitReport" :disabled="reporting">
            <span v-if="reporting" class="loading loading-spinner loading-xs"></span>
            Send report
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeReportModal">close</button></form>
    </dialog>

    <!-- Note reader: pasted text is stored as HTML and served as plain text
         (nothing from a source may run on this origin), so it is fetched and
         drawn here, cleaned, instead of opening as tags in a new tab. -->
    <dialog ref="noteModal" class="modal" aria-labelledby="sidebar-note-title">
      <div class="modal-box max-w-3xl">
        <h3 id="sidebar-note-title" class="font-bold text-lg">{{ noteDocument ? noteTitle(noteDocument) : 'Pasted text' }}</h3>
        <p v-if="noteDocument" class="text-xs text-base-content/60 mt-1">
          Pasted text<template v-if="noteDocument.indexed_at"> · added {{ new Date(noteDocument.indexed_at).toLocaleDateString() }}</template><template v-if="noteDocument.uploaded_by"> by {{ noteDocument.uploaded_by }}</template>
        </p>
        <div v-if="noteLoading" class="flex justify-center py-10"><span class="loading loading-spinner loading-lg"></span></div>
        <div v-else-if="noteError" class="alert alert-error mt-4"><XCircle :size="20" /><span>{{ noteError }}</span></div>
        <!-- eslint-disable-next-line vue/no-v-html -- cleaned by noteBodyHtml (DOMPurify allowlist) -->
        <div v-else class="rich-text mt-4 max-h-[65vh] overflow-y-auto pr-1 text-sm" data-testid="note-body" v-html="noteHtml"></div>
        <div class="modal-action">
          <button class="btn" @click="closeNote">Close</button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop"><button @click="closeNote">close</button></form>
    </dialog>

    <SupersedeDialog
      v-if="collectionStore.currentCollectionId"
      :doc="supersedeDoc"
      :collection-id="collectionStore.currentCollectionId"
      @close="supersedeDoc = null"
      @changed="onSupersedeChanged"
    />

  </div>
</template>

<script setup>
import { ref, computed, markRaw, nextTick, onMounted, onBeforeUnmount, watch } from 'vue'
import http from '../utils/http'
import EmbeddingMismatchNotice from './EmbeddingMismatchNotice.vue'
import SupersedeDialog from './SupersedeDialog.vue'
import SupersedeSuggestions from './SupersedeSuggestions.vue'
import SourcesTree from './SourcesTree.vue'
import PastedTextEditor from './PastedTextEditor.vue'
import { noteBodyHtml } from '../utils/richText'
import { buildTree, folderPaths, sortFiles } from '../utils/sourceTree.js'
import { replacedBadge } from '../utils/supersede'
import { FileText, Eye, Trash2, RefreshCw, X, ArrowUp, ArrowDown, FolderOpen, FileCode, FileSearch, CheckCircle, XCircle, Plus, ChevronDown, ShieldAlert, Table2, Mic, Square, BookOpen, Flag, Copy, Search, ListFilter, Link, Globe, FolderSync, GitBranch, EyeOff, Replace, FilePlus, ArrowUpDown, ChevronsDownUp, ChevronsUpDown, Rows3, FolderTree, ClipboardPaste } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useUiStore } from '../stores/uiStore'
import { useUserStore } from '../stores/userStore'
import { labelBadgeClass, showLabel, policyBadge, policyFlagPages, categoryLabel, injectionBadge } from '../utils/governance'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useExpertiseStore } from '../stores/expertiseStore'
import { useSelectionStore } from '../stores/selectionStore'
import { useLayoutStore } from '../stores/layoutStore'
import { useStatsStore } from '../stores/statsStore'
import { formatBytes, describeStorage } from '../utils/format'
import { describeFile, FAMILIES, isTabularFile } from '../utils/fileTypes'
import { useCustomFileTypesStore } from '../stores/customFileTypesStore'

// Registered custom types: loaded once, shared with the app shell.
const customTypes = useCustomFileTypesStore()
customTypes.load()

const emit = defineEmits(['document-deleted', 'open', 'background-job-started', 'clone-collection'])

const collectionStore = useCollectionStore()
const ui = useUiStore()
const userStore = useUserStore()
const backgroundJobsStore = useBackgroundJobsStore()
const expertiseStore = useExpertiseStore()

// Sidebar-specific state
const addSectionOpen = ref(true)
const expertiseSectionOpen = ref(false)
// Rail state: the filter field is hidden until asked for, so the panel head
// is nothing but icons at rest.
const filterOpen = ref(false)
const filterInput = ref(null)
const modKeyLabel = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '') ? '⌘' : 'Ctrl' 
const expertiseLoading = ref(false)

// Server capability flags (fetched on mount); native_file_picker=false → Docker/headless mode
const capabilities = ref({})

// Index state
const selectedPaths = ref([]) // Array of { path: string, name: string, isFolder?: boolean, size?: number, file?: File }
const folderScanning = ref(false) // browser is enumerating a picked directory
const SELECTED_PREVIEW = 50
const visibleSelectedPaths = computed(() => selectedPaths.value.slice(0, SELECTED_PREVIEW))
const indexing = ref(false)
const indexProgressPercent = ref(0)
const currentIndexingFile = ref('')
const indexSuccess = ref(false)
const indexError = ref('')
const indexResult = ref({ count: 0, chunks: 0 })
const uploading = ref(false)
const uploadingCount = ref(0)
const justIndexed = ref(false)

// Link panel: URLs pasted one per line (any whitespace separates them —
// a URL never contains one), de-duplicated before they are sent.
const linkPanelOpen = ref(false)
const linkInput = ref('')
const linkSubmitting = ref(false)
// The same panel adds a git repository (cloned on the server, optionally watched).
const linkKind = ref('web')
const webLinksAvailable = computed(() => capabilities.value.link_indexing !== false)
const gitAvailable = computed(() => !!capabilities.value.git_sources)
const activeLinkKind = computed(() => {
  if (linkKind.value === 'git' && gitAvailable.value) return 'git'
  if (linkKind.value === 'web' && webLinksAvailable.value) return 'web'
  return webLinksAvailable.value ? 'web' : 'git'
})
const gitUrl = ref('')
const gitBranch = ref('')
const gitWatch = ref(true)
const gitSubmitting = ref(false)
const parsedLinks = computed(() => [...new Set(linkInput.value.split(/\s+/).map(s => s.trim()).filter(Boolean))])
const isLinkDoc = (doc) => doc?.source_type === 'url' && /^https?:\/\//i.test(doc.source_path || '')

// Text panel: a note typed or pasted in, posted as HTML and indexed in the
// background like a link. The stored file is "<title>.html"; the list shows
// the title.
const textPanelOpen = ref(false)
const textSubmitting = ref(false)
const textEditor = ref(null)
const isNoteDoc = (doc) => doc?.source_type === 'text'
const noteTitle = (doc) => String(doc?.filename || '').replace(/\.html?$/i, '').replace(/_(\d+)$/, ' ($1)')
const noteModal = ref(null)
const noteDocument = ref(null)
const noteLoading = ref(false)
const noteError = ref('')
const noteHtml = ref('')

// Document management state
const documents = ref([])

// "Replaced by…" picker (SupersedeDialog) and the suggestions list share this
// refresh: the relationship changes how a row reads and how search ranks it.
const supersedeDoc = ref(null)
const supersedeSuggestions = ref(null)
const onSupersedeChanged = async () => {
  await loadDocuments()
  supersedeSuggestions.value?.load?.()
}

const loading = ref(false)
const deleting = ref(false)
const error = ref('')
const deleteModal = ref(null)
const documentToDelete = ref(null)
// The checkbox selection is the chat/report scope (stores/selectionStore):
// a computed with a setter so the existing bulk-action code keeps reading
// and assigning `selectedDocuments.value` unchanged.
const selectionStore = useSelectionStore()
const selectedDocuments = computed({
  get: () => selectionStore.currentIds,
  set: (ids) => selectionStore.set(ids),
})
const statsStore = useStatsStore()
const storageTone = computed(() => {
  const p = statsStore.storagePercent
  return p >= 100 ? 'error' : p >= 90 ? 'warning' : 'ok'
})
const storageTitle = computed(() => describeStorage(statsStore.storageBytes, statsStore.storageLimitBytes))
const chunksModal = ref(null)
const chunkDocument = ref(null)
const chunksLoading = ref(false)
const chunksError = ref('')
const showOnlyChunksWithFields = ref(false)
const chunkResponse = ref({
  document_id: '',
  filename: '',
  extraction_method: '',
  total_chunks: 0,
  returned_chunks: 0,
  chunks: []
})

// Meeting recording state
const isRecording = ref(false)
const transcribing = ref(false)
const transcribeStatus = ref('')
const recordError = ref('')
const elapsedSeconds = ref(0)
let mediaRecorder = null
let recordedChunks = []
let mediaStream = null
let elapsedTimer = null
let cancelled = false

const formattedElapsed = computed(() => {
  const mm = String(Math.floor(elapsedSeconds.value / 60)).padStart(2, '0')
  const ss = String(elapsedSeconds.value % 60).padStart(2, '0')
  return `${mm}:${ss}`
})

function pickRecordingMime() {
  // Prefer opus/webm (small, widely supported). Fall back to browser default.
  const candidates = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
  ]
  if (typeof MediaRecorder === 'undefined') return ''
  for (const mime of candidates) {
    if (MediaRecorder.isTypeSupported(mime)) return mime
  }
  return ''
}

function stopMediaTracks() {
  if (mediaStream) {
    mediaStream.getTracks().forEach(t => t.stop())
    mediaStream = null
  }
  if (elapsedTimer) {
    clearInterval(elapsedTimer)
    elapsedTimer = null
  }
}

async function startRecording() {
  if (!navigator.mediaDevices || typeof MediaRecorder === 'undefined') {
    recordError.value = 'Recording is not supported in this browser.'
    return
  }
  if (!collectionStore.canEditCurrent) {
    recordError.value = 'You do not have permission to add sources to this collection.'
    return
  }
  recordError.value = ''
  cancelled = false
  recordedChunks = []
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
  } catch (err) {
    console.error('getUserMedia failed:', err)
    recordError.value = err?.message?.includes('Permission')
      ? 'Microphone permission denied.'
      : 'Could not access microphone.'
    return
  }

  const mime = pickRecordingMime()
  try {
    mediaRecorder = mime
      ? new MediaRecorder(mediaStream, { mimeType: mime })
      : new MediaRecorder(mediaStream)
  } catch (err) {
    console.error('MediaRecorder init failed:', err)
    recordError.value = 'Failed to start recorder.'
    stopMediaTracks()
    return
  }

  mediaRecorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data)
  }
  mediaRecorder.onstop = async () => {
    stopMediaTracks()
    if (cancelled) {
      recordedChunks = []
      isRecording.value = false
      return
    }
    const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'audio/webm' })
    recordedChunks = []
    isRecording.value = false
    await uploadRecording(blob)
  }

  elapsedSeconds.value = 0
  elapsedTimer = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
  mediaRecorder.start()
  isRecording.value = true
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
}

function cancelRecording() {
  cancelled = true
  stopRecording()
}

function toggleRecording() {
  if (isRecording.value) stopRecording()
  else startRecording()
}

function extensionForMime(mime) {
  if (!mime) return 'webm'
  if (mime.includes('webm')) return 'webm'
  if (mime.includes('ogg')) return 'ogg'
  if (mime.includes('mp4')) return 'm4a'
  if (mime.includes('wav')) return 'wav'
  return 'webm'
}

async function uploadRecording(blob) {
  transcribing.value = true
  transcribeStatus.value = 'Uploading recording…'
  try {
    const ext = extensionForMime(blob.type)
    const now = new Date()
    const pad = (n) => String(n).padStart(2, '0')
    const stamp = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
    const filename = `meeting-${stamp}.${ext}`
    const file = new File([blob], filename, { type: blob.type })

    const form = new FormData()
    form.append('files', file)

    transcribeStatus.value = 'Transcribing with Whisper (may take a minute)…'
    const response = await http.post('/documents/upload', form, {
      params: { collection_id: collectionStore.currentCollectionId },
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 0, // extraction + Whisper can far exceed the default timeout
    })

    indexSuccess.value = true
    indexResult.value = {
      count: response.data.documents_processed || 1,
      chunks: response.data.total_chunks || 0,
    }
    await loadDocuments()
    emit('document-deleted')
  } catch (err) {
    console.error('Recording upload failed:', err)
    recordError.value = err?.message || 'Failed to transcribe recording'
  } finally {
    transcribing.value = false
    transcribeStatus.value = ''
  }
}

// Content-policy findings modal state
const policyModal = ref(null)
const policyDoc = ref(null)

function openPolicyFlags(doc) {
  policyDoc.value = doc
  policyModal.value?.showModal()
}

function closePolicyModal() {
  policyModal.value?.close()
}

// Report-a-source modal state
const reportModal = ref(null)
const reportDoc = ref(null)
const reportReason = ref('')
const reporting = ref(false)

function openReport(doc) {
  reportDoc.value = doc
  reportReason.value = ''
  reportModal.value?.showModal()
}

function closeReportModal() {
  reportModal.value?.close()
}

async function submitReport() {
  if (!reportDoc.value) return
  reporting.value = true
  try {
    const resp = await http.post(
      `/documents/${reportDoc.value.document_id}/report`,
      { reason: reportReason.value },
      { params: { collection_id: collectionStore.currentCollectionId } },
    )
    const n = resp.data?.admins_notified || 0
    ui.notify(n > 0 ? `Reported — ${n} administrator${n === 1 ? '' : 's'} notified.` : 'Reported for administrator review.', 'success')
    closeReportModal()
  } catch (err) {
    ui.toastError(err, 'Could not send the report')
  } finally {
    reporting.value = false
  }
}

// Bulk sensitivity relabel of the current selection
const labelling = ref(false)

async function applyBulkLabel(event) {
  const value = event.target.value
  event.target.value = ''
  if (!value || selectedDocuments.value.length === 0) return
  const sensitivity = value === '__inherit__' ? '' : value
  labelling.value = true
  try {
    for (const docId of selectedDocuments.value) {
      await http.patch(
        `/documents/${docId}/governance`,
        { sensitivity },
        { params: { collection_id: collectionStore.currentCollectionId } },
      )
    }
    ui.notify(`Labelled ${selectedDocuments.value.length} source${selectedDocuments.value.length === 1 ? '' : 's'}.`, 'success')
    await loadDocuments()
  } catch (err) {
    ui.toastError(err, 'Could not update the label')
  } finally {
    labelling.value = false
  }
}

// Injection warnings modal state
const injectionModal = ref(null)
const injectionDoc = ref(null)

function openInjectionWarnings(doc) {
  injectionDoc.value = doc
  injectionModal.value?.showModal()
}

function closeInjectionModal() {
  injectionModal.value?.close()
}

const dismissingInjection = ref(false)
async function dismissInjection() {
  const doc = injectionDoc.value
  if (!doc) return
  dismissingInjection.value = true
  try {
    await http.post(`/api/admin/documents/${collectionStore.currentCollectionId}/${doc.document_id}/approve`, { note: '', scope: 'injection' })
    doc.injection_review = 'dismissed'
    ui.notify(`Marked ${doc.filename} as reviewed.`, 'success')
    closeInjectionModal()
    await loadDocuments()
  } catch (err) {
    ui.toastError(err, 'Could not dismiss the warning')
  } finally {
    dismissingInjection.value = false
  }
}

function injectionWarningPages(doc) {
  if (!doc.injection_warnings) return []
  return Object.entries(doc.injection_warnings).map(([page, scan]) => ({ page: parseInt(page), ...scan }))
}

const chunksWithFieldsCount = computed(() => {
  return chunkResponse.value.chunks.filter(chunk => chunkFieldCount(chunk) > 0).length
})

const filteredChunks = computed(() => {
  if (!showOnlyChunksWithFields.value) {
    return chunkResponse.value.chunks
  }
  return chunkResponse.value.chunks.filter(chunk => chunkFieldCount(chunk) > 0)
})

const chunkFieldCount = (chunk) => {
  if (!chunk?.extracted_fields) return 0
  return Object.keys(chunk.extracted_fields).length
}

// Extract filename from path
const getFilename = (path) => {
  return path.split(/[/\\]/).pop()
}

// Handle files selected via browser <input type="file"> (headless/Docker mode).
// Items are markRaw'd (proxying File objects is pure overhead) and appended in
// ONE assignment — per-item reactive pushes froze the tab on a 75k-file folder.
const _appendBrowserSelection = (files, nameOf) => {
  const existingNames = new Set(selectedPaths.value.map(p => p.name))
  const additions = []
  for (const file of files) {
    const name = nameOf(file)
    if (!existingNames.has(name)) {
      additions.push(markRaw({ path: '', name, size: file.size, file }))
      existingNames.add(name)
    }
  }
  if (additions.length) selectedPaths.value = selectedPaths.value.concat(additions)
}

const handleBrowserFiles = (event) => {
  _appendBrowserSelection(Array.from(event.target.files || []), (f) => f.name)
  event.target.value = ''
}

// Handle folder selected via browser <input webkitdirectory> (headless/Docker mode)
const handleBrowserFolder = (event) => {
  folderScanning.value = false
  _appendBrowserSelection(
    Array.from(event.target.files || []),
    (f) => f.webkitRelativePath || f.name,
  )
  event.target.value = ''
}

// Open native OS file picker (desktop only — headless uses label/input directly in template)
const openFilePicker = async () => {
  try {
    indexError.value = ''
    const response = await http.post('/api/file-picker', null, {
      timeout: 0, // native dialog stays open until the user acts
      params: { multiple: true, include_sizes: true }
    })

    if (response.data.paths && response.data.paths.length > 0) {
      const existingPaths = new Set(selectedPaths.value.map(p => p.path))
      const sizes = response.data.sizes || {}
      for (const path of response.data.paths) {
        if (!existingPaths.has(path)) {
          selectedPaths.value.push({
            path: path,
            name: getFilename(path),
            size: sizes[path] || 0
          })
        }
      }
    }
  } catch (err) {
    console.error('File picker error:', err)
    indexError.value = err?.message || 'Failed to open file picker'
  }
}

// Open native OS folder picker (desktop only — headless uses label/input directly in template)
const openFolderPicker = async () => {
  try {
    indexError.value = ''
    const response = await http.post('/api/folder-picker', null, { timeout: 0 })

    if (response.data.path) {
      const folderPath = response.data.path

      // Scan folder for supported document types, add individual files
      // No extension filter: the server scans for everything it can index,
      // source code included, and skips what it cannot.
      const scanResponse = await http.post('/api/scan-folder', {
        path: folderPath,
        recursive: true,
      })

      if (scanResponse.data.files && scanResponse.data.files.length > 0) {
        const existingPaths = new Set(selectedPaths.value.map(p => p.path))
        for (const f of scanResponse.data.files) {
          if (!existingPaths.has(f.path)) {
            selectedPaths.value.push({
              path: f.path,
              name: f.relative_path || f.name
            })
          }
        }
      } else {
        indexError.value = customTypes.accepted.length
          ? `No supported files found in that folder (including ${customTypes.accepted.join(', ')}).`
          : 'No supported files found in that folder.'
      }
    }
  } catch (err) {
    console.error('Folder picker error:', err)
    indexError.value = err?.message || 'Failed to open folder picker'
  }
}

const removePath = (index) => {
  selectedPaths.value.splice(index, 1)
}

const clearAllPaths = () => {
  selectedPaths.value = []
  indexSuccess.value = false
  indexError.value = ''
}

// Main indexing function.
//
// Whatever was picked - browser files, server paths, folders - ends up as
// a background job on the server, and the only blocking work here is
// moving bytes. Files used to be indexed one at a time inside the request
// with the tab held open, folders started a job whose response this code
// misread as "0 files indexed", and picking both at once made the second
// submission fail with a 409. All three now land in the jobs drawer alike.
const indexFiles = async () => {
  if (selectedPaths.value.length === 0) return

  // Split browser-uploaded File objects from server-side path references
  const browserItems = selectedPaths.value.filter(p => p.file)
  const pathItems = selectedPaths.value.filter(p => !p.file)
  const files = pathItems.filter(p => !p.isFolder)
  const folders = pathItems.filter(p => p.isFolder)
  const collectionId = collectionStore.currentCollectionId

  indexing.value = true
  indexProgressPercent.value = 0
  currentIndexingFile.value = ''
  indexSuccess.value = false
  indexError.value = ''

  const errors = []
  let jobsStarted = 0
  let upToDate = 0
  let filesQueued = 0

  const describe = (err, fallback) => err?.message || fallback

  try {
    // Phase 1 - transfer. Browser File objects are streamed to the server
    // in batches, saved but not indexed. One request per batch keeps each
    // POST under proxy body-size caps (Cloudflare: 100MB) and response
    // timeouts, which is what makes very large folder drops work from the
    // browser at all. A failed batch is reported; the rest continue.
    const stagedPaths = []
    if (browserItems.length > 0) {
      const BATCH_MAX_FILES = 40
      const BATCH_MAX_BYTES = 25 * 1024 * 1024
      const batches = []
      let batch = []
      let batchBytes = 0
      for (const item of browserItems) {
        const size = item.size || item.file.size || 0
        if (batch.length && (batch.length >= BATCH_MAX_FILES || batchBytes + size > BATCH_MAX_BYTES)) {
          batches.push(batch)
          batch = []
          batchBytes = 0
        }
        batch.push(item)
        batchBytes += size
      }
      if (batch.length) batches.push(batch)

      uploading.value = true
      uploadingCount.value = browserItems.length
      let uploaded = 0
      try {
        for (const group of batches) {
          const formData = new FormData()
          for (const item of group) formData.append('files', item.file, item.name)
          formData.append('collection_id', collectionId)
          try {
            const response = await http.post('/documents/upload-staged', formData, {
              headers: { 'Content-Type': 'multipart/form-data' },
              timeout: 0, // large batches stream for as long as they need
            })
            for (const staged of response.data.staged || []) stagedPaths.push(staged.path)
            for (const f of response.data.failed || []) errors.push(`${f.filename}: ${f.error}`)
          } catch (err) {
            errors.push(`Upload of ${group.length} file(s) starting with ${group[0].name}: ` +
                        describe(err, 'transfer failed'))
          }
          uploaded += group.length
          uploadingCount.value = browserItems.length - uploaded
          indexProgressPercent.value = (uploaded / browserItems.length) * 100
          currentIndexingFile.value = group[group.length - 1].name
        }
      } finally {
        uploading.value = false
        uploadingCount.value = 0
      }
    }

    // Phase 2 - hand off. Staged uploads and server-side file paths are
    // the same thing to the indexer, so they go in as one job.
    const localPaths = [...stagedPaths, ...files.map(f => f.path)]
    if (localPaths.length > 0) {
      try {
        const { data } = await http.post('/documents/index-local-async', {
          file_paths: localPaths,
          collection_id: collectionId,
          copy_to_library: false,
        })
        backgroundJobsStore.addUploadJob(data)
        jobsStarted += 1
        filesQueued += data.total_files || localPaths.length
        for (const skipped of data.skipped_files || []) {
          errors.push(`${skipped.filename}: ${skipped.error}`)
        }
      } catch (err) {
        errors.push(describe(err, 'Could not start indexing'))
      }
    }

    // Phase 3 - folders. One job each, scanned and counted server-side.
    for (const folder of folders) {
      currentIndexingFile.value = folder.name
      try {
        const { data } = await http.post('/documents/upload-repo', {
          path: folder.path,
          collection_id: collectionId,
          recursive: true,
        }, { timeout: 0 }) // scanning a deep tree can take a while
        if (data.job_id) {
          backgroundJobsStore.addUploadJob({
            job_id: data.job_id,
            collection_id: collectionId,
            status: 'pending',
            total_files: (data.files_found || 0) - (data.skipped_unchanged || 0),
            processed_files: 0,
            progress_percent: 0,
            job_type: 'index',
          })
          jobsStarted += 1
          filesQueued += (data.files_found || 0) - (data.skipped_unchanged || 0)
          if (data.skipped_unchanged) ui.notify(`${folder.name}: ${data.skipped_unchanged.toLocaleString()} unchanged ${data.skipped_unchanged === 1 ? 'file' : 'files'} skipped`, 'info')
        } else if (data.status === 'up_to_date') {
          // Every file was already indexed: not an error, just nothing to do.
          ui.notify(`${folder.name} is up to date — ${(data.skipped_unchanged || 0).toLocaleString()} ${data.skipped_unchanged === 1 ? 'file' : 'files'} already indexed`, 'success')
          upToDate += 1
        } else {
          errors.push(`${folder.name}: ${data.message || 'no files matched'}`)
        }
        loadSyncFolders()
      } catch (err) {
        errors.push(`${folder.name}: ${describe(err, 'could not be added')}`)
      }
    }
  } finally {
    indexing.value = false
    currentIndexingFile.value = ''
    indexProgressPercent.value = 0
  }

  if (jobsStarted === 0 && upToDate > 0 && errors.length === 0) {
    selectedPaths.value = []
    addSectionOpen.value = false
    return
  }

  if (jobsStarted > 0) {
    indexSuccess.value = true
    indexResult.value = { count: filesQueued, jobs: jobsStarted, background: true }
    justIndexed.value = true
    addSectionOpen.value = false
    selectedPaths.value = []
    // The job may land its first documents before anyone looks again;
    // the store's throttled refresh tick keeps the list growing after this.
    loadDocuments()
    emit('document-deleted')
  }

  if (errors.length > 0) {
    indexError.value = errors.join('; ')
  } else {
    selectedPaths.value = []
  }
}

const indexLinks = async () => {
  const urls = parsedLinks.value
  if (urls.length === 0 || linkSubmitting.value) return
  const collectionId = collectionStore.currentCollectionId

  linkSubmitting.value = true
  indexSuccess.value = false
  indexError.value = ''
  try {
    const { data } = await http.post('/documents/index-links', { urls, collection_id: collectionId })
    backgroundJobsStore.addUploadJob(data)
    const skipped = (data.skipped_files || []).map(s => `${s.filename}: ${s.error}`)
    indexSuccess.value = true
    indexResult.value = { count: data.total_files || urls.length, jobs: 1, background: true, noun: 'link' }
    justIndexed.value = true
    linkInput.value = ''
    linkPanelOpen.value = false
    if (skipped.length > 0) {
      // Keep the panel open so the person sees which links were refused
      indexError.value = skipped.join('; ')
    } else {
      addSectionOpen.value = false
    }
    loadDocuments()
    emit('document-deleted')
  } catch (err) {
    indexError.value = err?.message || 'Could not add links'
  } finally {
    linkSubmitting.value = false
  }
}

const addPastedText = async ({ title, html }) => {
  if (textSubmitting.value) return
  const collectionId = collectionStore.currentCollectionId
  textSubmitting.value = true
  indexSuccess.value = false
  indexError.value = ''
  try {
    const { data } = await http.post('/documents/text', { title, html, collection_id: collectionId })
    backgroundJobsStore.addUploadJob(data)
    indexSuccess.value = true
    indexResult.value = { count: 1, jobs: 1, background: true, noun: 'note' }
    justIndexed.value = true
    textEditor.value?.clear()
    textPanelOpen.value = false
    addSectionOpen.value = false
    loadDocuments()
    emit('document-deleted')
  } catch (err) {
    // The text stays in the editor: nobody should have to paste it twice.
    indexError.value = err?.message || 'Could not add the text'
  } finally {
    textSubmitting.value = false
  }
}

const openNote = async (doc) => {
  noteDocument.value = doc
  noteLoading.value = true
  noteError.value = ''
  noteHtml.value = ''
  noteModal.value?.showModal()
  try {
    const { data } = await http.get(`/documents/${doc.document_id}/pdf`, {
      params: { collection_id: collectionStore.currentCollectionId },
      responseType: 'text',
      transformResponse: [(d) => d],
    })
    noteHtml.value = noteBodyHtml(data)
  } catch (err) {
    noteError.value = err?.message || 'Could not load the text'
  } finally {
    noteLoading.value = false
  }
}
const closeNote = () => {
  noteModal.value?.close()
  noteDocument.value = null
}

const indexGit = async () => {
  const url = gitUrl.value.trim()
  if (!url || gitSubmitting.value) return
  const collectionId = collectionStore.currentCollectionId
  gitSubmitting.value = true
  indexError.value = ''
  try {
    const { data } = await http.post('/documents/sync-git', {
      url,
      branch: gitBranch.value.trim() || null,
      collection_id: collectionId,
      watch: gitWatch.value,
    }, { timeout: 0 })
    if (data.job_id) {
      backgroundJobsStore.addUploadJob({
        job_id: data.job_id, collection_id: collectionId, status: 'pending',
        total_files: data.queued || 0, processed_files: 0, progress_percent: 0, job_type: 'index',
      })
      emit('background-job-started')
    }
    ui.notify(`${folderLabel(url.replace(/\.git$/, ''))}: ${data.queued ? `${data.queued.toLocaleString()} files queued` : 'up to date'}${gitWatch.value ? ' · watching for changes' : ''}`, data.job_id ? 'info' : 'success')
    gitUrl.value = ''
    gitBranch.value = ''
    linkPanelOpen.value = false
    addSectionOpen.value = false
    loadDocuments()
    emit('document-deleted')
  } catch (err) {
    // The server's reason (policy refusal, unreachable host) is the useful part.
    indexError.value = err?.message || 'Could not add the repository'
  } finally {
    gitSubmitting.value = false
    loadSyncFolders()
  }
}

const watchingPath = ref('')
const toggleWatch = async (folder) => {
  watchingPath.value = folder.path
  try {
    await http.post('/documents/sync-folders/watch', {
      collection_id: collectionStore.currentCollectionId,
      path: folder.path,
      watch: !folder.watch,
    })
    ui.notify(folder.watch ? 'No longer watching this source' : 'Watching: changes will show under source updates', 'success')
  } catch (err) {
    ui.toastError(err, 'Could not change watching')
  } finally {
    watchingPath.value = ''
    loadSyncFolders()
  }
}

// Document management functions
const isAllSelected = computed(() => {
  return documents.value.length > 0 && selectedDocuments.value.length === documents.value.length
})

const isSelected = (docId) => {
  return selectedDocuments.value.includes(docId)
}

const toggleSelect = (docId) => {
  selectionStore.toggle(docId)
}

const toggleSelectAll = () => {
  if (isAllSelected.value) {
    selectedDocuments.value = []
  } else {
    selectedDocuments.value = documents.value.map(doc => doc.document_id)
  }
}

// ── Tree view ────────────────────────────────────────────────────────────
// Folders come from where sources came from (synced folders keep their
// sub-folders, links group by site, uploads sit at the top). How the panel
// shows them, the sort and which folders are folded are per-browser
// preferences; the folding is kept per collection.
const readPref = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback } catch { return fallback } }
const writePref = (key, value) => { try { localStorage.setItem(key, value) } catch { /* a preference */ } }
const layoutStore = useLayoutStore()
const viewMode = computed(() => layoutStore.sourcesView)
const treeSort = ref(readPref('clio_sources_sort', 'name') === 'recent' ? 'recent' : 'name')
const setViewMode = (mode) => layoutStore.setSourcesView(mode)
watch(viewMode, (mode) => { if (mode === 'tree') loadTreeRemainder() })
const setTreeSort = (by) => {
  treeSort.value = by
  writePref('clio_sources_sort', by)
  document.activeElement?.blur()
}
// The collection's remembered sync folders (their roots name the tree's
// folders). Declared here, ahead of the tree that reads it.
const syncFolders = ref([])
const sourceTree = computed(() => sortFiles(buildTree(documents.value, syncFolders.value), treeSort.value))
const collapseKey = () => `clio_tree_collapsed:${collectionStore.currentCollectionId}`
// No saved folding yet: a small tree opens fully; a big one (a repository)
// opens one level deep, so the first look is the shape, not 5,000 rows.
let foldingChosen = false
const loadCollapsed = () => {
  try {
    const saved = localStorage.getItem(collapseKey())
    foldingChosen = saved !== null
    return new Set(JSON.parse(saved || '[]'))
  } catch {
    foldingChosen = false
    return new Set()
  }
}
const collapsedFolders = ref(loadCollapsed())
const BIG_TREE = 150
const saveCollapsed = () => {
  foldingChosen = true
  writePref(collapseKey(), JSON.stringify([...collapsedFolders.value]))
}
const toggleFolder = (path) => {
  const next = new Set(collapsedFolders.value)
  next.has(path) ? next.delete(path) : next.add(path)
  collapsedFolders.value = next
  saveCollapsed()
}
const allFolderPaths = computed(() => folderPaths(sourceTree.value))
watch(sourceTree, (tree) => {
  if (foldingChosen || tree.count <= BIG_TREE) return
  const topLevel = new Set(tree.folders.map(f => f.path))
  collapsedFolders.value = new Set(allFolderPaths.value.filter(p => !topLevel.has(p)))
})
const allCollapsed = computed(() => allFolderPaths.value.length > 0 &&
  allFolderPaths.value.every(p => collapsedFolders.value.has(p)))
const toggleCollapseAll = () => {
  collapsedFolders.value = allCollapsed.value ? new Set() : new Set(allFolderPaths.value)
  saveCollapsed()
}
const collapseOthers = (path) => {
  // Keep the folder and its ancestors open; fold every other folder.
  const keep = new Set(allFolderPaths.value.filter(p => path === p || path.startsWith(`${p}/`)))
  collapsedFolders.value = new Set(allFolderPaths.value.filter(p => !keep.has(p)))
  saveCollapsed()
}
const selectMany = (ids, select) => {
  const current = new Set(selectedDocuments.value)
  for (const id of ids) select ? current.add(id) : current.delete(id)
  selectedDocuments.value = [...current]
}
const openOriginal = (doc) => {
  if (isNoteDoc(doc)) return openNote(doc)
  const url = isLinkDoc(doc)
    ? doc.source_path
    : `/documents/${doc.document_id}/pdf?collection_id=${encodeURIComponent(collectionStore.currentCollectionId)}`
  window.open(url, '_blank', 'noopener')
}
watch(() => collectionStore.currentCollectionId, () => { collapsedFolders.value = loadCollapsed() })

// Paged loading: the sidebar only ever holds the pages the user has seen.
// A 75k-document collection would otherwise mean a ~30MB response and 75k
// DOM rows on every refresh.
const DOC_PAGE = 200
const docTotal = ref(0)
const docSearch = ref('')
const loadingMore = ref(false)
// Type filter (code / docs / data / media / other) and the server's per-kind
// counts for the current collection and filename filter.
const docKind = ref('')
const kindCounts = ref({})
const KIND_ORDER = ['code', 'docs', 'data', 'media', 'web', 'other']
const kindChips = computed(() =>
  KIND_ORDER.filter(k => (kindCounts.value[k] || 0) > 0 && FAMILIES[k]).map(k => ({ kind: k, count: kindCounts.value[k] }))
)
const kindTotal = computed(() => Object.values(kindCounts.value).reduce((a, b) => a + (b || 0), 0))
const setDocKind = (kind) => {
  if (docKind.value === kind) return
  docKind.value = kind
  loadDocuments()
}

// A folder tree is only right when it holds the whole collection: a
// folder showing 40 of its 900 files, or a tick that selects only the
// loaded ones, would mislead. In folder view the remaining pages load in
// bigger steps after the first, up to TREE_MAX; past that the panel says so.
const TREE_PAGE = 1000
const TREE_MAX = 10000
let loadGeneration = 0

const _docParams = (offset, limit = DOC_PAGE) => ({
  collection_id: collectionStore.currentCollectionId,
  limit,
  offset,
  ...(docSearch.value ? { q: docSearch.value } : {}),
  ...(docKind.value ? { kind: docKind.value } : {}),
})

const loadDocuments = async () => {
  loading.value = true
  error.value = ''
  const generation = ++loadGeneration

  try {
    const response = await http.get('/documents', { params: _docParams(0) })
    if (generation !== loadGeneration) return
    documents.value = response.data.documents || []
    docTotal.value = response.data.total_documents ?? documents.value.length
    if (response.data.kind_counts) kindCounts.value = response.data.kind_counts
    if (docTotal.value > 0) justIndexed.value = false
    if (docTotal.value > 0 && !addSectionTouched) addSectionOpen.value = false
  } catch (err) {
    error.value = err?.message || 'Failed to load sources'
  } finally {
    loading.value = false
  }
  if (generation === loadGeneration && viewMode.value === 'tree') loadTreeRemainder(generation)
}

const loadTreeRemainder = async (generation = loadGeneration) => {
  while (generation === loadGeneration && documents.value.length < Math.min(docTotal.value, TREE_MAX)) {
    loadingMore.value = true
    try {
      const response = await http.get('/documents', { params: _docParams(documents.value.length, TREE_PAGE) })
      if (generation !== loadGeneration) return
      const page = response.data.documents || []
      if (!page.length) break
      documents.value = documents.value.concat(page)
      docTotal.value = response.data.total_documents ?? docTotal.value
    } catch (err) {
      error.value = err?.message || 'Failed to load more sources'
      break
    } finally {
      loadingMore.value = false
    }
  }
}
const treeTruncated = computed(() => viewMode.value === 'tree' && docTotal.value > TREE_MAX && documents.value.length >= TREE_MAX)

const loadMoreDocuments = async () => {
  loadingMore.value = true
  try {
    const response = await http.get('/documents', { params: _docParams(documents.value.length) })
    documents.value = documents.value.concat(response.data.documents || [])
    docTotal.value = response.data.total_documents ?? docTotal.value
  } catch (err) {
    error.value = err?.message || 'Failed to load more sources'
  } finally {
    loadingMore.value = false
  }
}

let _docSearchTimer = null
watch(docSearch, () => {
  clearTimeout(_docSearchTimer)
  _docSearchTimer = setTimeout(loadDocuments, 250)
})

// ── Rail controls ────────────────────────────────────────────────────────
// Each of these can be reached while the panel is collapsed (the component
// stays mounted at width 0), so they ask the shell to open it first.
const openFilter = async () => {
  emit('open')
  filterOpen.value = true
  await nextTick()
  filterInput.value?.focus()
}
const closeFilter = () => {
  filterOpen.value = false
  docSearch.value = ''
}
const toggleFilter = () => {
  if (filterOpen.value || docSearch.value) closeFilter()
  else openFilter()
}
const openAddSources = () => {
  emit('open')
  addSectionOpen.value = true
  addSectionTouched = true
  ui.highlightAddSources = false
}
// The add form opens by itself only for an empty collection; once there
// are sources, the list is what the panel is for.
let addSectionTouched = false
const toggleAddSection = () => {
  addSectionTouched = true
  addSectionOpen.value = !addSectionOpen.value
  ui.highlightAddSources = false
}
const openAddLink = () => {
  openAddSources()
  linkPanelOpen.value = true
  textPanelOpen.value = false
}
const openAddText = () => {
  openAddSources()
  textPanelOpen.value = true
  linkPanelOpen.value = false
}
// One panel at a time under the buttons: the sidebar is narrow.
const toggleLinkPanel = () => {
  linkPanelOpen.value = !linkPanelOpen.value
  if (linkPanelOpen.value) textPanelOpen.value = false
}
const toggleTextPanel = () => {
  textPanelOpen.value = !textPanelOpen.value
  if (textPanelOpen.value) linkPanelOpen.value = false
}
const refreshAll = () => {
  loadDocuments()
  loadSyncFolders()
}

// `/` focuses the filter — the thing this panel is for. Ctrl/⌘+U (Add
// sources) is bound by the app shell, which asks this panel to open the
// flow through the `clio:add-sources` event so it works from any tab.
const onSidebarKeydown = (e) => {
  const el = e.target
  const typing = el instanceof HTMLElement && (
    el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
  )
  if (e.key === '/' && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault()
    openFilter()
  }
}

const openChunks = async (doc) => {
  chunkDocument.value = doc
  chunksLoading.value = true
  chunksError.value = ''
  showOnlyChunksWithFields.value = false
  chunkResponse.value = {
    document_id: doc.document_id,
    filename: doc.filename,
    extraction_method: '',
    total_chunks: 0,
    returned_chunks: 0,
    chunks: []
  }

  chunksModal.value?.showModal()

  try {
    const collectionId = collectionStore.currentCollectionId
    const response = await http.get(
      `/documents/${doc.document_id}/chunks`,
      {
        params: {
          collection_id: collectionId,
          include_fields: true
        }
      }
    )
    chunkResponse.value = response.data
  } catch (err) {
    chunksError.value = err?.message || 'Failed to load document chunks'
  } finally {
    chunksLoading.value = false
  }
}

const closeChunksModal = () => {
  chunksModal.value?.close()
}

const boostingChunkId = ref(null)

const formatBoost = (b) => {
  if (b == null) return '0.0'
  return (b > 0 ? '+' : '') + b.toFixed(2)
}
const boostTone = (b) => {
  if (b == null) return 'text-base-content/40'
  return b > 0 ? 'text-primary' : 'text-error'
}
const boostedChunkCount = computed(() =>
  chunkResponse.value.chunks.filter((c) => c.boost).length
)

// Manual ranking intervention: a small stepper that raises/lowers a chunk's
// bias by 0.25 steps (clamped to [-1, 1]); reset posts 0, which clears it.
// Reflects optimistically in the already-loaded chunk; the server clamps
// and audits the value.
async function applyChunkBoost(chunk, boost) {
  if (!chunk || boostingChunkId.value) return
  boostingChunkId.value = chunk.chunk_id
  try {
    const collectionId = collectionStore.currentCollectionId
    const response = await http.post(
      `/documents/${chunkDocument.value.document_id}/chunks/${chunk.chunk_id}/boost`,
      { boost, note: '' },
      { params: { collection_id: collectionId } }
    )
    chunk.boost = response.data.boost
    if (chunk.boost === 0) chunk.boost = null
  } catch (err) {
    ui.toastError(err, 'Could not set chunk boost')
  } finally {
    boostingChunkId.value = null
  }
}

function nudgeChunkBoost(chunk, delta) {
  const current = chunk.boost ?? 0
  const next = Math.max(-1, Math.min(1, Math.round((current + delta) * 100) / 100))
  applyChunkBoost(chunk, next)
}

// What each source is, for the tile and the type chips. The family
// boundaries are the server's (services/file_kinds.py); this only draws.
const fileInfo = (doc) => describeFile(doc?.filename, { isWeb: isLinkDoc(doc), isNote: isNoteDoc(doc) })

// What the extractor calls a "page" is only a page for PDFs: a Markdown or
// text file is split at headings, a deck by slide, a spreadsheet by row
// groups. Name the unit the reader would recognise; tables say nothing here
// because their "table" badge is the useful fact.
const sizeLabel = (doc) => {
  const n = doc?.total_pages || 0
  if (!n || isTabularFile(doc.filename)) return ''
  const ext = (doc.filename || '').split('.').pop().toLowerCase()
  const unit = ext === 'pdf' ? 'page' : ext === 'pptx' ? 'slide' : 'section'
  return `${n.toLocaleString()} ${unit}${n === 1 ? '' : 's'}`
}
const isCodeDoc = (doc) => fileInfo(doc).family === 'code'

const confirmDelete = (doc) => {
  documentToDelete.value = doc
  deleteModal.value?.showModal()
}

const closeDeleteModal = () => {
  if (!deleting.value) {
    deleteModal.value?.close()
    documentToDelete.value = null
  }
}

const deleteDocument = async () => {
  if (!documentToDelete.value) return

  deleting.value = true
  error.value = ''

  try {
    const collectionId = collectionStore.currentCollectionId
    await http.delete(`/documents/${documentToDelete.value.document_id}?collection_id=${collectionId}`)

    documents.value = documents.value.filter(
      doc => doc.document_id !== documentToDelete.value.document_id
    )

    selectionStore.remove([documentToDelete.value.document_id])

    emit('document-deleted')
  } catch (err) {
    error.value = err?.message || 'Failed to delete source'
  } finally {
    deleting.value = false
    closeDeleteModal()
  }
}

const confirmBulkDelete = () => {
  documentToDelete.value = null
  deleteModal.value?.showModal()
}

const deleteBulk = async () => {
  if (selectedDocuments.value.length === 0) return

  deleting.value = true
  error.value = ''

  try {
    const collectionId = collectionStore.currentCollectionId
    for (const docId of selectedDocuments.value) {
      await http.delete(`/documents/${docId}?collection_id=${collectionId}`)
    }

    documents.value = documents.value.filter(
      doc => !selectedDocuments.value.includes(doc.document_id)
    )

    selectedDocuments.value = []
    emit('document-deleted')
  } catch (err) {
    error.value = err?.message || 'Failed to delete sources'
  } finally {
    deleting.value = false
    closeDeleteModal()
  }
}

// Watch for collection changes
// Folders synced into this collection before (server-side memory).
const syncingPath = ref('')
const folderLabel = (path) => {
  const parts = String(path || '').split(/[\\/]/).filter(Boolean)
  return parts.slice(-2).join('/') || path
}
const formatSyncTime = (iso) => {
  const ts = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`).getTime()
  if (!Number.isFinite(ts)) return ''
  const mins = Math.max(0, Math.floor((Date.now() - ts) / 60000))
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return days < 7 ? `${days}d ago` : new Date(ts).toLocaleDateString()
}
// A watched source is checked more often than it changes: say so, so a row
// that reads "synced 3d ago" is not mistaken for one nobody looked at.
const checkedAfterSync = (folder) => {
  if (!folder.watch || !folder.last_checked_at || !folder.last_synced_at) return false
  return new Date(folder.last_checked_at).getTime() - new Date(folder.last_synced_at).getTime() > 60_000
}
const loadSyncFolders = async () => {
  if (!collectionStore.canEditCurrent) { syncFolders.value = []; return }
  try {
    const { data } = await http.get('/documents/sync-folders', { params: { collection_id: collectionStore.currentCollectionId } })
    syncFolders.value = data.folders || []
  } catch { syncFolders.value = [] }
}
const runFolderSync = async (folder, prune) => {
  const collectionId = collectionStore.currentCollectionId
  syncingPath.value = folder.path
  try {
    const { data } = await http.post('/documents/sync-folder', {
      path: folder.path,
      collection_id: collectionId,
      recursive: folder.recursive !== false,
      file_extensions: folder.file_extensions || null,
      exclude_patterns: folder.exclude_patterns || null,
      prune_missing: prune,
    }, { timeout: 0 })
    const bits = []
    if (data.queued) bits.push(`${data.queued.toLocaleString()} ${data.queued === 1 ? 'file' : 'files'} queued`)
    if (data.replaced_count) bits.push(`${data.replaced_count} replaced`)
    if (data.pruned_count) bits.push(`${data.pruned_count} removed`)
    if (data.skipped_unchanged) bits.push(`${data.skipped_unchanged.toLocaleString()} unchanged`)
    if (data.job_id) {
      backgroundJobsStore.addUploadJob({
        job_id: data.job_id, collection_id: collectionId, status: 'pending',
        total_files: data.queued || 0, processed_files: 0, progress_percent: 0, job_type: 'index',
      })
      emit('background-job-started')
    }
    ui.notify(`${folderLabel(folder.path)}: ${bits.join(' · ') || 'up to date'}`, data.job_id ? 'info' : 'success')
    if (data.pruned_count || data.replaced_count) {
      await loadDocuments()
      emit('document-deleted')
    }
  } catch (err) {
    ui.toastError(err, 'Folder sync failed')
  } finally {
    syncingPath.value = ''
    loadSyncFolders()
  }
}

watch(() => collectionStore.currentCollectionId, (newId) => {
  docKind.value = ''
  kindCounts.value = {}
  loadDocuments()
  loadSyncFolders()
  // The selection is kept per collection (selectionStore), so switching
  // collections shows that collection's own selection rather than wiping it.
  loadExpertise(newId)
})

// Applied Expertise helpers

const attachedPackIds = computed(() =>
  expertiseStore.attachedPackIds[collectionStore.currentCollectionId] || []
)

const attachedPacks = computed(() =>
  expertiseStore.getAttachedPackObjects(collectionStore.currentCollectionId)
)

const availablePacks = computed(() =>
  expertiseStore.packs.filter(p => !attachedPackIds.value.includes(p.id))
)

// Choosing which instructions a collection follows is the owner's call (the
// server answers 403 to anyone else, including a read-write collaborator).
function expertiseError(err, fallback) {
  if (err?.status === 403) {
    return new Error('Only the owner of this collection can change which instructions it follows.')
  }
  return new Error(err?.message || fallback)
}

async function loadExpertise(collId) {
  const id = collId || collectionStore.currentCollectionId
  try {
    await Promise.all([
      expertiseStore.fetchPacks(),
      expertiseStore.fetchAttached(id),
    ])
  } catch (e) {
    // non-fatal
  }
}

async function addExpertisePack(packId) {
  expertiseLoading.value = true
  try {
    const newIds = [...attachedPackIds.value, packId]
    await expertiseStore.setAttached(collectionStore.currentCollectionId, newIds)
  } catch (err) {
    ui.toastError(expertiseError(err, 'Could not apply these instructions'))
  } finally {
    expertiseLoading.value = false
  }
}

async function removeExpertisePack(packId) {
  expertiseLoading.value = true
  try {
    const newIds = attachedPackIds.value.filter(id => id !== packId)
    await expertiseStore.setAttached(collectionStore.currentCollectionId, newIds)
  } catch (err) {
    ui.toastError(expertiseError(err, 'Could not remove these instructions'))
  } finally {
    expertiseLoading.value = false
  }
}

// Live refresh while a job is indexing into this collection (throttled in
// the jobs store): re-pull the first page + total so the badge and list grow
// mid-job. Quiet — no loading spinner, and any load-more position
// intentionally resets to page one. docSearch is preserved via _docParams.
watch(() => backgroundJobsStore.dataRefreshTick, async () => {
  if (backgroundJobsStore.dataRefreshCollectionId !== collectionStore.currentCollectionId) return
  try {
    const response = await http.get('/documents', { params: _docParams(0) })
    documents.value = response.data.documents || []
    docTotal.value = response.data.total_documents ?? documents.value.length
    if (response.data.kind_counts) kindCounts.value = response.data.kind_counts
    if (docTotal.value > 0) justIndexed.value = false
  } catch {
    // transient mid-job failure — the next throttled tick retries
  }
})

// Watch for completed background uploads to reload documents
watch(() => backgroundJobsStore.uploadJobs, (jobs) => {
  const completedJob = jobs.find(j => j.status === 'completed' && !j.reloaded)
  if (completedJob) {
    completedJob.reloaded = true
    loadDocuments()
    emit('document-deleted')
  }
}, { deep: true })

// Sources added from another surface (the sample set in an empty chat)
watch(() => ui.sourcesVersion, () => { loadDocuments() })

// Watch for completed reindex jobs to reload documents
watch(() => backgroundJobsStore.reindexJob?.status, (newStatus, oldStatus) => {
  if (newStatus === 'completed' && oldStatus && oldStatus !== 'completed') {
    loadDocuments()
    emit('document-deleted')
  }
})

// Warn user before leaving page during indexing
const beforeUnloadHandler = (e) => {
  // Only the transfer of bytes from this browser is at risk from leaving.
  // Indexing itself runs server-side and survives the tab, so warning
  // about it just trained people to ignore the dialog.
  if (uploading.value || isRecording.value || transcribing.value) {
    e.preventDefault()
    e.returnValue = isRecording.value
      ? 'Recording in progress. Are you sure you want to leave?'
      : 'Files are still uploading. Are you sure you want to leave?'
    return e.returnValue
  }
}

onMounted(async () => {
  loadDocuments()
  loadExpertise()
  loadSyncFolders()
  window.addEventListener('beforeunload', beforeUnloadHandler)
  window.addEventListener('keydown', onSidebarKeydown)
  window.addEventListener('clio:add-sources', openAddSources)
  try {
    const resp = await http.get('/api/capabilities')
    capabilities.value = resp.data
  } catch {
    // leave capabilities empty; native picker stays as default
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('beforeunload', beforeUnloadHandler)
  window.removeEventListener('keydown', onSidebarKeydown)
  window.removeEventListener('clio:add-sources', openAddSources)
  if (isRecording.value) {
    cancelled = true
    try { mediaRecorder?.stop() } catch (_) { /* ignore */ }
  }
  stopMediaTracks()
})
</script>

<style scoped>
/* With a mouse, a source's actions float over the end of its row instead of
   holding a column open: the column was invisible until hover yet still
   truncated every title to about half the panel. Touch screens have no hover,
   so they keep the inline buttons (.hover-reveal shows them permanently). */
@media (hover: hover) {
  .row-actions {
    position: absolute;
    top: 0.375rem;
    right: 0.375rem;
    padding: 0.125rem;
    border-radius: 0.5rem;
    border: 1px solid var(--color-base-300);
    background: var(--color-base-100);
    box-shadow: 0 1px 3px rgb(0 0 0 / 0.12);
    pointer-events: none;
  }
  /* Hidden buttons must not catch a click aimed at the title underneath. */
  .group:hover .row-actions,
  .group:focus-within .row-actions { pointer-events: auto; }
}
</style>
