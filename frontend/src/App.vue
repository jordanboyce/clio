<template>
  <div class="h-dvh flex flex-col overflow-hidden bg-base-200" :class="{ 'select-none cursor-col-resize': isResizing || isResizingAnalysis }">

    <!-- ── Header ── -->
    <header class="flex items-center gap-2 px-3 h-11 bg-base-100 border-b border-base-300 flex-shrink-0 z-50">

      <!-- The panel toggle is the first thing in the window and never moves,
           on every tab — no label, no brand competing with it for the corner. -->
      <button
        class="side-icon-btn text-base-content/60 hover:text-base-content"
        :class="{ 'is-active': sourcesPanelShown }"
        @click="toggleSources"
        :title="sourcesPanelShown ? 'Hide sources panel' : 'Show sources panel'"
        :aria-label="sourcesPanelShown ? 'Hide sources panel' : 'Show sources panel'"
        :aria-pressed="sourcesPanelShown"
      >
        <component :is="sourcesPanelShown ? PanelLeftClose : PanelLeft" :size="16" />
      </button>

      <!-- Scope on the left, next to the sources panel it controls; the AI
           model sits on the right. They used to share a corner and blur together. -->
      <CollectionPicker
        :collections="collectionStore.sortedCollections"
        :current="collectionStore.currentCollection"
        :overview="isCollectionsView"
        @select="pickCollection"
        @overview="activeTab = 'collections'"
        @create="openCreateCollectionModal"
      />

      <WorkspaceNav
        v-if="!isCollectionsView"
        class="hidden md:flex"
        :active-tab="activeTab"
        :chat-enabled="chatTabEnabled"
        :admin-console="userStore.adminConsole"
        :admin-badge="reviewStore.total"
        @navigate="switchTab"
        @notes="openNotes"
      />

      <!-- Spacer -->
      <div class="flex-1"></div>

      <!-- User identity (private-collections mode) -->
      <div v-if="userStore.isPrivateMode" class="hidden md:flex items-center gap-1.5 text-xs text-base-content/50">
        <Users :size="12" />
        <span class="max-w-24 truncate">{{ userStore.displayName }}</span>
      </div>

      <!-- Phone: active-job indicator (the status footer is hidden there) -->
      <button
        v-if="backgroundJobsStore.hasActiveJobs"
        class="md:hidden btn btn-ghost btn-circle btn-sm"
        @click="showJobsDrawer = true"
        :title="`${backgroundJobsStore.activeJobCount} active job${backgroundJobsStore.activeJobCount === 1 ? '' : 's'}`"
        :aria-label="`Background jobs — ${backgroundJobsStore.activeJobCount} active`"
      >
        <Loader2 :size="16" class="animate-spin text-warning" aria-hidden="true" />
      </button>

      <!-- The one AI choice: provider and model for Ask, Find and Reports.
           Phones get the provider list in the overflow menu instead. -->
      <ModelPicker class="hidden md:block" @connect="activeTab = 'settings'" />

      <!-- Notes panel toggle: the right-hand twin of the sources one -->
      <!-- v-if, not `hidden md:inline-flex`: .side-icon-btn sets display in
           the unlayered stylesheet, which beats Tailwind's layered utility. -->
      <button
        v-if="!isCompact"
        class="side-icon-btn text-base-content/60 hover:text-base-content"
        :class="{ 'is-active': notesPanelShown }"
        @click="toggleNotes"
        :title="notesPanelShown ? 'Hide notes and tools' : 'Show notes and tools'"
        :aria-label="notesPanelShown ? 'Hide notes and tools' : 'Show notes and tools'"
        :aria-pressed="notesPanelShown"
      >
        <component :is="notesPanelShown ? PanelRightClose : PanelRight" :size="16" />
      </button>

      <!-- Review notifications (admins only): held/flagged content, injection
           warnings, user reports and access requests waiting for a decision -->
      <ReviewBell variant="header" @open="openReview" />

      <!-- Help menu (desktop; phones get it in the overflow menu) -->
      <div class="dropdown dropdown-end hidden md:block">
        <button tabindex="0" class="btn btn-ghost btn-circle btn-sm" title="Help" aria-label="Help menu" aria-haspopup="menu">
          <HelpCircle :size="16" />
        </button>
        <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-50 w-56 p-2 shadow border border-base-300">
          <li>
            <a href="https://github.com/jordanboyce/clio#readme" target="_blank" rel="noopener">
              <BookOpen :size="14" />
              Documentation
            </a>
          </li>
          <li>
            <button @click="showPalette = true">
              <Command :size="14" />
              Command palette
              <span class="ml-auto flex items-center gap-0.5" aria-hidden="true"><span class="kbd-hint">{{ MOD }}</span><span class="kbd-hint">K</span></span>
            </button>
          </li>
          <li>
            <button @click="showShortcuts = true">
              <Keyboard :size="14" />
              Keyboard shortcuts
              <span class="ml-auto kbd-hint" aria-hidden="true">?</span>
            </button>
          </li>
          <li>
            <button @click="showOnboarding = true">
              <Sparkles :size="14" />
              Run setup again
            </button>
          </li>
          <li>
            <button @click="showAbout = true">
              <Info :size="14" />
              About Clio
            </button>
          </li>
        </ul>
      </div>

      <!-- Settings button -->
      <button
        class="hidden md:inline-flex btn btn-ghost btn-circle btn-sm"
        :class="{ 'bg-base-300': activeTab === 'settings' }"
        @click="activeTab = 'settings'"
        title="Settings"
        aria-label="Settings"
        :aria-current="activeTab === 'settings' ? 'page' : undefined"
      >
        <Settings :size="16" />
      </button>

      <!-- Signed-in identity + sign out (Cloudflare Access deployments only) -->
      <div v-if="me.authenticated_via === 'cloudflare-access'" class="dropdown dropdown-end hidden md:block">
        <button tabindex="0" class="btn btn-ghost btn-circle btn-sm" :title="me.identity || 'Signed in'" aria-label="Account menu">
          <CircleUser :size="16" />
        </button>
        <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-50 w-64 p-2 shadow border border-base-300">
          <li class="menu-title"><span class="truncate">{{ me.identity || 'Signed in' }}</span></li>
          <li>
            <a :href="me.logout_url" class="text-error">
              <LogOut :size="14" />
              Sign out
            </a>
          </li>
        </ul>
      </div>

      <!-- Phone: one overflow menu holds what the desktop header shows inline -->
      <div class="dropdown dropdown-end md:hidden">
        <button tabindex="0" class="btn btn-ghost btn-circle btn-sm" title="More" aria-label="More options" aria-haspopup="menu">
          <EllipsisVertical :size="18" />
        </button>
        <ul tabindex="0" class="dropdown-content menu bg-base-100 rounded-box z-[60] w-64 p-2 shadow-lg border border-base-300 max-h-[70vh] overflow-y-auto flex-nowrap">
          <li v-if="me.identity || userStore.isPrivateMode" class="menu-title">
            <span class="truncate">{{ me.identity || userStore.displayName }}</span>
          </li>
          <li>
            <button :class="{ 'active': activeTab === 'settings' }" @click="activeTab = 'settings'; closeMenus()">
              <Settings :size="14" />
              Settings
            </button>
          </li>
          <li>
            <button @click="showJobsDrawer = true; closeMenus()">
              <Loader2 v-if="backgroundJobsStore.hasActiveJobs" :size="14" class="animate-spin text-warning" aria-hidden="true" />
              <Bell v-else :size="14" aria-hidden="true" />
              Background jobs
              <span v-if="backgroundJobsStore.activeJobCount > 0" class="badge badge-warning badge-xs ml-auto">{{ backgroundJobsStore.activeJobCount }}</span>
            </button>
          </li>
          <li v-if="userStore.adminConsole">
            <button @click="openReview('review'); closeMenus()">
              <ShieldAlert v-if="reviewStore.total > 0" :size="14" class="text-warning" aria-hidden="true" />
              <ShieldCheck v-else :size="14" aria-hidden="true" />
              Review queue
              <span v-if="reviewStore.total > 0" class="badge badge-warning badge-xs ml-auto">{{ reviewStore.total }}</span>
            </button>
          </li>
          <template v-if="providerPill.configured.length > 0">
            <li class="menu-title pt-2"><span class="text-xs">Default AI provider</span></li>
            <li v-for="pid in providerPill.configured" :key="'m-' + pid">
              <button class="flex items-center gap-2" :class="{ 'active': pid === providerPill.id }" @click="setGlobalProvider(pid)">
                <Check v-if="pid === providerPill.id" :size="12" class="flex-shrink-0" aria-hidden="true" />
                <span v-else class="w-3 flex-shrink-0" aria-hidden="true"></span>
                <span class="flex-1 text-left text-sm truncate">{{ providerDisplayName(pid) }}</span>
              </button>
            </li>
          </template>
          <li class="menu-title pt-2"><span class="text-xs">Help</span></li>
          <li>
            <a href="https://github.com/jordanboyce/clio#readme" target="_blank" rel="noopener">
              <BookOpen :size="14" />
              Documentation
            </a>
          </li>
          <li>
            <button @click="showOnboarding = true; closeMenus()">
              <Sparkles :size="14" />
              Run setup again
            </button>
          </li>
          <li>
            <button @click="showAbout = true; closeMenus()">
              <Info :size="14" />
              About Clio
            </button>
          </li>
          <li v-if="me.authenticated_via === 'cloudflare-access'">
            <a :href="me.logout_url" class="text-error">
              <LogOut :size="14" />
              Sign out
            </a>
          </li>
        </ul>
      </div>
    </header>

    <!-- ── Body ── -->
    <div class="flex flex-1 overflow-hidden min-h-0 relative">

      <!-- Compact mode: side panels overlay the main surface instead of
           squeezing it, so the answer column keeps the full window width.
           This backdrop closes whichever panel is open. -->
      <div
        v-if="isWorkspaceView && isCompact && (sourcesSidebarOpen || analysisSidebarOpen)"
        class="absolute inset-0 z-30 bg-base-content/20"
        @click="sourcesSidebarOpen = false; analysisSidebarOpen = false"
        aria-hidden="true"
      ></div>

      <!-- Left sidebar: sources (resizable, hidden on collections overview) -->
      <aside
        v-show="isWorkspaceView"
        class="bg-base-100 overflow-hidden flex flex-col"
        :class="[
          isResizing ? '' : 'transition-all duration-200 ease-in-out',
          isCompact ? 'absolute left-0 top-0 h-full z-40 shadow-2xl' : 'flex-shrink-0 relative',
        ]"
        :style="{ width: sourcesSidebarOpen ? effectiveSidebarWidth + 'px' : '0px' }"
      >
        <!-- Sidebar content fixed to sidebarWidth so it doesn't shrink during close animation -->
        <div class="h-full flex flex-col" :style="{ width: effectiveSidebarWidth + 'px' }">
          <SourcesSidebar
            @document-deleted="handleDocumentDeleted"
            @background-job-started="showJobsDrawer = true"
            @clone-collection="collectionStore.currentCollection && startClone(collectionStore.currentCollection)"
            @open="sourcesSidebarOpen = true"
          />
        </div>

        <!-- Drag handle (pointless in compact overlay mode) -->
        <div
          v-if="sourcesSidebarOpen && !isCompact"
          class="absolute right-0 top-0 h-full w-1 cursor-col-resize group z-10 hover:bg-primary/40 transition-colors"
          :class="isResizing ? 'bg-primary/60' : ''"
          @mousedown.prevent="startResize"
        >
          <!-- Visual grip dots -->
          <div class="absolute inset-y-0 right-0 flex items-center justify-center w-1">
            <div class="flex flex-col gap-1 opacity-0 group-hover:opacity-60 transition-opacity">
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
            </div>
          </div>
        </div>
      </aside>

      <!-- Main panel -->
      <main class="flex-1 flex flex-col overflow-hidden min-w-0">

        <!-- Tab content -->
        <div
          class="flex-1 min-h-0"
          :class="activeTab === 'chat' ? 'overflow-hidden p-0' : 'overflow-y-auto'"
        >
          <!-- Chat gets full height, no padding wrapper -->
          <div v-if="activeTab === 'chat'" class="h-full p-3 md:p-4">
            <ChatTab @switch-tab="switchTab" @show-sources="sourcesSidebarOpen = true" @add-sources="openAddSources" />
          </div>

          <!-- All other tabs: padded scroll container -->
          <div v-else class="px-4 py-4 md:px-6 md:py-6">

            <!-- Collections overview -->
            <div v-if="activeTab === 'collections'" class="pt-2 md:pt-4">
              <header class="flex items-end justify-between gap-6 flex-wrap pb-5 mb-4 border-b border-base-300/60">
                <div class="min-w-0">
                  <div class="flex items-center gap-2 mb-3">
                    <span class="brand-art brand-art-mark h-6 w-6 flex-shrink-0" aria-hidden="true"></span>
                    <span class="text-[11px] font-semibold uppercase tracking-[0.16em] text-base-content/45">Clio</span>
                  </div>
                  <h1 class="text-[22px] leading-none font-semibold tracking-tight">Collections</h1>
                  <p class="mt-2 text-xs text-base-content/50">
                    <span class="tabular-nums font-medium text-base-content/70">{{ collectionStore.sortedCollections.length }}</span>
                    {{ collectionStore.sortedCollections.length === 1 ? 'collection' : 'collections' }}
                    <span class="mx-1.5 text-base-content/25">·</span>
                    <span class="text-base-content/45">workspace for your sources</span>
                  </p>
                </div>

                <div class="flex items-center gap-2 flex-wrap w-full sm:w-auto">
                  <!-- Search -->
                  <div class="relative w-full sm:w-auto">
                    <Search :size="13" class="absolute left-2.5 top-1/2 -translate-y-1/2 text-base-content/40 pointer-events-none" />
                    <input
                      v-model="collectionsSearch"
                      type="text"
                      placeholder="Search collections"
                      class="input input-sm input-bordered pl-7 pr-7 w-full sm:w-56 sm:focus:w-64 transition-[width]"
                      aria-label="Search collections"
                    />
                    <button
                      v-if="collectionsSearch"
                      @click="collectionsSearch = ''"
                      class="absolute right-2 top-1/2 -translate-y-1/2 text-base-content/40 hover:text-base-content transition-colors"
                      aria-label="Clear search"
                    >
                      <X :size="13" />
                    </button>
                  </div>

                  <!-- View toggle -->
                  <div class="join" role="group" aria-label="View mode">
                    <button
                      class="btn btn-sm join-item"
                      :class="collectionsView === 'list' ? 'btn-active' : 'btn-ghost'"
                      @click="collectionsView = 'list'"
                      title="List view"
                      aria-label="List view"
                      :aria-pressed="collectionsView === 'list'"
                    >
                      <List :size="14" />
                    </button>
                    <button
                      class="btn btn-sm join-item"
                      :class="collectionsView === 'cards' ? 'btn-active' : 'btn-ghost'"
                      @click="collectionsView = 'cards'"
                      title="Card view"
                      aria-label="Card view"
                      :aria-pressed="collectionsView === 'cards'"
                    >
                      <LayoutGrid :size="14" />
                    </button>
                  </div>

                  <!-- Sort -->
                  <div class="dropdown dropdown-end">
                    <label
                      tabindex="0"
                      class="btn btn-sm btn-ghost gap-1.5 normal-case font-normal"
                      aria-label="Sort collections"
                    >
                      {{ collectionsSortLabel }}
                      <ChevronDown :size="12" />
                    </label>
                    <ul tabindex="0" class="dropdown-content z-[50] menu p-1 shadow-lg bg-base-100 border border-base-300 rounded-box w-44">
                      <li v-for="opt in collectionsSortOptions" :key="opt.id">
                        <a
                          @click="collectionsSort = opt.id"
                          :class="{ 'font-semibold bg-base-200': collectionsSort === opt.id }"
                          class="text-sm"
                        >{{ opt.label }}</a>
                      </li>
                    </ul>
                  </div>

                  <div class="w-px h-5 bg-base-300/70 mx-0.5"></div>

                  <!-- CTA -->
                  <button
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="openCreateCollectionModal"
                  >
                    <Plus :size="14" />
                    New collection
                  </button>
                  <button
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="importModal?.choose()"
                    title="Create a collection from a .clio.zip someone exported"
                  >
                    <Upload :size="14" />
                    Import
                  </button>
                  <button
                    v-if="userStore.privateCollections"
                    class="btn btn-sm btn-ghost gap-1.5 normal-case font-medium border border-base-300 hover:border-base-content/30"
                    @click="openJoinSharedModal"
                    title="Accept a share token someone sent you"
                  >
                    <Share2 :size="14" />
                    Join shared
                  </button>
                </div>
              </header>

              <!-- Empty search result -->
              <div
                v-if="filteredCollections.length === 0 && collectionsSearch"
                class="text-center py-16 text-sm text-base-content/45"
              >
                No collections match
                <span class="text-base-content/70">"{{ collectionsSearch }}"</span>
              </div>

              <!-- List view -->
              <ul
                v-else-if="collectionsView === 'list'"
                class="divide-y divide-base-300/50"
              >
                <template v-for="(collection, index) in filteredCollections" :key="collection.id">
                <li
                  v-if="index === firstSharedIndex"
                  class="pt-6 pb-1 !border-t-0 list-none"
                  aria-hidden="true"
                >
                  <span class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/40">Shared with me</span>
                </li>
                <li
                  class="group flex items-start gap-3 py-4 sm:gap-5 sm:py-5 cursor-pointer transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary rounded-sm"
                  @click="selectCollectionAndNavigate(collection.id)"
                  :aria-current="collection.id === collectionStore.currentCollectionId ? 'true' : undefined"
                  role="button"
                  tabindex="0"
                  @keydown.enter="selectCollectionAndNavigate(collection.id)"
                  @keydown.space.prevent="selectCollectionAndNavigate(collection.id)"
                >
                  <span
                    class="mt-[0.55rem] w-2 h-2 rounded-full flex-shrink-0 ring-4 ring-transparent transition-all"
                    :class="{ 'ring-base-200': collection.id === collectionStore.currentCollectionId }"
                    :style="{ backgroundColor: collection.color }"
                    aria-hidden="true"
                  ></span>

                  <div class="flex-1 min-w-0">
                    <div class="flex items-baseline flex-wrap gap-x-3 gap-y-1">
                      <h3
                        class="text-[15px] truncate group-hover:text-primary transition-colors"
                        :class="collection.id === collectionStore.currentCollectionId ? 'font-semibold' : 'font-medium'"
                      >{{ collection.name }}</h3>
                      <span
                        v-if="collection.id === collectionStore.currentCollectionId"
                        class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/55"
                      >Active</span>
                      <span
                        v-if="collection.shared"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Shared</span>
                      <span
                        v-else-if="userStore.privateCollections && collection.team"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Team</span>
                      <span
                        v-else-if="userStore.privateCollections && collection.published"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >{{ collection.permission === 'owner' ? 'Shared with everyone' : 'Read-only' }}</span>
                      <span
                        v-else-if="userStore.privateCollections"
                        class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                      >Private</span>
                      <span
                        v-if="showLabel(collection.sensitivity)"
                        class="badge badge-xs"
                        :class="labelBadgeClass(collection.sensitivity)"
                        :title="`Sensitivity: ${collection.sensitivity}`"
                      >{{ collection.sensitivity }}</span>
                    </div>
                    <p v-if="collection.description" class="mt-1.5 text-[13px] leading-relaxed text-base-content/55 truncate">{{ collection.description }}</p>
                  </div>

                  <div class="flex items-center gap-4 flex-shrink-0 mt-[0.125rem]">
                    <span class="tabular-nums flex items-baseline gap-1">
                      <span class="text-[15px] font-medium text-base-content/75">{{ collection.document_count || 0 }}</span>
                      <span class="text-[10px] uppercase tracking-wider text-base-content/35">{{ (collection.document_count || 0) === 1 ? 'doc' : 'docs' }}</span>
                    </span>
                    <span
                      class="hidden sm:inline tabular-nums text-[12px] w-16 text-right"
                      :class="storageClass(collection)"
                      :title="storageTitle(collection)"
                    >{{ formatBytes(collection.storage_bytes || 0) }}</span>

                    <div class="flex items-center gap-0.5 hover-reveal">
                      <button
                        v-if="userStore.privateCollections && collection.permission === 'owner' && !collection.team"
                        @click.stop="openShareModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Share collection"
                        :aria-label="`Share collection ${collection.name}`"
                      >
                        <Share2 :size="13" />
                      </button>
                      <button
                        @click.stop="startClone(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Make my own copy"
                        :aria-label="`Make my own copy of ${collection.name}`"
                        :disabled="cloningId === collection.id"
                      >
                        <span v-if="cloningId === collection.id" class="loading loading-spinner loading-xs"></span>
                        <Copy v-else :size="13" />
                      </button>
                      <button
                        v-if="collectionStore.canConfigure(collection)"
                        @click.stop="openExportModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Export to share with another Clio"
                        :aria-label="`Export collection ${collection.name}`"
                      >
                        <Download :size="13" />
                      </button>
                      <button
                        v-if="collectionStore.canConfigure(collection)"
                        @click.stop="openEditCollectionModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Edit collection"
                        :aria-label="`Edit collection ${collection.name}`"
                      >
                        <Pencil :size="13" />
                      </button>
                    </div>
                  </div>
                </li>
                </template>
              </ul>

              <!-- Card view -->
              <div
                v-else
                class="grid gap-3"
                style="grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));"
              >
                <div
                  v-for="collection in filteredCollections"
                  :key="collection.id"
                  class="group relative flex flex-col gap-3 p-5 rounded-lg border border-base-300/60 bg-base-100 hover:border-base-content/25 hover:bg-base-100 transition-colors cursor-pointer min-h-[148px]"
                  :class="{ 'ring-1 ring-base-content/20': collection.id === collectionStore.currentCollectionId }"
                  @click="selectCollectionAndNavigate(collection.id)"
                  :aria-current="collection.id === collectionStore.currentCollectionId ? 'true' : undefined"
                  role="button"
                  tabindex="0"
                  @keydown.enter="selectCollectionAndNavigate(collection.id)"
                  @keydown.space.prevent="selectCollectionAndNavigate(collection.id)"
                >
                  <div class="flex items-start gap-2.5">
                    <span
                      class="mt-[0.45rem] w-2 h-2 rounded-full flex-shrink-0 ring-4 ring-transparent transition-all"
                      :class="{ 'ring-base-200': collection.id === collectionStore.currentCollectionId }"
                      :style="{ backgroundColor: collection.color }"
                      aria-hidden="true"
                    ></span>
                    <div class="min-w-0 flex-1">
                      <h3
                        class="text-[15px] truncate group-hover:text-primary transition-colors"
                        :class="collection.id === collectionStore.currentCollectionId ? 'font-semibold' : 'font-medium'"
                      >{{ collection.name }}</h3>
                      <div class="flex gap-2 mt-0.5 min-h-[12px]">
                        <span
                          v-if="collection.id === collectionStore.currentCollectionId"
                          class="text-[10px] uppercase tracking-[0.14em] font-semibold text-base-content/55"
                        >Active</span>
                        <span
                          v-if="collection.shared"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Shared</span>
                        <span
                          v-else-if="userStore.privateCollections && collection.team"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Team</span>
                        <span
                          v-else-if="userStore.privateCollections && collection.published"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >{{ collection.permission === 'owner' ? 'Shared with everyone' : 'Read-only' }}</span>
                        <span
                          v-else-if="userStore.privateCollections"
                          class="text-[10px] uppercase tracking-[0.14em] text-base-content/40"
                        >Private</span>
                        <span
                          v-if="showLabel(collection.sensitivity)"
                          class="badge badge-xs"
                          :class="labelBadgeClass(collection.sensitivity)"
                          :title="`Sensitivity: ${collection.sensitivity}`"
                        >{{ collection.sensitivity }}</span>
                      </div>
                    </div>
                  </div>

                  <p
                    v-if="collection.description"
                    class="text-[13px] leading-relaxed text-base-content/55 line-clamp-3 flex-1"
                  >{{ collection.description }}</p>
                  <div v-else class="flex-1"></div>

                  <div class="flex items-center justify-between pt-1 border-t border-base-300/40 -mx-1 px-1">
                    <span class="tabular-nums flex items-baseline gap-2">
                      <span class="flex items-baseline gap-1">
                        <span class="text-[15px] font-medium text-base-content/75">{{ collection.document_count || 0 }}</span>
                        <span class="text-[10px] uppercase tracking-wider text-base-content/35">{{ (collection.document_count || 0) === 1 ? 'doc' : 'docs' }}</span>
                      </span>
                      <span class="text-[12px]" :class="storageClass(collection)" :title="storageTitle(collection)">{{ formatBytes(collection.storage_bytes || 0) }}</span>
                    </span>
                    <div class="flex items-center gap-0.5 hover-reveal">
                      <button
                        v-if="userStore.privateCollections && collection.permission === 'owner' && !collection.team"
                        @click.stop="openShareModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Share collection"
                        :aria-label="`Share collection ${collection.name}`"
                      >
                        <Share2 :size="13" />
                      </button>
                      <button
                        @click.stop="startClone(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Make my own copy"
                        :aria-label="`Make my own copy of ${collection.name}`"
                        :disabled="cloningId === collection.id"
                      >
                        <span v-if="cloningId === collection.id" class="loading loading-spinner loading-xs"></span>
                        <Copy v-else :size="13" />
                      </button>
                      <button
                        v-if="collectionStore.canConfigure(collection)"
                        @click.stop="openExportModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Export to share with another Clio"
                        :aria-label="`Export collection ${collection.name}`"
                      >
                        <Download :size="13" />
                      </button>
                      <button
                        v-if="collectionStore.canConfigure(collection)"
                        @click.stop="openEditCollectionModal(collection)"
                        class="btn btn-ghost btn-xs btn-square"
                        title="Edit collection"
                        :aria-label="`Edit collection ${collection.name}`"
                      >
                        <Pencil :size="13" />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <SearchTab v-if="activeTab === 'search'" @switch-tab="switchTab" />
            <ArtifactsTab v-if="activeTab === 'generate'" />
            <ExpertiseLibrary v-if="activeTab === 'expertise'" />
            <MCPTab v-if="activeTab === 'mcp'" />
            <AdminTab v-if="activeTab === 'admin'" />
            <SettingsTab v-if="activeTab === 'settings'" @data-cleared="handleDataCleared" @stats-updated="statsStore.fetchStats" @switch-tab="switchTab" @chat-tab-toggled="onChatTabToggled" @clone-collection="collectionStore.currentCollection && startClone(collectionStore.currentCollection)" />
          </div>
        </div>

      </main>

      <!-- Right sidebar: Studio (resizable, hidden on collections overview) -->
      <aside
        v-show="isWorkspaceView"
        class="bg-base-100 overflow-hidden flex flex-col border-l border-base-300"
        :class="[
          isResizingAnalysis ? '' : 'transition-all duration-200 ease-in-out',
          isCompact ? 'absolute right-0 top-0 h-full z-40 shadow-2xl' : 'flex-shrink-0 relative',
        ]"
        :style="{ width: analysisSidebarOpen ? effectiveAnalysisWidth + 'px' : '0px' }"
      >
        <!-- Drag handle (on the LEFT edge of the right sidebar; pointless in compact overlay mode) -->
        <div
          v-if="analysisSidebarOpen && !isCompact"
          class="absolute left-0 top-0 h-full w-1 cursor-col-resize group z-10 hover:bg-primary/40 transition-colors"
          :class="isResizingAnalysis ? 'bg-primary/60' : ''"
          @mousedown.prevent="startResizeAnalysis"
        >
          <div class="absolute inset-y-0 left-0 flex items-center justify-center w-1">
            <div class="flex flex-col gap-1 opacity-0 group-hover:opacity-60 transition-opacity">
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
              <div class="w-1 h-1 rounded-full bg-base-content"></div>
            </div>
          </div>
        </div>

        <div class="h-full flex flex-col" :style="{ width: effectiveAnalysisWidth + 'px' }">
          <StudioSidebar
            :overlay="isCompact"
            @close="analysisSidebarOpen = false"
            @send-to-chat="handleSendToChat"
            @switch-tab="switchTab"
          />
        </div>
      </aside>
    </div>

    <!-- ── Footer status bar (background jobs) ── -->
    <footer
      class="hidden md:flex items-center gap-3 px-3 h-6 bg-base-100 border-t border-base-300 text-[11px] text-base-content/55 flex-shrink-0"
      role="contentinfo"
      aria-label="Background jobs status"
    >
      <span class="flex items-center gap-1.5 text-base-content/45 flex-shrink-0">
        <span class="brand-art brand-art-mark h-3.5 w-3.5" aria-hidden="true"></span>
        <span class="font-medium tracking-tight">Clio</span>
      </span>
      <span class="w-px h-3 bg-base-300" aria-hidden="true"></span>

      <button
        class="flex items-center gap-1.5 hover:text-base-content transition-colors"
        @click="showJobsDrawer = true"
        :title="backgroundJobsStore.activeJobCount > 0 ? `${backgroundJobsStore.activeJobCount} active job${backgroundJobsStore.activeJobCount === 1 ? '' : 's'} — click to open` : 'Background jobs — click to open'"
        :aria-label="backgroundJobsStore.activeJobCount > 0
          ? `Background jobs — ${backgroundJobsStore.activeJobCount} active`
          : 'Background jobs'"
      >
        <Loader2
          v-if="backgroundJobsStore.hasActiveJobs"
          :size="11"
          class="animate-spin text-warning"
          aria-hidden="true"
        />
        <Bell v-else :size="11" aria-hidden="true" />
        <span v-if="backgroundJobsStore.activeJobCount > 0" class="font-medium text-warning">
          {{ backgroundJobsStore.activeJobCount }} job{{ backgroundJobsStore.activeJobCount === 1 ? '' : 's' }} running
        </span>
        <span v-else>Idle</span>
      </button>

      <!-- Pending review (admins only) -->
      <template v-if="userStore.adminConsole">
        <span class="w-px h-3 bg-base-300" aria-hidden="true"></span>
        <ReviewBell variant="footer" @open="openReview" />
      </template>

      <!-- Inline progress for the most recent active job, if any -->
      <template v-if="footerActiveJob">
        <span class="w-px h-3 bg-base-300" aria-hidden="true"></span>
        <span class="truncate max-w-[40ch] text-base-content/45">
          {{ footerJobLabel }}
        </span>
        <progress
          class="progress progress-warning w-24 h-1.5"
          :value="footerActiveJob.progress || 0"
          max="100"
          aria-hidden="true"
        ></progress>
      </template>

      <div class="flex-1"></div>

      <!-- Air-gapped deployment indicator (OFFLINE_MODE=1 on the server) -->
      <span
        v-if="statsStore.offline"
        class="hidden sm:flex items-center gap-1 text-base-content/60"
        title="Offline mode: cloud AI disabled — no external connections beyond your configured endpoints"
      >
        <ShieldCheck :size="11" aria-hidden="true" />
        Air-gapped
      </span>
      <span v-if="statsStore.offline" class="hidden sm:inline w-px h-3 bg-base-300" aria-hidden="true"></span>

      <!-- Stats moved here from the header for breathing room -->
      <span class="hidden md:inline tabular-nums">
        {{ statsStore.documents }} {{ statsStore.documents === 1 ? 'source' : 'sources' }}
      </span>
      <span class="hidden md:inline w-px h-3 bg-base-300" aria-hidden="true"></span>
      <span class="hidden md:inline tabular-nums">
        {{ statsStore.pages }} {{ statsStore.pages === 1 ? 'page' : 'pages' }}
      </span>
    </footer>

    <!-- ── Phone: bottom tab bar (the header tabs move here) ── -->
    <WorkspaceNav
      v-if="!isCollectionsView"
      class="md:hidden flex-shrink-0 bg-base-100 border-t border-base-300 pb-[env(safe-area-inset-bottom)]"
      mobile
      :active-tab="activeTab"
      :chat-enabled="chatTabEnabled"
      :admin-console="userStore.adminConsole"
      :admin-badge="reviewStore.total"
      @navigate="navigateMobile"
      @notes="openNotes"
    />

    <!-- Global toast stack + backend-unreachable banner -->
    <Toaster />

    <!-- Command palette, shortcuts sheet and About: the three things the
         Help menu and the keyboard open from anywhere. -->
    <CommandPalette :open="showPalette" :commands="paletteCommands" @close="showPalette = false" />
    <ShortcutsDialog :open="showShortcuts" @close="showShortcuts = false" />
    <AboutDialog :open="showAbout" @close="showAbout = false" />

    <!-- Search-index readiness. One quiet bar at the top: progress while the
         model downloads, a way forward when it is missing or failed, nothing
         at all once it is ready. -->
    <Transition name="rise">
      <div
        v-if="embeddingBanner && !embeddingBannerDismissed"
        class="fixed top-0 inset-x-0 z-[102] flex justify-center pointer-events-none"
        :role="embeddingBanner.kind === 'progress' ? 'status' : 'alert'"
        aria-live="polite"
      >
        <div
          class="notice shadow-lg rounded-t-none rounded-b-xl bg-base-100 ring-1 ring-base-content/10 w-full max-w-xl pointer-events-auto items-start"
          :class="{ 'notice-warning': embeddingBanner.kind === 'missing', 'notice-error': embeddingBanner.kind === 'error' }"
        >
          <span v-if="embeddingBanner.kind === 'progress'" class="loading loading-spinner loading-xs mt-0.5" aria-hidden="true"></span>
          <AlertTriangle v-else :size="15" class="mt-0.5" :class="embeddingBanner.kind === 'error' ? 'text-error' : 'text-warning'" aria-hidden="true" />
          <div class="flex-1 min-w-0">
            <div class="flex items-center justify-between gap-3">
              <span class="truncate">{{ embeddingBanner.text }}</span>
              <span v-if="embeddingBanner.kind === 'progress' && embeddingBanner.percent != null" class="tabular-nums text-base-content/60 flex-shrink-0">{{ embeddingBanner.percent }}%</span>
            </div>
            <progress
              v-if="embeddingBanner.kind === 'progress'"
              class="progress progress-primary w-full h-1 mt-1.5"
              :value="embeddingBanner.percent ?? undefined"
              max="100"
            ></progress>
            <div v-else class="flex items-center gap-1.5 mt-1.5">
              <button
                v-if="embeddingBanner.canDownload"
                class="btn btn-xs btn-primary"
                :disabled="embeddingWarming"
                @click="downloadEmbeddingModel"
              >
                <span v-if="embeddingWarming" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                {{ embeddingBanner.kind === 'error' ? 'Try again' : 'Download now' }}
              </button>
              <button class="btn btn-xs btn-ghost" @click="switchTab('settings')">Choose another provider</button>
            </div>
          </div>
          <button
            class="side-icon-btn side-icon-btn-sm text-base-content/50 hover:text-base-content -mr-1"
            aria-label="Dismiss this notice"
            @click="embeddingBannerDismissed = true"
          >
            <X :size="13" aria-hidden="true" />
          </button>
        </div>
      </div>
    </Transition>

    <!-- Create Collection Modal (native showModal: focus trap, Escape, inert background) -->
    <dialog :ref="createModal.dialogRef" class="modal" @close="createModal.onClosed" aria-labelledby="create-collection-title">
      <div class="modal-box">
        <h3 id="create-collection-title" class="font-bold text-lg mb-4">Create New Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-name">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            id="new-collection-name"
            v-model="newCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            @keyup.enter="createCollection"
          />
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-description">
            <span class="label-text">Description (optional)</span>
          </label>
          <textarea
            id="new-collection-description"
            v-model="newCollectionDescription"
            class="textarea textarea-bordered"
            placeholder="What kind of sources will this collection contain?"
          ></textarea>
        </div>

        <div v-if="userStore.privateCollections" class="form-control w-full mb-4">
          <label class="label pb-1">
            <span class="label-text">Visibility</span>
          </label>
          <div class="flex flex-col gap-2" role="radiogroup" aria-label="Collection visibility">
            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                   :class="newCollectionVisibility === 'private' ? 'border-primary bg-primary/5' : 'border-base-300'">
              <input type="radio" value="private" v-model="newCollectionVisibility" class="radio radio-primary radio-sm mt-0.5" />
              <span>
                <span class="block text-sm font-medium">Private</span>
                <span class="block text-xs text-base-content/55">Only you can see it. Share it later with read or read-write links.</span>
              </span>
            </label>
            <label class="flex items-start gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                   :class="newCollectionVisibility === 'team' ? 'border-primary bg-primary/5' : 'border-base-300'">
              <input type="radio" value="team" v-model="newCollectionVisibility" class="radio radio-primary radio-sm mt-0.5" />
              <span>
                <span class="block text-sm font-medium">Team</span>
                <span class="block text-xs text-base-content/55">Everyone on this deployment can see and edit it.</span>
              </span>
            </label>
          </div>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="new-collection-color">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              id="new-collection-color"
              v-model="newCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
              aria-label="Custom collection color"
            />
            <div class="flex gap-2" role="radiogroup" aria-label="Preset collection colors">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                type="button"
                role="radio"
                :aria-checked="newCollectionColor === color"
                :aria-label="`Color ${color}`"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="newCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="newCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="createModal.close()" :disabled="creatingCollection">
            Cancel
          </button>
          <button
            class="btn btn-primary"
            @click="createCollection"
            :disabled="!newCollectionName.trim() || creatingCollection"
          >
            <span v-if="creatingCollection" class="loading loading-spinner loading-sm"></span>
            Create Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Edit Collection Modal -->
    <dialog :ref="editModal.dialogRef" class="modal" @close="editModal.onClosed" aria-labelledby="edit-collection-title">
      <div class="modal-box">
        <h3 id="edit-collection-title" class="font-bold text-lg mb-4">Edit Collection</h3>

        <div class="form-control w-full mb-4">
          <label class="label" for="edit-collection-name">
            <span class="label-text">Collection Name</span>
          </label>
          <input
            id="edit-collection-name"
            v-model="editCollectionName"
            type="text"
            placeholder="e.g., Research Papers"
            class="input input-bordered w-full"
            :disabled="editingCollectionId === 'default'"
            :aria-describedby="editingCollectionId === 'default' ? 'edit-collection-name-help' : undefined"
          />
          <p v-if="editingCollectionId === 'default'" id="edit-collection-name-help" class="label-text-alt text-warning mt-1">
            Default collection name cannot be changed
          </p>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label pb-1" for="edit-collection-description">
            <span class="label-text">Description</span>
          </label>
          <textarea
            id="edit-collection-description"
            v-model="editCollectionDescription"
            class="textarea textarea-bordered w-full"
            rows="2"
            placeholder="What kind of sources does this collection contain?"
          ></textarea>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label pb-1" for="edit-collection-guide">
            <span class="label-text">Guide for calling agents</span>
          </label>
          <textarea
            id="edit-collection-guide"
            v-model="editCollectionGuide"
            class="textarea textarea-bordered w-full font-mono text-sm"
            rows="6"
            placeholder="e.g. currency is USD; 'Jane' = Jane Smith; dates are MM/DD/YYYY; 'NAV' column is market value net of fees."
          ></textarea>
          <p class="text-xs opacity-70 mt-1">
            Surfaced in every MCP response (~500 char summary in search, full text in get_collection_info) so the calling LLM has durable context.
          </p>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label pb-1" for="edit-collection-sensitivity">
            <span class="label-text">Sensitivity</span>
          </label>
          <select
            id="edit-collection-sensitivity"
            v-model="editCollectionSensitivity"
            class="select select-bordered w-full"
          >
            <option v-for="level in userStore.sensitivityLevels" :key="level" :value="level">
              {{ level.charAt(0).toUpperCase() + level.slice(1) }}
            </option>
          </select>
          <p class="text-xs opacity-70 mt-1">
            {{ SENSITIVITY_HELP[editCollectionSensitivity] }} Shown on every result and citation; individual documents can override it.
          </p>
        </div>

        <!-- Release to everyone. Read-only for them, unchanged for you: the
             one way a collection reaches the whole deployment while staying
             yours to rebuild. -->
        <div v-if="canPublishEditing" class="form-control w-full mb-4">
          <label class="flex items-start gap-3 cursor-pointer">
            <input
              v-model="editCollectionPublished"
              type="checkbox"
              class="toggle toggle-primary toggle-sm mt-0.5 flex-shrink-0"
              :disabled="editCollectionSensitivity === 'restricted'"
            />
            <span class="min-w-0">
              <span class="label-text font-medium">Share with everyone, read-only</span>
              <span class="block text-xs opacity-70 mt-1">
                Anyone signed in can search it, ask about it and reach it from their AI tools.
                Only you can add sources, change its settings or re-index it.
              </span>
              <span v-if="editCollectionSensitivity === 'restricted'" class="block text-xs text-warning mt-1">
                Restricted collections cannot be released. Change the sensitivity label first.
              </span>
            </span>
          </label>
        </div>

        <div class="form-control w-full mb-4">
          <label class="label" for="edit-collection-color">
            <span class="label-text">Color</span>
          </label>
          <div class="flex items-center gap-3">
            <input
              id="edit-collection-color"
              v-model="editCollectionColor"
              type="color"
              class="w-12 h-12 rounded cursor-pointer border-2 border-base-300"
              aria-label="Custom collection color"
            />
            <div class="flex gap-2" role="radiogroup" aria-label="Preset collection colors">
              <button
                v-for="color in ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899']"
                :key="color"
                type="button"
                role="radio"
                :aria-checked="editCollectionColor === color"
                :aria-label="`Color ${color}`"
                class="w-8 h-8 rounded cursor-pointer border-2"
                :class="editCollectionColor === color ? 'border-base-content' : 'border-transparent'"
                :style="{ backgroundColor: color }"
                @click="editCollectionColor = color"
              ></button>
            </div>
          </div>
        </div>

        <div class="modal-action justify-between">
          <button
            v-if="editingCollectionId !== 'default'"
            class="btn btn-error btn-outline"
            @click="confirmDeleteCollection"
            :disabled="updatingCollection"
          >
            <Trash2 :size="16" />
            Delete
          </button>
          <div v-else></div>
          <div class="flex gap-2">
            <button class="btn btn-ghost" @click="editModal.close()" :disabled="updatingCollection">
              Cancel
            </button>
            <button
              class="btn btn-primary"
              @click="updateCollection"
              :disabled="!editCollectionName.trim() || updatingCollection"
            >
              <span v-if="updatingCollection" class="loading loading-spinner loading-sm"></span>
              Save Changes
            </button>
          </div>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Export / import (portable .clio.zip bundles): both state which
         embedding model indexed the collection and what that means -->
    <ExportCollectionModal ref="exportModal" />
    <ImportCollectionModal ref="importModal" @imported="onCollectionImported" />

    <!-- Delete Collection Confirmation Modal -->
    <dialog
      :ref="deleteModal.dialogRef"
      class="modal"
      @close="deleteModal.onClosed"
      aria-labelledby="delete-collection-title"
      aria-describedby="delete-collection-desc"
    >
      <div class="modal-box">
        <h3 id="delete-collection-title" class="font-bold text-lg text-error mb-4">Delete Collection</h3>
        <p id="delete-collection-desc" class="mb-2">Are you sure you want to delete <strong>{{ editCollectionName }}</strong>?</p>

        <div class="alert alert-warning my-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          <div>
            <div class="font-bold">This will permanently delete:</div>
            <ul class="list-disc list-inside text-sm mt-1">
              <li>All uploaded sources and files</li>
              <li>All vector indexes and embeddings</li>
              <li>All search history for this collection</li>
            </ul>
            <div class="text-sm font-semibold mt-2">This action cannot be undone.</div>
          </div>
        </div>

        <!-- Error display -->
        <div v-if="deleteError" class="alert alert-error mb-4">
          <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span>{{ deleteError }}</span>
        </div>

        <div class="modal-action">
          <button class="btn btn-ghost" @click="closeDeleteModal" :disabled="deletingCollection">
            Cancel
          </button>
          <button
            class="btn btn-error"
            @click="deleteCollection"
            :disabled="deletingCollection"
          >
            <span v-if="deletingCollection" class="loading loading-spinner loading-sm"></span>
            Delete Collection
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button>close</button>
      </form>
    </dialog>

    <!-- Share Modal -->
    <ShareModal
      :visible="showShareModal"
      :collection-id="shareCollectionId"
      :collection-name="shareCollectionName"
      :initial-token="shareInitialToken"
      @close="showShareModal = false; shareInitialToken = ''"
      @shared="handleShared"
    />

    <!-- First-run onboarding takeover (shown only when no provider is configured) -->
    <WelcomeOnboarding
      :show="showOnboarding"
      @complete="handleOnboardingComplete"
      @skip="handleOnboardingSkip"
    />

    <!-- Acceptable-use acknowledgement: a takeover until accepted when the
         deployment requires it, otherwise openable on demand -->
    <AcceptableUseModal v-if="userStore.aup.enabled || userStore.aupOpen" />

    <!-- Background Jobs Sidebar Drawer -->
    <div
      v-if="showJobsDrawer"
      class="fixed inset-0 z-[200]"
      @click.self="showJobsDrawer = false"
      role="dialog"
      aria-modal="true"
      aria-labelledby="jobs-drawer-title"
    >
      <!-- Backdrop -->
      <div class="absolute inset-0 bg-base-content/20" @click="showJobsDrawer = false" aria-hidden="true"></div>

      <!-- Drawer Panel -->
      <div class="absolute right-0 top-0 h-full w-96 max-w-[90vw] bg-base-100 shadow-2xl flex flex-col">
        <!-- Header -->
        <div class="flex items-center justify-between p-4 border-b border-base-300">
          <h3 id="jobs-drawer-title" class="text-lg font-bold">Background Jobs</h3>
          <button
            class="btn btn-ghost btn-sm btn-circle"
            @click="showJobsDrawer = false"
            aria-label="Close background jobs panel"
          >
            <X :size="20" />
          </button>
        </div>

        <!-- Content -->
        <div class="flex-1 overflow-y-auto p-4">
          <div v-if="backgroundJobsStore.allJobs.length === 0" class="text-center py-12 text-base-content/50">
            <Bell :size="48" class="mx-auto mb-4 opacity-30" />
            <p>No background jobs</p>
            <p class="text-sm mt-1">Large file uploads will appear here</p>
          </div>

          <div v-else class="space-y-4">
            <div
              v-for="job in backgroundJobsStore.allJobs"
              :key="`${job.type}-${job.id}`"
              class="card bg-base-200"
            >
              <div class="card-body p-4 gap-2">
                <!-- Job Header -->
                <div class="flex items-center justify-between gap-2">
                  <div class="flex items-center gap-2 min-w-0">
                    <Loader2
                      v-if="backgroundJobsStore.isActiveStatus(job.status)"
                      :size="18"
                      class="animate-spin text-primary flex-shrink-0"
                    />
                    <CheckCircle
                      v-else-if="job.status === 'completed' && job.failedFiles.length === 0"
                      :size="18"
                      class="text-success flex-shrink-0"
                    />
                    <AlertTriangle
                      v-else-if="job.status === 'completed'"
                      :size="18"
                      class="text-warning flex-shrink-0"
                    />
                    <XCircle v-else :size="18" class="text-error flex-shrink-0" />
                    <span class="font-semibold truncate">{{ jobTitle(job) }}</span>
                  </div>
                  <div class="flex items-center gap-1 flex-shrink-0">
                    <span class="badge badge-sm" :class="jobBadgeClass(job)">{{ jobStatusLabel(job) }}</span>
                    <button
                      v-if="job.cancellable"
                      @click="cancelJob(job.id)"
                      class="btn btn-ghost btn-xs text-error"
                      :disabled="job.cancelRequested"
                      :title="job.cancelRequested ? 'Stopping after the current file' : 'Cancel job'"
                      :aria-label="`Cancel ${job.type} job`"
                    >
                      <XCircle :size="16" />
                    </button>
                  </div>
                </div>

                <!-- What it is working on, or where it sits in line -->
                <div v-if="job.queuePosition" class="text-sm text-base-content/70">
                  Waiting for another job to finish — {{ ordinal(job.queuePosition) }} in line.
                </div>
                <div v-else-if="job.currentFile" class="text-sm text-base-content/70 truncate" :title="job.currentFile">
                  {{ job.currentFile }}
                </div>

                <!-- Phase Info -->
                <div v-if="job.phase && backgroundJobsStore.isActiveStatus(job.status) && !job.queuePosition" class="flex items-center gap-2 text-sm flex-wrap">
                  <span class="badge badge-sm badge-primary capitalize">{{ job.phase }}</span>
                  <span v-if="job.chunksTotal > 0" class="text-base-content/50">
                    {{ job.chunksProcessed.toLocaleString() }}/{{ job.chunksTotal.toLocaleString() }} chunks
                  </span>
                </div>

                <!-- Progress -->
                <div v-if="backgroundJobsStore.isActiveStatus(job.status)" class="space-y-1">
                  <progress
                    class="progress w-full"
                    :class="job.cancelRequested ? 'progress-warning' : 'progress-primary'"
                    :value="job.queuePosition ? 0 : job.progress"
                    max="100"
                  ></progress>
                  <div class="flex justify-between text-xs text-base-content/60">
                    <span>{{ Math.round(job.progress) }}%</span>
                    <span>{{ (job.processedFiles || 0).toLocaleString() }}/{{ (job.totalFiles || 0).toLocaleString() }} files</span>
                  </div>
                </div>

                <!-- Outcome, once it is over. Suppressed when an error
                     message already says how it ended, so the card does not
                     state the same thing twice. -->
                <div v-else-if="!job.error" class="text-xs text-base-content/60">
                  {{ jobOutcomeLine(job) }}
                </div>

                <!-- Error -->
                <div v-if="job.error" class="text-sm text-error">
                  {{ job.error }}
                </div>

                <!-- Files that did not make it in. This list is the only
                     record a person has of sources missing from a job that
                     otherwise looks like it worked. -->
                <details v-if="job.failedFiles.length" class="text-xs">
                  <summary class="cursor-pointer text-warning">
                    {{ job.failedFiles.length }} file{{ job.failedFiles.length === 1 ? '' : 's' }} could not be indexed
                  </summary>
                  <ul class="mt-1.5 space-y-1 max-h-40 overflow-y-auto">
                    <li v-for="(f, i) in job.failedFiles" :key="i" class="text-base-content/70">
                      <div class="font-medium truncate" :title="f.filename">{{ f.filename }}</div>
                      <div class="text-base-content/50 line-clamp-2" :title="f.error">{{ f.error }}</div>
                    </li>
                  </ul>
                </details>

                <div v-if="!backgroundJobsStore.isActiveStatus(job.status) && job.type !== 'reindex'" class="flex justify-end">
                  <button class="btn btn-ghost btn-xs" @click="backgroundJobsStore.removeUploadJob(job.id)">
                    Dismiss
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div v-if="backgroundJobsStore.finishedJobs.length" class="p-4 border-t border-base-300">
          <button class="btn btn-ghost btn-sm w-full" @click="backgroundJobsStore.clearFinishedJobs()">
            Clear finished jobs
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch, computed, nextTick } from 'vue'
import { Settings, Plus, Check, ChevronDown, Pencil, Trash2, Bell, Loader2, CheckCircle, XCircle, AlertTriangle, X, Share2, Users, LayoutGrid, List, BookOpen, Sparkles, ShieldCheck, CircleUser, LogOut, HelpCircle, EllipsisVertical, Search, Copy, Download, Upload, PanelLeft, PanelLeftClose, PanelRight, PanelRightClose, Command, Keyboard, Info, MessageSquare, Plug, FileText, Gauge, Layers, StickyNote, FolderPlus, SunMedium, Moon, Palette, MonitorCog, History, MessageSquarePlus, ShieldAlert } from 'lucide-vue-next'
import CommandPalette from './components/CommandPalette.vue'
import ShortcutsDialog from './components/ShortcutsDialog.vue'
import AboutDialog from './components/AboutDialog.vue'
import { isTypingTarget, isMod, MOD } from './utils/shortcuts'
import { useChatStore } from './stores/chatStore'
import http from './utils/http'
import { useModal } from './composables/useModal'
import { lazyView } from './utils/lazyView'

const chatTabEnabled = ref(true)

// Core layout + default tab load eagerly; every other tab is code-split so
// the initial bundle stays small.
import SourcesSidebar from './components/SourcesSidebar.vue'
import StudioSidebar from './components/StudioSidebar.vue'
import WorkspaceNav from './components/WorkspaceNav.vue'
import ReviewBell from './components/ReviewBell.vue'
import ModelPicker from './components/ModelPicker.vue'
import CollectionPicker from './components/CollectionPicker.vue'
import ExportCollectionModal from './components/ExportCollectionModal.vue'
import ImportCollectionModal from './components/ImportCollectionModal.vue'
import ChatTab from './components/ChatTab.vue'

// Every tab but Ask lives in its own chunk; lazyView keeps a chunk that has
// gone missing (deploy under an open tab) from rendering as an empty pane.
const SearchTab = lazyView(() => import('./components/SearchTab.vue'))
const ArtifactsTab = lazyView(() => import('./components/ArtifactsTab.vue'))
const SettingsTab = lazyView(() => import('./components/SettingsTab.vue'))
const MCPTab = lazyView(() => import('./components/MCPTab.vue'))
const ShareModal = lazyView(() => import('./components/ShareModal.vue'))
const ExpertiseLibrary = lazyView(() => import('./components/ExpertiseLibrary.vue'))
const WelcomeOnboarding = lazyView(() => import('./components/WelcomeOnboarding.vue'))
const AdminTab = lazyView(() => import('./components/AdminTab.vue'))
const AcceptableUseModal = lazyView(() => import('./components/AcceptableUseModal.vue'))
import { SENSITIVITY_HELP, labelBadgeClass, showLabel } from './utils/governance'
import { formatBytes, describeStorage } from './utils/format'
import Toaster from './components/Toaster.vue'
import {
  getConfiguredProviderIds,
  retireSurfaceOverrides,
  getServerProviderIds,
  setActiveProviderLS,
  getProviderDisplayName,
  getProviderConfig,
  migrateLegacySettings,
} from './utils/aiProviders.js'
import { useCollectionStore } from './stores/collectionStore'
import { useUserStore } from './stores/userStore'
import { useReviewStore } from './stores/reviewStore'
import { useSearchStore } from './stores/searchStore'
import { useBackgroundJobsStore } from './stores/backgroundJobsStore'
import { useProviderStore } from './stores/providerStore'
import { useStatsStore } from './stores/statsStore'
import { useUiStore } from './stores/uiStore'

// Fold legacy provider keys (chat_provider, ai_api_key_*) into the unified
// config before any tab mounts and reads it.
migrateLegacySettings()

const collectionStore = useCollectionStore()
const searchStore = useSearchStore()
const backgroundJobsStore = useBackgroundJobsStore()
const userStore = useUserStore()
const reviewStore = useReviewStore()
const providerStore = useProviderStore()
const statsStore = useStatsStore()
const ui = useUiStore()

// ── Global provider pill (top bar) ──
// Shows the app-wide default provider resolved by the shared chain in
// aiProviders.js; the dropdown switches ai_settings.provider directly.
// Reactivity comes from providerStore's version — every provider config
// write bumps it, so this recomputes without any event listeners here.
const providerPill = computed(() => {
  providerStore.version // reactivity hook
  const configured = getConfiguredProviderIds()
  const id = providerStore.activeProviderId
  return {
    id,
    configured,
    teamIds: getServerProviderIds(),
    name: id ? getProviderDisplayName(id) : '',
    // The deployment's own provider has no local config to read a model
    // from — the server picked it, so the server is what names it.
    model: (id && getProviderConfig(id)?.model)
      || (id === providerStore.deploymentDefault.provider ? providerStore.deploymentDefault.model : '')
      || '',
  }
})

const providerDisplayName = getProviderDisplayName

// DaisyUI dropdowns stay open while their trigger has focus; blur closes them.
const closeMenus = () => {
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
}

const setGlobalProvider = (pid) => {
  setActiveProviderLS(pid) // dispatches the change event → pill refreshes
  closeMenus()
}

// Restore the last active tab so a refresh doesn't dump the user back in Chat
const VALID_TABS = ['chat', 'search', 'generate', 'expertise', 'mcp', 'admin', 'settings', 'collections']
const savedTab = localStorage.getItem('active_tab')
const activeTab = ref(VALID_TABS.includes(savedTab) ? savedTab : 'chat')
watch(activeTab, (tab) => localStorage.setItem('active_tab', tab))

// Resizable sidebar
const SIDEBAR_MIN = 220
const SIDEBAR_MAX = 600
const sidebarWidth = ref(parseInt(localStorage.getItem('sidebar_width') || '320'))
const isResizing = ref(false)

const startResize = (e) => {
  isResizing.value = true
  const startX = e.clientX
  const startWidth = sidebarWidth.value

  const onMove = (e) => {
    const delta = e.clientX - startX
    sidebarWidth.value = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startWidth + delta))
  }

  const onUp = () => {
    isResizing.value = false
    localStorage.setItem('sidebar_width', String(sidebarWidth.value))
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// Signed-in identity (Cloudflare Access deployments); drives the account menu.
const me = ref({ authenticated_via: null, identity: null, logout_url: null })
const loadMe = async () => {
  try {
    const resp = await http.get('/api/me')
    me.value = resp.data
  } catch { /* open or password deployments simply show no account menu */ }
}

// Compact (sidebar/companion) mode: when the window is pinned narrow next to
// other apps, the side panels overlay the main surface instead of squeezing
// it. Tracked live so dragging the window across the threshold adapts.
const compactQuery = window.matchMedia('(max-width: 767px)')
const isCompact = ref(compactQuery.matches)
compactQuery.addEventListener('change', e => { isCompact.value = e.matches })

// Narrow (tablet-width) mode: panels stay inline, but two 320px panels on a
// 768px screen would leave the answer column ~130px wide — so only one of
// the two may be open at a time.
const narrowQuery = window.matchMedia('(max-width: 1023px)')
const isNarrow = ref(narrowQuery.matches)
narrowQuery.addEventListener('change', e => { isNarrow.value = e.matches })

// Sources sidebar state (persisted; starts closed in compact mode where an
// open overlay would cover the whole answer surface)
const sourcesSidebarOpen = ref(
  isCompact.value ? false : localStorage.getItem('sources_sidebar_open') !== 'false'
)

watch(sourcesSidebarOpen, v => {
  if (!isCompact.value) localStorage.setItem('sources_sidebar_open', String(v))
  if (v && (isCompact.value || isNarrow.value)) analysisSidebarOpen.value = false  // one panel at a time
})

// Analysis sidebar state (persisted, resizable)
const ANALYSIS_MIN = 240
const ANALYSIS_MAX = 600
const analysisWidth = ref(parseInt(localStorage.getItem('analysis_width') || '320'))
const analysisSidebarOpen = ref(
  isCompact.value ? false : localStorage.getItem('analysis_sidebar_open') === 'true'
)
const isResizingAnalysis = ref(false)

// Both persisted open on a narrow window: the Sources panel wins (before the
// watcher below is registered, so this doesn't overwrite the preference).
if (isNarrow.value && sourcesSidebarOpen.value && analysisSidebarOpen.value) {
  analysisSidebarOpen.value = false
}

watch(analysisSidebarOpen, v => {
  if (!isCompact.value && !isNarrow.value) localStorage.setItem('analysis_sidebar_open', String(v))
  if (v && (isCompact.value || isNarrow.value)) sourcesSidebarOpen.value = false  // one panel at a time
})

watch(isNarrow, narrow => {
  if (narrow && !isCompact.value && sourcesSidebarOpen.value && analysisSidebarOpen.value) {
    analysisSidebarOpen.value = false
  }
})

// Live viewport width so overlay sizing follows rotations and resizes
const viewportWidth = ref(window.innerWidth)
window.addEventListener('resize', () => { viewportWidth.value = window.innerWidth }, { passive: true })

// Overlay panels: on phones they take the full width (a full sheet is
// easier to read and dismiss than a sliver of chat peeking through); on
// wider compact windows they keep their desktop width, capped at 85% so
// some context stays visible.
const compactPanelWidth = (preferred) =>
  viewportWidth.value < 480
    ? viewportWidth.value
    : Math.min(preferred, Math.round(viewportWidth.value * 0.85))
const effectiveSidebarWidth = computed(() =>
  isCompact.value ? compactPanelWidth(sidebarWidth.value) : sidebarWidth.value
)
const effectiveAnalysisWidth = computed(() =>
  isCompact.value ? compactPanelWidth(analysisWidth.value) : analysisWidth.value
)

// Entering compact mode with both panels open would stack two overlays; close them.
watch(isCompact, compact => {
  if (compact) {
    sourcesSidebarOpen.value = false
    analysisSidebarOpen.value = false
  } else {
    sourcesSidebarOpen.value = localStorage.getItem('sources_sidebar_open') !== 'false'
    analysisSidebarOpen.value = localStorage.getItem('analysis_sidebar_open') === 'true'
      && !(isNarrow.value && sourcesSidebarOpen.value)
  }
})

const startResizeAnalysis = (e) => {
  isResizingAnalysis.value = true
  const startX = e.clientX
  const startWidth = analysisWidth.value

  const onMove = (e) => {
    // Right sidebar grows when dragging LEFT, so invert
    const delta = startX - e.clientX
    analysisWidth.value = Math.min(ANALYSIS_MAX, Math.max(ANALYSIS_MIN, startWidth + delta))
  }

  const onUp = () => {
    isResizingAnalysis.value = false
    localStorage.setItem('analysis_width', String(analysisWidth.value))
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }

  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
}

// True when the user is on the all-collections overview — sidebars hide here.
// Storage against the per-collection cap, on the overview rows.
function storageRatio(collection) {
  const limit = userStore.collectionStorageLimitBytes
  return limit ? (collection.storage_bytes || 0) / limit : 0
}
function storageClass(collection) {
  const r = storageRatio(collection)
  return r >= 1 ? 'text-error font-medium' : r >= 0.9 ? 'text-warning font-medium' : 'text-base-content/45'
}
function storageTitle(collection) {
  return describeStorage(collection.storage_bytes || 0, userStore.collectionStorageLimitBytes)
}

const isCollectionsView = computed(() => activeTab.value === 'collections')
const isWorkspaceView = computed(() => ['chat', 'search'].includes(activeTab.value))
const navigateMobile = (tab) => {
  switchTab(tab)
  sourcesSidebarOpen.value = false
  analysisSidebarOpen.value = false
}
const openNotes = () => {
  if (!isWorkspaceView.value) switchTab(chatTabEnabled.value ? 'chat' : 'search')
  analysisSidebarOpen.value = true
}
// The header toggles live outside the workspace tabs, so off a workspace tab
// they mean "take me back there with this panel open" rather than nothing.
const sourcesPanelShown = computed(() => isWorkspaceView.value && sourcesSidebarOpen.value)
const notesPanelShown = computed(() => isWorkspaceView.value && analysisSidebarOpen.value)
const toggleSources = () => {
  if (!isWorkspaceView.value) {
    switchTab(chatTabEnabled.value ? 'chat' : 'search')
    sourcesSidebarOpen.value = true
    return
  }
  sourcesSidebarOpen.value = !sourcesSidebarOpen.value
}
const toggleNotes = () => {
  if (notesPanelShown.value) analysisSidebarOpen.value = false
  else openNotes()
}

// Most recent active job, surfaced inline in the footer.
// The status bar has room for one job, so it shows the one doing work.
// A job waiting for a slot is the less informative of the two, and it
// sorts first by start time.
const footerActiveJob = computed(() => {
  const active = backgroundJobsStore.allJobs.filter(j => backgroundJobsStore.isActiveStatus(j.status))
  return active.find(j => !j.queuePosition) || active[0]
})

// Job presentation helpers, shared by the drawer and the footer.
// What the status bar says about the job in flight. "queued" and
// "cancelling" are states a person needs to see without opening a drawer.
const footerJobLabel = computed(() => {
  const job = footerActiveJob.value
  if (!job) return ''
  if (job.cancelRequested) return 'Cancelling…'
  if (job.queuePosition) return `Queued — ${ordinal(job.queuePosition)} in line`
  return job.currentFile || job.phase || jobTitle(job)
})

function jobTitle(job) {
  if (job.type === 'reindex') return 'Re-indexing'
  if (job.type === 'upload') return 'Upload'
  if (job.type === 'import') return 'Import'
  return 'Indexing'
}

function jobStatusLabel(job) {
  if (job.cancelRequested && backgroundJobsStore.isActiveStatus(job.status)) return 'cancelling'
  if (job.queuePosition) return 'queued'
  if (job.status === 'completed' && job.failedFiles.length) return 'completed with errors'
  return job.status
}

function jobBadgeClass(job) {
  if (job.cancelRequested || job.queuePosition) return 'badge-warning'
  if (job.status === 'running') return 'badge-info'
  if (job.status === 'pending') return 'badge-warning'
  if (job.status === 'completed') return job.failedFiles.length ? 'badge-warning' : 'badge-success'
  return 'badge-error'
}

function ordinal(n) {
  const suffix = ['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th'
  return `${n}${suffix}`
}

function jobOutcomeLine(job) {
  const total = job.totalFiles || 0
  if (job.status === 'completed') {
    const indexed = job.indexedFiles ?? job.processedFiles ?? 0
    return `Indexed ${indexed.toLocaleString()} of ${total.toLocaleString()} file${total === 1 ? '' : 's'}`
  }
  if (job.status === 'cancelled') {
    return `Stopped after ${(job.processedFiles || 0).toLocaleString()} of ${total.toLocaleString()} files`
  }
  return `Stopped at ${(job.processedFiles || 0).toLocaleString()} of ${total.toLocaleString()} files`
}

// Forward an Analysis-sidebar action into the chat input.
const handleSendToChat = (prompt) => {
  if (!chatTabEnabled.value) return
  activeTab.value = 'chat'
  // Wait one tick so ChatTab is mounted before we deliver the prompt.
  setTimeout(() => {
    window.dispatchEvent(new CustomEvent('clio:prefill-chat', { detail: { prompt } }))
  }, 50)
}

// Create collection modal (native <dialog> via useModal: focus trap,
// Escape, focus restore)
const createModal = useModal()
const newCollectionName = ref('')
const newCollectionDescription = ref('')
const newCollectionColor = ref('#3b82f6')
const newCollectionVisibility = ref('private')
const creatingCollection = ref(false)

// Export / import (portable .clio.zip bundles) live in their own dialogs.
const exportModal = ref(null)
const importModal = ref(null)
const openExportModal = (collection) => exportModal.value?.open(collection)

const onCollectionImported = async (data) => {
  await collectionStore.loadCollections()
  collectionStore.setCurrentCollection(data.id)
  backgroundJobsStore.addUploadJob({
    job_id: data.job_id, collection_id: data.id, status: 'pending',
    total_files: 1, processed_files: 0, job_type: 'import',
  })
  showJobsDrawer.value = true
  const how = data.vectors === 'reused'
    ? 'its vectors match this server, so it is ready as soon as keyword search is rebuilt'
    : "re-embedding its text with this server's model in the background"
  const blocked = data.documents_blocked ? ` ${data.documents_blocked} blocklisted source(s) were left out.` : ''
  ui.notify(`Imported ${data.name}: ${data.documents_imported} source(s), ${how}.${blocked}`, 'success', { duration: 8000 })
}

// Edit collection modal
const editModal = useModal()
const editingCollectionId = ref('')
const editCollectionName = ref('')
const editCollectionDescription = ref('')
const editCollectionColor = ref('#3b82f6')
const editCollectionGuide = ref('')
const editCollectionSensitivity = ref('internal')
const editCollectionPublished = ref(false)
// Publishing needs one accountable owner, so it is offered on collections
// you own — never on team collections, which belong to everybody and would
// end up locked with nobody able to unlock them (see api/collections).
const canPublishEditing = computed(() => {
  const c = collectionStore.collections.find(x => x.id === editingCollectionId.value)
  return userStore.privateCollections && !!c && c.permission === 'owner' && !c.team
})
const updatingCollection = ref(false)

// Delete confirmation modal
const deleteModal = useModal()
const deletingCollection = ref(false)
const deleteError = ref('')

// Share modal state
const showShareModal = ref(false)
const shareCollectionId = ref('')
const shareCollectionName = ref('')
const shareInitialToken = ref('')

// Background jobs drawer state
const showJobsDrawer = ref(false)

// Embedding-model-missing banner: the flag comes from /health (via
// statsStore), but dismissal is a per-session UI concern, not persisted.
const embeddingBannerDismissed = ref(false)
const embeddingWarming = ref(false)
// What the readiness bar says, derived from the stats store's live status.
const embeddingBanner = computed(() => {
  const e = statsStore.embedding
  if (!e) return statsStore.embeddingModelMissing
    ? { kind: 'missing', text: 'The search model is not on this server yet.', canDownload: false }
    : null
  const model = e.model ? ` (${e.model})` : ''
  if (e.status === 'downloading') {
    const p = e.progress || {}
    const mb = (n) => `${Math.round((n || 0) / 1048576)} MB`
    const detail = p.total_bytes ? ` · ${mb(p.downloaded_bytes)} of ${mb(p.total_bytes)}` : ''
    return { kind: 'progress', text: `Preparing the search model${model}${detail}`, percent: p.percent ?? null }
  }
  if (e.status === 'loading') return { kind: 'progress', text: `Loading the search model${model}`, percent: null }
  if (e.status === 'missing') {
    return {
      kind: 'missing',
      text: e.can_download
        ? `The search model${model} is not on this server yet. Download it once and it stays with your data.`
        : `The search model${model} is not on this server, and this network cannot download it. Choose another provider or pre-seed the model.`,
      canDownload: !!e.can_download,
    }
  }
  if (e.status === 'error') return { kind: 'error', text: `The search model could not be prepared: ${e.error || 'unknown error'}`, canDownload: true }
  return null
})
// A new problem after a dismissal should show again.
watch(() => statsStore.embedding?.status, () => { embeddingBannerDismissed.value = false })
const downloadEmbeddingModel = async () => {
  embeddingWarming.value = true
  try {
    await statsStore.warmEmbedding()
  } catch (err) {
    ui.toastError(err, 'Could not start the download')
  } finally {
    embeddingWarming.value = false
  }
}

// First-run onboarding: a full-screen takeover shown when the user has
// never configured an AI provider. Resolves the "you installed the app but
// nothing works until you curl an endpoint" problem we hit earlier.
const showOnboarding = ref(false)

const checkOnboardingNeeded = () => {
  // If any provider is already configured in localStorage, skip onboarding.
  // We intentionally don't also check server-side embedding keys — the
  // primary gate is "can this user have a chat conversation yet."
  // Users who pre-configured a chat provider via another path (e.g. the
  // MCP setup flow) shouldn't be blocked by this screen.
  showOnboarding.value = getConfiguredProviderIds().length === 0
    && localStorage.getItem('onboarding_dismissed') !== 'true'
}

const handleOnboardingComplete = () => {
  showOnboarding.value = false
  // Provider connected — the actual next blocker is having zero sources.
  // Point at the sidebar instead of dropping the user on an empty chat.
  if (statsStore.documents === 0) {
    sourcesSidebarOpen.value = true
    ui.highlightAddSources = true
    ui.notify('AI connected. Next: add your first sources in the left panel.', 'success', { duration: 8000 })
  }
}

const handleOnboardingSkip = () => {
  showOnboarding.value = false
  localStorage.setItem('onboarding_dismissed', 'true')
  activeTab.value = 'search'
  if (statsStore.documents === 0) sourcesSidebarOpen.value = true
}

// Collections overview: view, search, sort
const collectionsView = ref(localStorage.getItem('collections_view') || 'list')
const collectionsSort = ref(localStorage.getItem('collections_sort') || 'recent')
const collectionsSearch = ref('')

const collectionsSortOptions = [
  { id: 'recent', label: 'Most recent' },
  { id: 'name', label: 'Name' },
  { id: 'docs', label: 'Most documents' },
]

const collectionsSortLabel = computed(
  () => collectionsSortOptions.find(o => o.id === collectionsSort.value)?.label || ''
)

watch(collectionsView, v => localStorage.setItem('collections_view', v))
watch(collectionsSort, v => localStorage.setItem('collections_sort', v))

// Reset search each time the user enters the collections tab
watch(activeTab, tab => {
  if (tab === 'collections') collectionsSearch.value = ''
})

const filteredCollections = computed(() => {
  const q = collectionsSearch.value.trim().toLowerCase()
  let list = [...collectionStore.sortedCollections]

  if (q) {
    list = list.filter(c =>
      c.name.toLowerCase().includes(q) ||
      (c.description && c.description.toLowerCase().includes(q))
    )
  }

  if (collectionsSort.value === 'name') {
    list.sort((a, b) => a.name.localeCompare(b.name))
  } else if (collectionsSort.value === 'docs') {
    list.sort((a, b) => (b.document_count || 0) - (a.document_count || 0))
  } else if (collectionsSort.value === 'recent') {
    list.sort((a, b) => {
      const ta = a.updated_at || a.created_at || ''
      const tb = b.updated_at || b.created_at || ''
      return tb.localeCompare(ta)
    })
  }

  // Pin the default collection first when not actively searching
  if (!q) {
    const i = list.findIndex(c => c.id === 'default')
    if (i > 0) list.unshift(list.splice(i, 1)[0])
  }

  // Stable partition: your collections first, then ones shared with you —
  // each group keeps the chosen sort order. The divider renders between.
  return [...list.filter(c => !c.shared), ...list.filter(c => c.shared)]
})

// Where the "Shared with me" divider goes in the overview (-1 = no divider:
// nothing shared, or everything shared).
const firstSharedIndex = computed(() => {
  const i = filteredCollections.value.findIndex(c => c.shared)
  return i > 0 ? i : -1
})

const updateThemeFromStorage = () => {
  const savedTheme = localStorage.getItem('theme')
  if (savedTheme) {
    document.documentElement.setAttribute('data-theme', savedTheme)
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.setAttribute('data-theme', prefersDark ? 'dark' : 'light')
  }
}

const handleDocumentDeleted = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    statsStore.fetchStats(),
    collectionStore.loadCollections()
  ])
}

const handleDataCleared = async () => {
  // Reload both stats and collections list (for document_count in dropdown)
  await Promise.all([
    statsStore.fetchStats(),
    collectionStore.loadCollections()
  ])
}

const onChatTabToggled = (enabled) => {
  chatTabEnabled.value = enabled
  if (!enabled && activeTab.value === 'chat') {
    activeTab.value = 'search'
  }
}

const switchTab = (tabName) => {
  activeTab.value = tabName
}

// Notification bell / footer indicator: land on the part of Admin that holds
// the thing being counted, not just the top of the page.
const openReview = (target = 'review') => {
  switchTab('admin')
  // The Admin tab is a lazy chunk, so its sections may not exist yet.
  const id = target === 'access' ? 'admin-access' : 'admin-review'
  let tries = 0
  const scrollWhenReady = () => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    else if (tries++ < 30) setTimeout(scrollWhenReady, 100)
  }
  nextTick(scrollWhenReady)
}

// ── Command palette and global shortcuts ──
// The palette is the one place every destination and action is listed, so
// anything reachable from the header is reachable from the keyboard too.
const chatStore = useChatStore()
const showPalette = ref(false)
const showShortcuts = ref(false)
const showAbout = ref(false)

const THEME_CHOICES = [
  { id: 'light', label: 'Light', icon: SunMedium },
  { id: 'dark', label: 'Dark', icon: Moon },
  { id: 'cupcake', label: 'Cupcake', icon: Palette },
  { id: 'nord', label: 'Nord', icon: Palette },
  { id: 'dracula', label: 'Dracula', icon: Palette },
]
const setTheme = (id) => {
  if (id) localStorage.setItem('theme', id)
  else localStorage.removeItem('theme')
  window.dispatchEvent(new CustomEvent('theme-changed'))
}

const focusChatInput = () => {
  switchTab('chat')
  nextTick(() => document.getElementById('chat-input')?.focus())
}
const focusSearchInput = () => {
  switchTab('search')
  nextTick(() => document.getElementById('search-query')?.focus())
}
const newChat = () => {
  chatStore.newSession(collectionStore.currentCollectionId)
  focusChatInput()
}
// The Sources panel owns its Add-sources flow; it listens for this event.
const openAddSources = () => {
  if (!isWorkspaceView.value) switchTab(chatTabEnabled.value ? 'chat' : 'search')
  sourcesSidebarOpen.value = true
  nextTick(() => window.dispatchEvent(new CustomEvent('clio:add-sources')))
}

const paletteCommands = computed(() => {
  const current = collectionStore.currentCollectionId
  const currentTheme = localStorage.getItem('theme')
  const cmds = []
  const add = (group, label, icon, run, extra = {}) => cmds.push({ id: `${group}:${label}`, group, label, icon, run, ...extra })

  if (chatTabEnabled.value) add('Go to', 'Ask', MessageSquare, () => switchTab('chat'), { keys: ['G', 'A'], keywords: 'chat question' })
  add('Go to', 'Find', Search, () => switchTab('search'), { keys: ['G', 'F'], keywords: 'search passages' })
  add('Go to', 'Connect', Plug, () => switchTab('mcp'), { keys: ['G', 'C'], keywords: 'mcp token claude chatgpt client' })
  add('Go to', 'Reports', FileText, () => switchTab('generate'), { keywords: 'generate briefing summary' })
  add('Go to', 'Saved instructions', BookOpen, () => switchTab('expertise'), { keywords: 'expertise packs' })
  if (userStore.adminConsole) add('Go to', 'Administration', Gauge, () => switchTab('admin'), { keywords: 'admin audit access' })
  add('Go to', 'Collections', Layers, () => switchTab('collections'), { keys: ['G', 'O'], keywords: 'overview all' })
  add('Go to', 'Settings', Settings, () => switchTab('settings'), { keys: ['G', 'S'], keywords: 'preferences provider model theme' })

  if (chatTabEnabled.value) {
    add('Ask', 'New chat', MessageSquarePlus, newChat, { keys: [MOD, 'J'] })
    add('Ask', 'Focus the question box', MessageSquare, focusChatInput)
    for (const session of chatStore.getSessions(current).slice(0, 8)) {
      if (!session.messages?.length) continue
      add('Recent chats', session.title || 'New chat', History, () => {
        chatStore.selectSession(current, session.id)
        switchTab('chat')
      }, { detail: `${session.messages.length} message${session.messages.length === 1 ? '' : 's'}`, keywords: 'chat session history' })
    }
  }
  add('Find', 'Search passages', Search, focusSearchInput, { keywords: 'find exact' })

  add('Sources', 'Add sources', FolderPlus, openAddSources, { keys: [MOD, 'U'], keywords: 'upload index file folder link' })
  add('Sources', sourcesPanelShown.value ? 'Hide sources panel' : 'Show sources panel', PanelLeft, toggleSources, { keys: [MOD, '\\'] })
  if (!isCompact.value) add('Sources', notesPanelShown.value ? 'Hide notes and tools' : 'Show notes and tools', StickyNote, toggleNotes, { keys: [MOD, '.'] })
  add('Sources', 'Background jobs', Bell, () => { showJobsDrawer.value = true }, { keywords: 'indexing progress' })

  for (const c of collectionStore.sortedCollections) {
    if (c.id === current) continue
    add('Switch collection', c.name, Layers, () => selectCollectionAndNavigate(c.id), {
      swatch: c.color,
      detail: `${c.document_count || 0} ${(c.document_count || 0) === 1 ? 'doc' : 'docs'}`,
      keywords: 'collection switch open',
    })
  }
  add('Switch collection', 'New collection', Plus, () => { switchTab('collections'); nextTick(openCreateCollectionModal) }, { keywords: 'create' })

  for (const t of THEME_CHOICES) {
    add('Theme', t.label, t.icon, () => setTheme(t.id), { hint: currentTheme === t.id ? 'current' : undefined, keywords: 'theme appearance colour color' })
  }
  add('Theme', 'Follow system', MonitorCog, () => setTheme(null), { hint: !currentTheme ? 'current' : undefined, keywords: 'theme auto os' })

  add('Help', 'Keyboard shortcuts', Keyboard, () => { showShortcuts.value = true }, { keys: ['?'] })
  add('Help', 'Documentation', BookOpen, () => window.open('https://github.com/jordanboyce/clio#readme', '_blank', 'noopener'))
  add('Help', 'Run setup again', Sparkles, () => { showOnboarding.value = true })
  add('Help', 'About Clio', Info, () => { showAbout.value = true }, { keywords: 'version whats new' })
  return cmds
})

// Two-key "go to" sequences (g then a letter) live here; the pending prefix
// expires quickly so a stray g never arms a later keystroke.
let goPrefixUntil = 0
const onGlobalKeydown = (e) => {
  if (e.defaultPrevented) return
  const key = e.key
  if (isMod(e) && !e.shiftKey && key.toLowerCase() === 'k') {
    e.preventDefault()
    showPalette.value = !showPalette.value
    return
  }
  if (showPalette.value) return
  if (isMod(e) && !e.shiftKey && key.toLowerCase() === 'j' && chatTabEnabled.value) { e.preventDefault(); newChat(); return }
  if (isMod(e) && !e.shiftKey && key === '\\') { e.preventDefault(); toggleSources(); return }
  if (isMod(e) && !e.shiftKey && key === '.' && !isCompact.value) { e.preventDefault(); toggleNotes(); return }
  if (isMod(e) && !e.shiftKey && key.toLowerCase() === 'u') { e.preventDefault(); openAddSources(); return }

  if (isTypingTarget(e.target) || e.ctrlKey || e.metaKey || e.altKey) return
  if (key === '?') { e.preventDefault(); showShortcuts.value = !showShortcuts.value; return }
  const now = Date.now()
  if (key === 'g' || key === 'G') { goPrefixUntil = now + 1200; return }
  if (goPrefixUntil > now) {
    goPrefixUntil = 0
    const target = { a: chatTabEnabled.value ? 'chat' : null, f: 'search', c: 'mcp', o: 'collections', s: 'settings' }[key.toLowerCase()]
    if (target) { e.preventDefault(); switchTab(target) }
  }
}

const selectCollectionAndNavigate = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
  activeTab.value = chatTabEnabled.value ? 'chat' : 'search'
}

// Picker: switch in place. Only the overview (which has no conversation to
// stay in) and tabs unrelated to a collection hand you back to Ask/Find.
const pickCollection = (collectionId) => {
  collectionStore.setCurrentCollection(collectionId)
  if (!['chat', 'search', 'generate', 'expertise'].includes(activeTab.value)) {
    activeTab.value = chatTabEnabled.value ? 'chat' : 'search'
  }
}

const openCreateCollectionModal = () => {
  newCollectionName.value = ''
  newCollectionDescription.value = ''
  newCollectionColor.value = '#3b82f6'
  newCollectionVisibility.value = 'private'
  createModal.open()
}

const createCollection = async () => {
  if (!newCollectionName.value.trim()) return

  creatingCollection.value = true
  try {
    const collection = await collectionStore.createCollection({
      name: newCollectionName.value.trim(),
      description: newCollectionDescription.value.trim(),
      color: newCollectionColor.value,
      visibility: newCollectionVisibility.value
    })
    collectionStore.setCurrentCollection(collection.id)
    createModal.close()
  } catch (err) {
    ui.toastError(err, 'Failed to create collection')
  } finally {
    creatingCollection.value = false
  }
}

const openEditCollectionModal = (collection) => {
  editingCollectionId.value = collection.id
  editCollectionName.value = collection.name
  editCollectionDescription.value = collection.description || ''
  editCollectionColor.value = collection.color || '#3b82f6'
  editCollectionGuide.value = collection.guide || ''
  editCollectionSensitivity.value = collection.sensitivity || 'internal'
  editCollectionPublished.value = !!collection.published
  editModal.open()
}

const updateCollection = async () => {
  if (!editCollectionName.value.trim()) return

  updatingCollection.value = true
  try {
    await collectionStore.updateCollection(editingCollectionId.value, {
      name: editingCollectionId.value === 'default' ? undefined : editCollectionName.value.trim(),
      description: editCollectionDescription.value.trim(),
      color: editCollectionColor.value,
      guide: editCollectionGuide.value,
      sensitivity: editCollectionSensitivity.value,
      published: canPublishEditing.value ? editCollectionPublished.value : undefined,
    })
    editModal.close()
  } catch (err) {
    ui.toastError(err, 'Failed to update collection')
  } finally {
    updatingCollection.value = false
  }
}

// Cloning is a background indexing job, so the jobs drawer is where the
// progress lives; switch to the copy straight away so the person lands in
// the collection they now own rather than the one they could only read.
const cloningId = ref('')
const startClone = async (collection) => {
  if (cloningId.value) return
  cloningId.value = collection.id
  try {
    const clone = await collectionStore.cloneCollection(collection.id)
    collectionStore.setCurrentCollection(clone.id)
    showJobsDrawer.value = true
    ui.notify(`Copying ${collection.name} — indexing in the background.`, 'success')
  } catch (err) {
    ui.toastError(err, 'Could not copy this collection')
  } finally {
    cloningId.value = ''
  }
}

const openShareModal = (collection) => {
  shareCollectionId.value = collection.id
  shareCollectionName.value = collection.name
  showShareModal.value = true
}

// Accept-only mode: teammates who own nothing yet still need a way to paste
// a share token — ShareModal hides the create/list sections without an id.
const openJoinSharedModal = () => {
  shareCollectionId.value = ''
  shareCollectionName.value = ''
  showShareModal.value = true
}

const handleShared = () => {
  collectionStore.loadCollections()
}

const confirmDeleteCollection = () => {
  deleteError.value = ''
  deleteModal.open()
}

const closeDeleteModal = () => {
  if (!deletingCollection.value) {
    deleteModal.close()
    deleteError.value = ''
  }
}

const deleteCollection = async () => {
  deletingCollection.value = true
  deleteError.value = ''
  try {
    const deletedCollectionId = editingCollectionId.value
    const wasCurrentCollection = collectionStore.currentCollectionId === deletedCollectionId

    await collectionStore.deleteCollection(deletedCollectionId)
    // Clear search history for the deleted collection
    searchStore.clearCollectionCache(deletedCollectionId)

    // Close modals
    deleteModal.close()
    editModal.close()
    deleteError.value = ''

    // Switch to default collection if we deleted the current one
    if (wasCurrentCollection) {
      collectionStore.setCurrentCollection('default')
    }

    // Reload stats for the new current collection
    await statsStore.fetchStats()
  } catch (err) {
    deleteError.value = err.message || 'Failed to delete collection. Please try again.'
  } finally {
    deletingCollection.value = false
  }
}

// Watch for collection changes to reload stats
watch(() => collectionStore.currentCollectionId, () => {
  statsStore.fetchStats()
})

// Watch for background jobs completing to refresh collection counts and
// stats. Keyed by job id: the list is sorted newest-first and jobs come
// and go, so comparing two arrays position by position compared the
// statuses of different jobs.
watch(
  () => backgroundJobsStore.allJobs.map(j => `${j.type}-${j.id}:${j.status}`),
  (now, before) => {
    if (!before) return
    const previous = new Map(before.map(entry => {
      const at = entry.lastIndexOf(':')
      return [entry.slice(0, at), entry.slice(at + 1)]
    }))
    const finished = now.some(entry => {
      const at = entry.lastIndexOf(':')
      const key = entry.slice(0, at)
      const status = entry.slice(at + 1)
      const was = previous.get(key)
      return was !== undefined && was !== status &&
        ['completed', 'failed', 'cancelled'].includes(status)
    })
    if (finished) {
      collectionStore.loadCollections()
      statsStore.fetchStats()
    }
  },
)

// Live stats refresh while a job is still indexing into the current
// collection (throttled inside the jobs store), so the footer counts and
// chat gate update as documents land instead of only at job completion.
watch(() => backgroundJobsStore.dataRefreshTick, () => {
  if (backgroundJobsStore.dataRefreshCollectionId === collectionStore.currentCollectionId) {
    statsStore.fetchStatsDebounced()
  }
})

// Coming back to the tab re-reads the job list. A job can be started from
// another tab, another device, or an agent over MCP, and this tab would
// otherwise keep saying "Idle" until someone reloaded it. Throttled, since
// focus fires generously.
let lastJobsRefresh = 0
const refreshJobsOnReturn = () => {
  if (document.visibilityState === 'hidden') return
  const now = Date.now()
  if (now - lastJobsRefresh < 15000) return
  lastJobsRefresh = now
  backgroundJobsStore.checkActiveJobs()
}

// Cancel an active upload job
const cancelJob = async (jobId) => {
  try {
    await backgroundJobsStore.cancelUploadJob(jobId)
  } catch (err) {
    ui.toastError(err, 'Failed to cancel the job')
  }
}

onMounted(async () => {
  // Check whether we need the first-run onboarding takeover. Done first so
  // the screen paints immediately — the rest of the boot continues behind it.
  checkOnboardingNeeded()

  // Share-invitation deep link (?share_token=... from emailed invites):
  // open the join dialog with the token prefilled, then clean the URL so a
  // reload doesn't re-prompt.
  const params = new URLSearchParams(window.location.search)
  const shareToken = params.get('share_token')
  if (shareToken) {
    shareInitialToken.value = shareToken
    openJoinSharedModal()
    window.history.replaceState({}, '', window.location.pathname)
  }
  // ?tab=admin deep link (report-notification emails point here).
  const wantedTab = params.get('tab')
  if (wantedTab && VALID_TABS.includes(wantedTab)) {
    activeTab.value = wantedTab
    window.history.replaceState({}, '', window.location.pathname)
  }

  // Who's signed in (Access deployments) — drives the account/sign-out menu.
  loadMe()

  // Server-stored team keys count as configured providers — the pill may
  // appear (or change) once they're known. providerStore reactivity takes
  // care of the refresh; no listeners needed.
  providerStore.loadServerProviders().then(() => {
    retireSurfaceOverrides()
    providerStore.touch()
    if (providerStore.configuredIds.length > 0) showOnboarding.value = false
  })

  // Load UI feature flags from server config
  try {
    const cfgResp = await http.get('/api/config')
    chatTabEnabled.value = cfgResp.data.enable_chat_tab ?? true
    if (!chatTabEnabled.value) showOnboarding.value = false
    if (!chatTabEnabled.value && activeTab.value === 'chat') {
      activeTab.value = 'search'
    }
  } catch { /* defaults to true */ }

  // Load user info
  await userStore.loadCurrentUser()
  if (userStore.adminConsole) reviewStore.start()

  // Load collections first
  await collectionStore.loadCollections()

  // Then load stats for current collection
  statsStore.fetchStats()

  // Check for any active background jobs
  backgroundJobsStore.checkActiveJobs()
  window.addEventListener('visibilitychange', refreshJobsOnReturn)
  window.addEventListener('focus', refreshJobsOnReturn)
  window.addEventListener('keydown', onGlobalKeydown)

  // Initialize theme
  updateThemeFromStorage()

  // Listen for theme changes (from Settings tab via custom event)
  window.addEventListener('theme-changed', () => {
    updateThemeFromStorage()
  })

  // Listen for storage changes (theme changed in another tab)
  window.addEventListener('storage', (e) => {
    if (e.key === 'theme') {
      updateThemeFromStorage()
    }
  })

  // Listen for system theme changes (only if user hasn't set a preference)
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (!localStorage.getItem('theme')) {
      updateThemeFromStorage()
    }
  })
})

onBeforeUnmount(() => {
  reviewStore.stop()
  backgroundJobsStore.cleanup()
  window.removeEventListener('visibilitychange', refreshJobsOnReturn)
  window.removeEventListener('focus', refreshJobsOnReturn)
  window.removeEventListener('keydown', onGlobalKeydown)
})
</script>
