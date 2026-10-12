<template>
  <div class="flex flex-col h-full min-h-0">

      <!-- Header row -->
      <div class="flex items-center justify-between flex-shrink-0 py-1.5 mb-1 border-b border-base-300/60">
        <div class="flex items-center gap-1.5 min-w-0">
          <Bot :size="14" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
          <form v-if="renaming" class="min-w-0" @submit.prevent="commitRename">
            <label for="chat-title-input" class="sr-only">Chat title</label>
            <input
              id="chat-title-input"
              ref="renameInputRef"
              v-model="renameDraft"
              class="input input-xs input-bordered w-64 max-w-full"
              maxlength="80"
              @keydown.esc.prevent="renaming = false"
              @blur="commitRename"
            />
          </form>
          <button
            v-else
            type="button"
            class="text-sm font-medium truncate text-base-content/80 hover:text-base-content text-left min-w-0"
            :class="{ 'cursor-default': messages.length === 0 }"
            :title="messages.length > 0 ? 'Rename this chat' : undefined"
            :aria-label="messages.length > 0 ? `Rename chat: ${activeSessionTitle}` : activeSessionTitle"
            @click="messages.length > 0 && startRename()"
          >{{ activeSessionTitle }}</button>
          <span v-if="messages.length > 0" class="text-xs text-base-content/40 ml-1 flex-shrink-0">
            · {{ messages.length }}
          </span>
        </div>

        <div class="flex items-center gap-0.5">

          <button
            v-if="messages.length > 0"
            class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-base-content"
            title="Export this chat as Markdown"
            aria-label="Export this chat as Markdown"
            @click="exportChat"
          >
            <Download :size="13" aria-hidden="true" />
          </button>

          <!-- Session switcher -->
          <div class="dropdown dropdown-end">
            <label
              tabindex="0"
              class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-base-content"
              title="Switch chat session"
              :aria-label="`Switch chat session. Current: ${activeSessionTitle}`"
              aria-haspopup="menu"
            >
              <History :size="13" aria-hidden="true" />
            </label>
            <ul tabindex="0" class="dropdown-content z-[60] menu p-2 shadow-lg bg-base-100 border border-base-300 rounded-box w-72 max-h-96 overflow-y-auto">
              <li class="menu-title">
                <span class="text-xs">Chat sessions ({{ sessions.length }})</span>
              </li>
              <li v-if="sessions.length > 4" class="px-1 pb-1">
                <label class="sr-only" for="chat-session-filter">Find a chat</label>
                <input
                  id="chat-session-filter"
                  v-model="sessionFilter"
                  type="search"
                  class="input input-xs input-bordered w-full"
                  placeholder="Find a chat…"
                  @click.stop
                  @keydown.stop
                />
              </li>
              <li v-if="sessionFilter && filteredSessions.length === 0" class="disabled">
                <span class="text-xs text-base-content/50">No chats match</span>
              </li>
              <li v-for="session in filteredSessions" :key="session.id">
                <div
                  class="flex items-start gap-2 group"
                  :class="{ 'bg-primary/10': session.id === activeSessionId }"
                >
                  <button class="flex-1 text-left" @click="switchSession(session.id)">
                    <div class="text-sm font-medium truncate">{{ session.title || 'New chat' }}</div>
                    <div class="text-xs text-base-content/50">
                      {{ session.messages.length }} message{{ session.messages.length !== 1 ? 's' : '' }}
                      · {{ formatRelativeTime(session.updatedAt) }}
                    </div>
                  </button>
                  <button
                    class="btn btn-xs btn-ghost btn-circle hover-reveal"
                    title="Delete session"
                    :aria-label="`Delete chat session: ${session.title || 'New chat'}`"
                    @click.stop="deleteSession(session.id)"
                  >
                    <Trash2 :size="12" />
                  </button>
                </div>
              </li>
            </ul>
          </div>

          <button
            class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-base-content"
            @click="startNewChat"
            title="New chat"
            aria-label="New chat"
          >
            <Plus :size="13" />
          </button>

          <button
            v-if="messages.length > 0"
            class="btn btn-xs btn-ghost btn-square text-base-content/50 hover:text-error"
            @click="clearChat"
            title="Clear this session"
            aria-label="Clear messages"
          >
            <Trash2 :size="13" />
          </button>
        </div>
      </div>

      <!-- Source scope: what this conversation is allowed to read. -->
      <div
        class="flex items-center gap-2 flex-shrink-0 mb-1 px-1 py-1 text-xs text-base-content/60"
        role="status"
        aria-live="polite"
      >
        <ListTree :size="13" class="text-base-content/40 flex-shrink-0" aria-hidden="true" />
        <span class="flex-1 min-w-0">
          <template v-if="scope === 'all'">
            All accessible collections<span v-if="selectionActive"> · Your source selection does not apply.</span>
          </template>
          <template v-else-if="selectionActive">
            Asking about <span class="font-semibold tabular-nums">{{ selectionStore.count }}</span>
            <span v-if="documentCount"> of {{ documentCount.toLocaleString() }}</span>
            selected {{ selectionStore.count === 1 ? 'source' : 'sources' }}
            <span class="text-base-content/50">· answers stay inside the selection</span>
          </template>
          <template v-else>{{ collectionStore.currentCollection?.name || 'Current collection' }} · All {{ documentCount.toLocaleString() }} sources</template>
        </span>
        <button v-if="scope === 'all' && selectionActive" class="btn btn-ghost btn-xs h-auto py-2" @click="scope = 'current'">Use selection</button>
        <button class="btn btn-ghost btn-xs" @click="$emit('show-sources')" title="Change the selection in the Sources panel">Sources</button>
        <button v-if="selectionActive" class="btn btn-ghost btn-xs" @click="selectionStore.clear()" title="Use every source again" aria-label="Clear source selection">Clear</button>
      </div>

      <!-- No providers configured notice (inline, always visible) -->
      <div v-if="!hasAnyProvider" class="notice notice-info flex-shrink-0">
        <Info :size="15" class="text-info" aria-hidden="true" />
        <span class="flex-1">Connect a model to ask questions, or find passages without one.</span>
        <button class="btn btn-xs btn-primary" @click="$emit('switch-tab', 'settings')">Connect a model</button>
        <button class="btn btn-xs btn-ghost" @click="$emit('switch-tab', 'search')">Find passages</button>
      </div>

      <!-- Chat Settings Drawer -->
      <AISettingsDrawer
        v-model:open="settingsDrawerOpen"
        title="Chat Settings"
        :provider-id="selectedProvider"
        v-model:top-k="topK"
        v-model:search-mode="searchMode"
        v-model:rerank="rerank"
        v-model:cache-threshold="cacheThreshold"
        top-k-label="Context chunks"
        :top-k-max="20"
        @switch-tab="$emit('switch-tab', $event)"
      >
        <template #advanced>
          <!-- Scope -->
          <div class="space-y-2">
            <span class="text-xs font-semibold text-base-content/60 uppercase tracking-wider">Scope</span>
            <div class="flex items-center gap-1 rounded-lg border border-base-300 p-1 bg-base-200/60">
              <button
                class="btn btn-xs gap-1 flex-1 transition-all"
                :class="scope === 'current' ? 'btn-primary' : 'btn-ghost'"
                @click="scope = 'current'"
              >
                <Layers :size="12" />
                Current
              </button>
              <button
                class="btn btn-xs gap-1 flex-1 transition-all"
                :class="scope === 'all' ? 'btn-secondary' : 'btn-ghost'"
                @click="scope = 'all'"
              >
                <Database :size="12" />
                All
              </button>
            </div>
          </div>
        </template>
      </AISettingsDrawer>

      <!-- No data notice / mid-ingest status -->
      <div v-if="documentCount === 0 && indexingActive" class="notice flex-shrink-0" role="status">
        <span class="loading loading-spinner loading-xs" aria-hidden="true"></span>
        <span>Indexing in progress — chat becomes available as documents land.</span>
      </div>
      <div v-else-if="indexingActive" class="notice flex-shrink-0" role="status">
        <span class="loading loading-spinner loading-xs" aria-hidden="true"></span>
        <span>{{ documentCount.toLocaleString() }} {{ documentCount === 1 ? 'source' : 'sources' }} indexed so far — indexing continues in the background.</span>
      </div>

      <!-- Error -->
      <div v-if="error" class="notice notice-error flex-shrink-0 items-start" role="alert">
        <CircleAlert :size="15" class="text-error mt-0.5" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <p>{{ error }}</p>
          <details v-if="errorDetail" class="mt-1 text-xs text-base-content/70">
            <summary class="cursor-pointer hover:text-base-content">Provider details</summary>
            <pre class="mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-words font-sans">{{ errorDetail }}</pre>
          </details>
        </div>
        <button class="side-icon-btn side-icon-btn-sm text-base-content/60" @click="dismissError" aria-label="Dismiss error"><X :size="13" aria-hidden="true" /></button>
      </div>

      <!-- Message list -->
      <div ref="messagesContainer" class="flex-1 overflow-y-auto space-y-4 min-h-0 pr-1">

        <!-- Empty state -->
        <div v-if="messages.length === 0" class="flex flex-col items-center justify-center h-full py-8 px-4">
          <Transition name="rise" appear>
            <div v-if="hasAnyProvider && documentCount > 0" class="w-full max-w-xl">
              <h3 class="text-[22px] leading-tight font-semibold tracking-tight text-base-content/90">
                {{ collectionStore.currentCollection?.name || 'Your collection' }}
              </h3>
              <p class="mt-1 text-sm text-base-content/55">
                <span class="tabular-nums">{{ documentCount.toLocaleString() }}</span> {{ documentCount === 1 ? 'source' : 'sources' }} ready.
                Ask a question, then open its citations to check the evidence.
              </p>
              <div class="mt-6" aria-live="polite">
                <p v-if="starters.length || startersLoading" class="side-label text-base-content/45 mb-1.5">From your sources</p>
                <template v-if="startersLoading && !starters.length">
                  <div class="space-y-2">
                    <div v-for="n in 3" :key="n" class="skeleton h-9 w-full rounded-lg" aria-hidden="true"></div>
                  </div>
                  <span class="sr-only">Reading your sources for suggested questions</span>
                </template>
                <ul v-else class="divide-y divide-base-300/50">
                  <li v-for="suggestion in starterList" :key="suggestion">
                    <button
                      type="button"
                      class="group w-full text-left py-2.5 flex items-start gap-3 text-[15px] leading-snug text-base-content/80 hover:text-base-content disabled:opacity-50"
                      :disabled="loading"
                      @click="askQuestion(suggestion)"
                    >
                      <ArrowUp :size="14" class="mt-1 flex-shrink-0 rotate-45 text-base-content/30 group-hover:text-primary transition-colors" aria-hidden="true" />
                      <span>{{ suggestion }}</span>
                    </button>
                  </li>
                </ul>
              </div>
              <p class="mt-8 text-xs text-base-content/40 hidden sm:block">
                <span class="kbd-hint">/</span> for commands in the box
                <span class="mx-1.5 text-base-content/25">·</span>
                <span class="kbd-hint">{{ MOD }}</span> <span class="kbd-hint">K</span> to jump anywhere
              </p>
            </div>
            <!-- Nothing indexed yet: one quiet invitation, not a warning. -->
            <div v-else-if="documentCount === 0 && !indexingActive" class="w-full max-w-md text-center">
              <div class="mx-auto w-12 h-12 rounded-2xl bg-base-content/[0.06] flex items-center justify-center">
                <FileText :size="20" class="text-base-content/45" aria-hidden="true" />
              </div>
              <h3 class="mt-4 text-[17px] font-semibold tracking-tight text-base-content/90">
                {{ collectionStore.currentCollection?.name || 'This collection' }} is empty
              </h3>
              <p class="mt-1.5 text-sm text-base-content/55 leading-relaxed">
                Add documents, a folder, a link or a recording. Clio indexes them locally and every answer cites the passage it came from.
              </p>
              <button type="button" class="btn btn-sm btn-primary mt-5 gap-1.5" @click="$emit('show-sources'); $emit('add-sources')">
                <Plus :size="14" aria-hidden="true" />
                Add sources
              </button>
              <p class="mt-3 text-xs text-base-content/40 hidden sm:block"><span class="kbd-hint">{{ MOD }}</span> <span class="kbd-hint">U</span> from anywhere</p>
            </div>
          </Transition>
        </div>

        <!-- Messages: the reading room. A question is a quiet block on the
             right; the answer is prose on the page with no bubble, no avatar.
             New turns rise in; the list is keyed by position because it is
             append-only until it is cleared. -->
        <TransitionGroup name="rise" tag="div" class="space-y-7 max-w-3xl mx-auto w-full">
        <div v-for="(msg, index) in messages" :key="index">

          <!-- User message -->
          <div v-if="msg.role === 'user'" class="flex justify-end">
            <div class="max-w-[88%] sm:max-w-[75%] rounded-xl bg-base-content/[0.07] px-4 py-2.5">
              <p class="text-[15px] leading-relaxed whitespace-pre-wrap">{{ msg.content }}</p>
            </div>
          </div>

          <!-- Assistant message -->
          <div v-else class="flex flex-col gap-1">
            <div class="flex items-start max-w-full">
              <div class="answer-block flex-1 min-w-0">
                <div
                  v-if="msg.slashCommand"
                  class="whitespace-pre-wrap font-mono text-xs leading-snug rounded-lg bg-base-content/[0.05] px-3 py-2.5"
                >{{ msg.content }}</div>

                <!-- Initial "Thinking…" placeholder before any content arrives -->
                <div v-else-if="msg.streaming && !msg.content && (!msg.structuredResults || msg.structuredResults.length === 0)"
                  class="flex items-center gap-2 text-sm text-base-content/50 py-0.5">
                  <span class="loading loading-dots loading-xs text-primary"></span>
                  <span>Thinking…</span>
                </div>

                <!-- Structured query / metric results (rendered BEFORE the prose answer
                     so the synthesized response lands at the bottom of the message,
                     where the auto-scroll anchor keeps it in view as it streams) -->
                <details v-if="msg.structuredResults && msg.structuredResults.length > 0" class="mt-3 space-y-2" :open="msg.streaming || msg.structuredResults.some(sr => sr.error || sr.result?.partial_failure)">
                  <summary class="cursor-pointer text-xs text-base-content/70 py-2" aria-live="polite">
                    Research activity · {{ msg.structuredResults.filter(sr => sr.tool !== '_thinking').length }} steps
                    <span v-if="msg.streaming"> · Working…</span>
                    <span v-else-if="msg.structuredResults.some(sr => sr.error || sr.result?.partial_failure)"> · Some steps failed</span>
                  </summary>
                  <template v-for="(sr, srIdx) in msg.structuredResults" :key="srIdx">

                  <!-- Thinking breadcrumb: prose the agent emitted between tool calls -->
                  <div
                    v-if="sr.tool === '_thinking'"
                    class="flex items-start gap-2 text-xs text-base-content/50 italic px-1 py-0.5"
                  >
                    <Sparkles :size="11" class="mt-0.5 flex-shrink-0 text-base-content/30" />
                    <span class="whitespace-pre-wrap">{{ sr.result?.text }}</span>
                  </div>

                  <!-- Tool call card -->
                  <div
                    v-else
                    class="rounded-lg border bg-base-100 overflow-hidden transition-colors"
                    :class="sr.pending ? 'border-primary/40 bg-primary/5' : 'border-base-300'"
                  >
                    <div class="flex items-center gap-2 px-3 py-1.5 border-b"
                      :class="sr.pending ? 'bg-primary/10 border-primary/20' : 'bg-base-200 border-base-300'"
                    >
                      <!-- Spinning icon while pending, normal icon when done -->
                      <span v-if="sr.pending" class="loading loading-spinner loading-xs text-primary flex-shrink-0"></span>
                      <component v-else :is="toolIcon(sr.tool)" :size="12" :class="toolIconClass(sr.tool)" />
                      <span class="text-xs font-semibold">{{ toolLabel(sr) }}</span>
                      <span v-if="toolDetail(sr)" class="text-xs text-base-content/50 font-mono truncate">{{ toolDetail(sr) }}</span>
                      <span v-if="sr.pending" class="ml-auto text-xs text-primary/60 italic">running…</span>
                      <button
                        v-else
                        class="ml-auto btn btn-ghost btn-xs p-0 h-4 min-h-0 text-base-content/40"
                        @click="toggleStructuredDetail(srIdx, index)"
                        :title="isStructuredOpen(srIdx, index) ? 'Hide details' : 'Show details'"
                      >
                        <ChevronDown
                          :size="12"
                          class="transition-transform"
                          :class="isStructuredOpen(srIdx, index) ? 'rotate-180' : ''"
                        />
                      </button>
                    </div>

                    <!-- Error -->
                    <div v-if="sr.error" class="px-3 py-2 text-xs text-error">{{ sr.error }}</div>

                    <!-- Tabular result (query_table / get_table_rows / etc.) -->
                    <div
                      v-else-if="sr.result && sr.result.columns && sr.result.rows"
                      class="overflow-x-auto max-h-80"
                    >
                      <table class="table table-xs">
                        <thead>
                          <tr>
                            <th v-for="col in sr.result.columns" :key="col" class="text-xs">{{ col }}</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(row, rIdx) in sr.result.rows" :key="rIdx">
                            <td
                              v-for="(cell, cIdx) in row"
                              :key="cIdx"
                              class="text-xs font-mono"
                              :class="{ 'text-right': isNumeric(cell) }"
                            >{{ formatCell(cell) }}</td>
                          </tr>
                        </tbody>
                      </table>
                      <div v-if="sr.result.truncated" class="px-3 py-1 text-xs text-base-content/40 border-t border-base-300">
                        Showing first {{ sr.result.row_count }} rows (truncated)
                      </div>
                    </div>

                    <!-- Breakdown result -->
                    <div v-else-if="sr.result && sr.result.groups" class="overflow-x-auto max-h-80">
                      <table class="table table-xs">
                        <thead>
                          <tr>
                            <th class="text-xs">{{ sr.result.group_column || 'group' }}</th>
                            <th class="text-xs text-right">total</th>
                            <th class="text-xs text-right">count</th>
                            <th class="text-xs text-right">%</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(g, gIdx) in sr.result.groups" :key="gIdx">
                            <td class="text-xs">{{ g.group }}</td>
                            <td class="text-xs font-mono text-right">{{ formatCell(g.total) }}</td>
                            <td class="text-xs font-mono text-right">{{ g.count }}</td>
                            <td class="text-xs font-mono text-right">{{ g.pct != null ? g.pct.toFixed(2) + '%' : '' }}</td>
                          </tr>
                        </tbody>
                      </table>
                    </div>

                    <!-- Scalar metric result -->
                    <div v-else-if="sr.result && 'value' in sr.result" class="px-3 py-2 text-sm">
                      <span class="font-mono font-bold">{{ formatCell(sr.result.value) }}</span>
                      <span v-if="sr.result.column" class="ml-2 text-xs text-base-content/50">from "{{ sr.result.column }}"</span>
                    </div>

                    <!-- Document search results -->
                    <div v-else-if="['search_documents', 'research_documents'].includes(sr.tool) && sr.result?.results" class="px-3 py-2 space-y-1">
                      <p v-if="sr.result.partial_failure" class="text-xs text-warning">Some searches failed. Evidence may be incomplete.</p>
                      <ul v-if="sr.result.coverage" class="text-xs space-y-1 pb-2">
                        <li v-for="(item, coverageIndex) in sr.result.coverage" :key="coverageIndex">
                          {{ item.query }} · {{ item.status === 'weak_evidence' ? 'Weak evidence only' : item.evidence_ids.length ? 'Passages retrieved' : item.status === 'search_failed' ? 'Search failed' : 'No selected evidence' }}
                        </li>
                      </ul>
                      <div class="text-xs text-base-content/50">
                        {{ sr.result.total_results }} result{{ sr.result.total_results === 1 ? '' : 's' }}
                      </div>
                      <ul class="space-y-0.5">
                        <li
                          v-for="r in sr.result.results.slice(0, 6)"
                          :key="r.rank + r.filename"
                          class="text-xs flex items-baseline gap-2"
                        >
                          <span class="text-base-content/40 font-mono">#{{ r.rank }}</span>
                          <span class="font-medium truncate">{{ r.filename }}</span>
                          <span v-if="r.page_number" class="text-base-content/40">p.{{ r.page_number }}</span>
                          <span v-if="r.similarity_score != null" class="text-base-content/40 ml-auto font-mono">{{ Number(r.similarity_score).toFixed(3) }}</span>
                        </li>
                      </ul>
                    </div>

                    <!-- Document context fetch -->
                    <div v-else-if="sr.tool === 'get_document_context' && sr.result" class="px-3 py-2 text-xs">
                      <div class="text-base-content/50">
                        {{ sr.result.filename }}
                        <span v-if="sr.result.total_pages">· {{ sr.result.total_pages }} pages</span>
                        · {{ sr.result.total_chars }} chars retrieved
                      </div>
                    </div>

                    <!-- list_tables / list_collections / get_collection_info -->
                    <div v-else-if="sr.result?.tables" class="px-3 py-2 text-xs text-base-content/60">
                      {{ sr.result.tables.length }} table{{ sr.result.tables.length === 1 ? '' : 's' }}
                    </div>
                    <div v-else-if="sr.result?.collections" class="px-3 py-2 text-xs text-base-content/60">
                      {{ sr.result.collections.length }} collection{{ sr.result.collections.length === 1 ? '' : 's' }}
                    </div>

                    <!-- Generic key/value fallback -->
                    <div v-else-if="sr.result" class="px-3 py-2 text-xs font-mono space-y-0.5 max-h-48 overflow-y-auto">
                      <div v-for="(val, key) in sr.result" :key="key" class="flex gap-2">
                        <span class="text-base-content/50">{{ key }}:</span>
                        <span class="truncate">{{ formatCell(val) }}</span>
                      </div>
                    </div>

                    <!-- Details toggle (SQL, raw args) -->
                    <div v-if="isStructuredOpen(srIdx, index)" class="px-3 py-2 border-t border-base-300 bg-base-200/50">
                      <div v-if="sr.args?.sql" class="text-xs font-mono break-all text-base-content/60">
                        <span class="font-semibold">SQL:</span> {{ sr.args.sql }}
                      </div>
                      <div v-else-if="sr.args" class="text-xs font-mono text-base-content/60">
                        {{ JSON.stringify(sr.args) }}
                      </div>
                    </div>
                  </div>
                  </template>
                </details>

                <!-- Synthesized prose answer — streams in below the tool cards so
                     auto-scroll keeps the final response visible. -->
                <div v-if="!msg.slashCommand && (msg.content || msg.streaming)" class="relative"
                  :class="{ 'mt-3': msg.structuredResults && msg.structuredResults.length > 0 }">
                  <AnswerEvidence :content="msg.content" :sources="msg.sources || []" :streaming="msg.streaming" answer />
                  <!-- Blinking cursor while streaming -->
                  <span v-if="msg.streaming && msg.content" class="answer-caret" aria-hidden="true"></span>
                  <!-- Post-tool "Synthesizing…" hint: tools finished, prose not started -->
                  <div
                    v-if="msg.streaming && !msg.content && msg.structuredResults?.length > 0 && !msg.structuredResults.some(sr => sr.pending)"
                    class="flex items-center gap-2 text-xs text-base-content/50 py-0.5"
                  >
                    <span class="loading loading-dots loading-xs text-primary"></span>
                    <span>Synthesizing answer…</span>
                  </div>
                </div>

                <!-- Footer: one muted line of provenance, actions on the right -->
                <div
                  v-if="!msg.streaming && (msg.aiUsage || msg.content)"
                  class="flex items-center gap-x-2 gap-y-1 mt-2.5 flex-wrap text-xs text-base-content/45"
                >
                  <span v-if="msg.depth" class="inline-flex items-center gap-1" :title="msg.depth === 'quick' ? 'Quick answer: one pass from the retrieved passages' : 'Research answer: document searches and verification ran before answering'">
                    <Zap v-if="msg.depth === 'quick'" :size="10" aria-hidden="true" />
                    <Search v-else :size="10" aria-hidden="true" />
                    {{ msg.depth === 'quick' ? 'Quick' : 'Research' }}
                  </span>
                  <template v-if="msg.cached">
                    <span aria-hidden="true">·</span>
                    <span :title="msg.cachedQuestion ? `Cached answer originally generated for: ${msg.cachedQuestion}` : 'Served from the answer cache'">
                      cached<template v-if="msg.cachedSimilarity != null && msg.cachedSimilarity < 0.9995"> · {{ Math.round(msg.cachedSimilarity * 100) }}% match</template>
                    </span>
                  </template>
                  <template v-else-if="msg.aiUsage">
                    <span aria-hidden="true">·</span>
                    <span>{{ providerDisplayName(msg.provider || selectedProvider) }}</span>
                    <span aria-hidden="true">·</span>
                    <span class="tabular-nums" :title="`${msg.aiUsage.total_input_tokens} in · ${msg.aiUsage.total_output_tokens} out`">{{ formatTokens(msg.aiUsage.total_input_tokens + msg.aiUsage.total_output_tokens) }} tokens</span>
                    <template v-if="msg.aiUsage.features_used?.includes('reranking')"><span aria-hidden="true">·</span><span>reranked</span></template>
                    <template v-if="msg.aiUsage.features_used?.includes('structured_tools')"><span aria-hidden="true">·</span><span class="inline-flex items-center gap-1"><Table2 :size="10" aria-hidden="true" />tables</span></template>
                    <template v-if="msg.scope === 'all'"><span aria-hidden="true">·</span><span>all collections</span></template>
                  </template>
                  <span class="ml-auto"></span>
                  <template v-if="msg.content && index === lastAssistantIndex">
                    <button
                      v-if="msg.depth === 'quick'"
                      class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 gap-1 text-base-content/50 hover:text-base-content"
                      title="Answer again with document searches and verification"
                      :disabled="loading"
                      @click="rerunAnswer(index, { depth: 'research' })"
                    >
                      <Search :size="11" aria-hidden="true" />
                      <span class="text-xs">Go deeper</span>
                    </button>
                    <button
                      class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 gap-1 text-base-content/50 hover:text-base-content"
                      :title="msg.cached ? 'Bypass the cache and generate a fresh answer' : 'Generate this answer again'"
                      :aria-label="msg.cached ? 'Generate a fresh answer' : 'Regenerate this answer'"
                      :disabled="loading"
                      @click="rerunAnswer(index)"
                    >
                      <RefreshCw :size="11" aria-hidden="true" />
                      <span class="text-xs">{{ msg.cached ? 'Fresh answer' : 'Regenerate' }}</span>
                    </button>
                  </template>
                  <button
                    v-if="msg.content"
                    class="btn btn-ghost btn-xs h-5 min-h-0 px-1.5 text-base-content/50 hover:text-base-content gap-1"
                    :title="copiedMessageIndex === index ? 'Copied!' : 'Copy answer with evidence'"
                    :aria-label="copiedMessageIndex === index ? 'Copied to clipboard' : 'Copy answer with evidence to clipboard'"
                    @click="copyMessage(msg, index)"
                  >
                    <Check v-if="copiedMessageIndex === index" :size="11" class="text-success" />
                    <Copy v-else :size="11" />
                    <span class="text-xs">{{ copiedMessageIndex === index ? 'Copied' : 'Copy with evidence' }}</span>
                  </button>
                </div>

                <!-- Related: what a reader of this answer asks next. One tap asks it. -->
                <div
                  v-if="!msg.streaming && msg.relatedQuestions && msg.relatedQuestions.length"
                  class="mt-3 pt-2 border-t border-base-300/70"
                  data-testid="related-questions"
                >
                  <div class="flex items-center gap-1 text-xs font-medium text-base-content/60 mb-0.5">
                    <Sparkles :size="11" class="text-base-content/40" aria-hidden="true" />
                    Related
                  </div>
                  <ul class="divide-y divide-base-300/60">
                    <li v-for="question in msg.relatedQuestions" :key="question">
                      <button
                        type="button"
                        class="w-full text-left text-sm py-1.5 flex items-start gap-2 text-base-content/80 hover:text-primary disabled:opacity-50"
                        :disabled="loading"
                        @click="askQuestion(question)"
                      >
                        <Plus :size="13" class="mt-0.5 flex-shrink-0 text-base-content/40" aria-hidden="true" />
                        <span>{{ question }}</span>
                      </button>
                    </li>
                  </ul>
                </div>
              </div>
            </div>

          </div>

        </div>

        <!-- Waiting for the first SSE event -->
        <div v-if="loading && !hasStreamingMessage" key="connecting" class="flex items-center gap-2 text-sm text-base-content/50 max-w-3xl mx-auto w-full">
          <span class="loading loading-dots loading-xs text-primary"></span>
          <span>Connecting…</span>
        </div>
        </TransitionGroup>

        <div ref="messagesEnd"></div>
      </div>

      <!-- Input area -->
      <div class="flex-shrink-0 pt-3">

        <div
          class="relative rounded-2xl border border-base-300 bg-base-200/60 focus-within:border-primary/50 focus-within:ring-1 focus-within:ring-primary/20 transition-all"
        >
          <!-- Slash command picker (floats above the input) -->
          <SlashCommandPicker
            ref="slashPickerRef"
            :show="slashPickerOpen"
            :model-value="inputMessage"
            @select="onSlashSelect"
            @close="slashPickerOpen = false"
          />

          <!-- Textarea -->
          <label for="chat-input" class="sr-only">Ask a question about your documents</label>
          <textarea
            id="chat-input"
            ref="chatInputRef"
            v-model="inputMessage"
            class="composer-input w-full bg-transparent border-none outline-none resize-none text-[15px] leading-relaxed px-4 pt-3 pb-1 placeholder:text-base-content/35"
            rows="1"
            placeholder="Ask about your sources…"
            :disabled="loading"
            @input="onInputChange"
            @keydown="onKeydown"
            @blur="onInputBlur"
          ></textarea>

          <!-- Bottom toolbar -->
          <div class="flex items-center justify-between px-3 pb-2">
            <!-- Left: inline controls -->
            <div class="flex items-center gap-1.5">
              <button
                class="btn btn-ghost btn-xs btn-circle"
                @click="settingsDrawerOpen = !settingsDrawerOpen"
                title="Chat settings"
                aria-label="Open chat settings"
                :aria-expanded="settingsDrawerOpen"
              >
                <SlidersHorizontal :size="14" />
              </button>
              <button
                class="btn btn-ghost btn-xs btn-circle font-mono"
                @mousedown.prevent="toggleSlashPicker"
                title="Slash commands"
                aria-label="Show slash commands"
                :aria-expanded="slashPickerOpen"
              >
                /
              </button>
              <!-- Answer depth: the one-tap Quick / Research choice -->
              <div class="join rounded-full border border-base-300" role="radiogroup" aria-label="Answer depth">
                <button
                  type="button"
                  class="btn btn-xs join-item gap-1 border-0 rounded-l-full"
                  :class="depth === 'quick' ? 'btn-active' : 'btn-ghost text-base-content/60'"
                  role="radio"
                  :aria-checked="depth === 'quick'"
                  title="Quick: one pass from the retrieved passages. Fastest."
                  @click="depth = 'quick'"
                >
                  <Zap :size="12" aria-hidden="true" />
                  Quick
                </button>
                <button
                  type="button"
                  class="btn btn-xs join-item gap-1 border-0 rounded-r-full"
                  :class="depth === 'research' ? 'btn-active' : 'btn-ghost text-base-content/60'"
                  role="radio"
                  :aria-checked="depth === 'research'"
                  title="Research: runs document searches and verification before answering. Slower, more thorough."
                  @click="depth = 'research'"
                >
                  <Search :size="12" aria-hidden="true" />
                  Research
                </button>
              </div>
              <span v-if="selectedProvider" class="badge badge-xs hidden sm:inline-flex" :class="providerBadgeClass(selectedProvider)">{{ providerDisplayName(selectedProvider) }}</span>
              <span v-if="rerank" class="badge badge-xs badge-outline badge-primary hidden sm:inline-flex">Rerank</span>
            </div>

            <!-- Right: send, or stop while an answer is streaming -->
            <button
              v-if="loading"
              type="button"
              class="btn btn-circle btn-sm btn-neutral transition-all"
              @click="stopStreaming"
              title="Stop generating"
              aria-label="Stop generating"
            >
              <Square :size="12" fill="currentColor" aria-hidden="true" />
            </button>
            <button
              v-else
              type="button"
              class="btn btn-circle btn-sm btn-primary transition-all"
              :class="{ 'btn-disabled opacity-40': sendDisabled }"
              :disabled="sendDisabled"
              @click="sendMessage"
              title="Send message"
              aria-label="Send message"
            >
              <ArrowUp :size="16" aria-hidden="true" />
            </button>
          </div>
        </div>
        <p class="hidden sm:block text-[11px] text-base-content/30 mt-1.5 text-center">
          <span class="kbd-hint">Enter</span> to send · <span class="kbd-hint">Shift</span> <span class="kbd-hint">Enter</span> for a new line · <span class="kbd-hint">/</span> for commands
        </p>
      </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, nextTick, watch } from 'vue'
import http from '../utils/http'
import AnswerEvidence from './AnswerEvidence.vue'
import { answerWithReferences } from '../utils/answerEvidence'
import { Bot, FileText, ArrowUp, Trash2, Layers, Database, Plus, History, ChevronDown, SlidersHorizontal, Table2, Search, BookOpen, ListTree, Wrench, Sparkles, Copy, Check, RefreshCw, Zap, Download, Square, Info, CircleAlert, X } from 'lucide-vue-next'
import { useChatStore } from '../stores/chatStore'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useProviderStore } from '../stores/providerStore'
import { useStatsStore } from '../stores/statsStore'
import { useSelectionStore } from '../stores/selectionStore'
import SlashCommandPicker from './SlashCommandPicker.vue'
import AISettingsDrawer from './AISettingsDrawer.vue'
import { runSlashCommand, isSlashCommand } from '../utils/slashCommands'
import { MOD } from '../utils/shortcuts'
import { presentChatError } from '../utils/chatErrors'
import {
  getConfiguredProviderIds,
  buildProviderHeaders,
  getAPIProviderName,
  getProviderDisplayName,
  resolveProvider,
} from '../utils/aiProviders.js'

const emit = defineEmits(['switch-tab', 'show-sources', 'add-sources'])

const chatStore = useChatStore()
const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()
const providerStore = useProviderStore()
const statsStore = useStatsStore()
const selectionStore = useSelectionStore()
// The sidebar selection scopes this conversation (see stores/selectionStore).
const selectionActive = computed(() => selectionStore.active)

// documentCount is the real "is there anything indexed" signal — CSV/XLSX
// files live entirely in the structured SQL store and produce zero chunks,
// so a chunk count alone would falsely trigger the "no data" banner for
// users whose only sources are tabular. Read from statsStore (previously
// prop-drilled from App).
const documentCount = computed(() => statsStore.documents)

// A job is actively indexing into the current collection. With zero documents
// this softens the "no data" warning into an info banner; with documents it
// annotates that more are landing (retrieval already works mid-ingest).
const indexingActive = computed(() =>
  backgroundJobsStore.allJobs.some(j =>
    (j.status === 'running' || j.status === 'pending') &&
    j.collectionId === collectionStore.currentCollectionId
  )
)

// UI state
const loading = ref(false)
const error = ref('')
const errorDetail = ref('')
const inputMessage = ref('')
const messagesEnd = ref(null)
const messagesContainer = ref(null)
const settingsDrawerOpen = ref(false)

// Chat options (persisted)
const topK = ref(parseInt(localStorage.getItem('chat_top_k') || '5'))
const searchMode = ref(localStorage.getItem('chat_search_mode') || 'hybrid')
const scope = ref(localStorage.getItem('chat_scope') || 'current')
const rerank = ref(localStorage.getItem('chat_rerank') === 'true')
// Answer-cache similarity floor. A stored value is the user's own choice;
// otherwise the slider seeds from the deployment default (GET /api/chat/cache)
// and null hides it when the cache is off.
const storedCacheThreshold = localStorage.getItem('chat_cache_threshold')
const cacheThreshold = ref(storedCacheThreshold != null ? Number(storedCacheThreshold) : 0.9)
let seedingCacheThreshold = false
const seedCacheThreshold = async () => {
  try {
    const { data } = await http.get('/api/chat/cache')
    seedingCacheThreshold = true
    if (data.enabled === false) cacheThreshold.value = null
    else if (storedCacheThreshold == null && typeof data.threshold === 'number') cacheThreshold.value = data.threshold
    await nextTick()
  } catch {
    // the slider keeps its local value
  } finally {
    seedingCacheThreshold = false
  }
}
// Answer depth. Quick answers in one pass from the retrieved passages (large
// tables stay queryable); Research runs document searches and verification
// first. Quick is the default: time-to-answer is the product's metric, and
// "Go deeper" on any quick answer is one click.
const DEPTHS = ['quick', 'research']
const depth = ref(DEPTHS.includes(localStorage.getItem('chat_depth')) ? localStorage.getItem('chat_depth') : 'quick')
// Set by "Go deeper" / "Regenerate" for the one turn they trigger.
const depthOverride = ref('')

// Provider state. Chat has no provider or model of its own: it follows the
// one choice made in the top bar (see aiProviders.js). The configured list is
// a ref because it can grow after mount, when server-stored team keys arrive.
const configuredProviders = ref(getConfiguredProviderIds())
const globalProvider = ref(resolveProvider())
const selectedProvider = computed(() => globalProvider.value)
const hasAnyProvider = computed(() => configuredProviders.value.length > 0)

// Re-read the provider state. Called on mount, after team keys arrive, and
// whenever the top-bar picker changes the global provider.
const refreshProviders = () => {
  configuredProviders.value = getConfiguredProviderIds()
  globalProvider.value = resolveProvider()
}

const sendDisabled = computed(() => {
  if (!inputMessage.value.trim() || loading.value) return true
  // Slash commands don't need a provider or indexed content —
  // they read collection metadata directly.
  if (isSlashCommand(inputMessage.value)) return false
  return !hasAnyProvider.value || documentCount.value === 0
})

// True while an SSE streaming message is in-flight (has been added to store but not finalized)
const hasStreamingMessage = computed(() =>
  messages.value.some(m => m.streaming === true)
)

const messages = computed(() => chatStore.getMessages(collectionStore.currentCollectionId))
const sessions = computed(() => chatStore.getSessions(collectionStore.currentCollectionId))
const activeSessionId = computed(() => chatStore.getActiveSessionId(collectionStore.currentCollectionId))
const activeSessionTitle = computed(() => {
  const s = sessions.value.find((x) => x.id === activeSessionId.value)
  return s?.title || 'New chat'
})
const lastAssistantIndex = computed(() => {
  for (let i = messages.value.length - 1; i >= 0; i--) {
    if (messages.value[i].role === 'assistant' && !messages.value[i].slashCommand) return i
  }
  return -1
})

// ── Session library: find, rename, export ────────────────────────────────
const sessionFilter = ref('')
const filteredSessions = computed(() => {
  const q = sessionFilter.value.trim().toLowerCase()
  if (!q) return sessions.value
  return sessions.value.filter((s) =>
    (s.title || '').toLowerCase().includes(q) ||
    (s.messages || []).some((m) => m.role === 'user' && String(m.content || '').toLowerCase().includes(q))
  )
})

const renaming = ref(false)
const renameDraft = ref('')
const renameInputRef = ref(null)
const startRename = () => {
  renameDraft.value = activeSessionTitle.value
  renaming.value = true
  nextTick(() => { renameInputRef.value?.focus(); renameInputRef.value?.select() })
}
const commitRename = () => {
  if (!renaming.value) return
  renaming.value = false
  const title = renameDraft.value.trim()
  if (title && title !== activeSessionTitle.value) {
    chatStore.renameSession(collectionStore.currentCollectionId, activeSessionId.value, title)
  }
}

const exportChat = () => {
  const collectionName = collectionStore.currentCollection?.name || collectionStore.currentCollectionId
  const lines = [`# ${activeSessionTitle.value}`, '', `Collection: ${collectionName}`, `Exported: ${new Date().toLocaleString()}`, '']
  for (const m of messages.value) {
    if (m.streaming) continue
    if (m.role === 'user') lines.push(`## ${m.content}`, '')
    else lines.push(answerWithReferences(m), '')
  }
  const slug = activeSessionTitle.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'chat'
  const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = `${slug}.md`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(link.href)
}

const formatRelativeTime = (ts) => {
  if (!ts) return ''
  const diff = Date.now() - ts
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return 'just now'
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}m ago`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr}h ago`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day}d ago`
  return new Date(ts).toLocaleDateString()
}

const startNewChat = () => {
  chatStore.newSession(collectionStore.currentCollectionId)
}

const switchSession = (sessionId) => {
  chatStore.selectSession(collectionStore.currentCollectionId, sessionId)
  // Close dropdown by blurring active element
  if (document.activeElement instanceof HTMLElement) document.activeElement.blur()
  scrollToBottom()
}

const deleteSession = (sessionId) => {
  if (confirm('Delete this chat session? This cannot be undone.')) {
    chatStore.deleteSession(collectionStore.currentCollectionId, sessionId)
  }
}

// ── Starter questions ────────────────────────────────────────────────────
// Written from the collection's own passages by the fast model (cached
// server-side per corpus version). The generic list is the fallback when no
// model can be reached or the collection has nothing to sample.
const FALLBACK_SUGGESTIONS = [
  'Summarize the key points in these documents',
  'Compare the requirements in these sources and cite any conflicts.',
  'Which deadlines and responsibilities are stated? Cite each source.',
  'What is missing from these documents that I should verify?',
]
const starters = ref([])
const startersLoading = ref(false)
let startersKey = ''
const starterList = computed(() => (starters.value.length ? starters.value : FALLBACK_SUGGESTIONS))
const startersKeyNow = () => [
  collectionStore.currentCollectionId,
  documentCount.value,
  selectionStore.active ? selectionStore.currentIds.join(',') : '',
  selectedProvider.value,
].join('|')

const loadStarters = async () => {
  if (!hasAnyProvider.value || documentCount.value === 0 || messages.value.length > 0) return
  const key = startersKeyNow()
  if (key === startersKey) return
  startersKey = key
  startersLoading.value = true
  try {
    const { data } = await http.post(
      `/api/chat/starters?collection_id=${encodeURIComponent(collectionStore.currentCollectionId)}`,
      {
        provider: getAPIProviderName(selectedProvider.value),
        document_ids: selectionStore.active ? selectionStore.currentIds : null,
      },
      { headers: buildProviderHeaders(selectedProvider.value) },
    )
    if (startersKeyNow() === key) starters.value = Array.isArray(data?.questions) ? data.questions : []
  } catch {
    // The generic suggestions stand in; nothing to surface.
    if (startersKeyNow() === key) starters.value = []
  } finally {
    if (startersKeyNow() === key) startersLoading.value = false
  }
}
watch(
  () => [startersKeyNow(), hasAnyProvider.value, messages.value.length === 0],
  ([key]) => {
    if (key !== startersKey) starters.value = []
    loadStarters()
  },
  { immediate: true },
)

// Expanded-details state for structured result cards: Set of "msgIdx:srIdx"
const openStructuredDetails = ref(new Set())
const structuredKey = (srIdx, msgIdx) => `${msgIdx}:${srIdx}`
const toggleStructuredDetail = (srIdx, msgIdx) => {
  const key = structuredKey(srIdx, msgIdx)
  const next = new Set(openStructuredDetails.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  openStructuredDetails.value = next
}
const isStructuredOpen = (srIdx, msgIdx) =>
  openStructuredDetails.value.has(structuredKey(srIdx, msgIdx))

const isNumeric = (v) => typeof v === 'number' || (typeof v === 'string' && v !== '' && !isNaN(Number(v)))

// Map agent tool names to a renderer label, icon, and primary detail.
const TOOL_META = {
  research_documents:      { label: 'Researching sources',   icon: Search,   color: 'text-info' },
  find_in_documents:       { label: 'Finding exact wording',  icon: Search,   color: 'text-info' },
  search_documents:        { label: 'Searching documents',  icon: Search,   color: 'text-info' },
  get_document_context:    { label: 'Reading document',     icon: BookOpen, color: 'text-info' },
  list_tables:             { label: 'Listing tables',       icon: ListTree, color: 'text-base-content/60' },
  get_table_schema:        { label: 'Inspecting schema',    icon: ListTree, color: 'text-base-content/60' },
  get_table_rows:          { label: 'Reading table rows',   icon: Table2,   color: 'text-success' },
  query_table:             { label: 'SQL query',            icon: Table2,   color: 'text-success' },
  aggregate_table:         { label: 'Aggregating table',    icon: Table2,   color: 'text-success' },
  list_collections:        { label: 'Listing collections',  icon: Database, color: 'text-base-content/60' },
  get_collection_info:     { label: 'Collection info',      icon: Database, color: 'text-base-content/60' },
}

const toolMeta = (name) => TOOL_META[name] || { label: name || 'tool', icon: Wrench, color: 'text-base-content/50' }
const toolIcon = (name) => toolMeta(name).icon
const toolIconClass = (name) => toolMeta(name).color
const toolLabel = (sr) => toolMeta(sr.tool).label
const toolDetail = (sr) => {
  const a = sr.args || {}
    return a.query || a.pattern || a.identifier || a.table || a.filename || a.document_id || ''
}

const formatCell = (v) => {
  if (v == null) return ''
  if (typeof v === 'number') {
    if (!isFinite(v)) return String(v)
    // Large numbers: group with commas and trim to 2 decimals for floats
    if (Number.isInteger(v)) return v.toLocaleString()
    return v.toLocaleString(undefined, { maximumFractionDigits: 4 })
  }
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  if (Array.isArray(v)) return `[${v.length} item${v.length === 1 ? '' : 's'}]`
  if (typeof v === 'object') {
    // Avoid the default "[object Object]" by showing compact JSON, trimmed.
    try {
      const s = JSON.stringify(v)
      return s.length > 200 ? s.slice(0, 200) + '…' : s
    } catch {
      return '[object]'
    }
  }
  const s = String(v)
  return s.length > 200 ? s.slice(0, 200) + '…' : s
}

const providerDisplayName = getProviderDisplayName

const providerBadgeClass = (provider) => ({
  'badge-primary': provider === 'anthropic',
  'badge-success': provider === 'openai',
  'badge-info': provider === 'ollama',
  'badge-secondary': provider === 'ollama_cloud',
  'badge-warning': provider === 'grok',
  'badge-accent': provider === 'google',
  'badge-neutral': !['anthropic','openai','ollama','ollama_cloud','grok','google'].includes(provider),
})

const scrollToBottom = async () => {
  await nextTick()
  messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
}

// ── Streaming auto-scroll ────────────────────────────────────────────────
// A single throttled scroll driven by the deep watch on messages (below):
// per-event scrolling during streaming thrashed layout on every token.
// Auto-scroll is skipped when the user has scrolled up to read.
const NEAR_BOTTOM_PX = 100

const isNearBottom = () => {
  const el = messagesContainer.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= NEAR_BOTTOM_PX
}

let autoScrollTimer = null
const throttledAutoScroll = () => {
  if (autoScrollTimer) return
  autoScrollTimer = setTimeout(async () => {
    autoScrollTimer = null
    if (!isNearBottom()) return
    await nextTick()
    messagesEnd.value?.scrollIntoView({ behavior: 'auto' })
  }, 100)
}

// ── Copy answer to clipboard ─────────────────────────────────────────────
const copiedMessageIndex = ref(null)
let copyResetTimer = null

const copyMessage = async (msg, index) => {
  if (!msg?.content) return
  try {
      await navigator.clipboard.writeText(answerWithReferences(msg))
    copiedMessageIndex.value = index
    if (copyResetTimer) clearTimeout(copyResetTimer)
    copyResetTimer = setTimeout(() => { copiedMessageIndex.value = null }, 1600)
  } catch (err) {
    console.warn('Clipboard write failed:', err)
  }
}

// A starter or related question is asked the moment it is tapped.
const askQuestion = async (question) => {
  if (loading.value || !question) return
  inputMessage.value = question
  await sendMessage()
}

// Slash command picker state
const slashPickerOpen = ref(false)
const slashPickerRef = ref(null)
const chatInputRef = ref(null)

const toggleSlashPicker = () => {
  slashPickerOpen.value = !slashPickerOpen.value
  if (slashPickerOpen.value) {
    nextTick(() => chatInputRef.value?.focus())
  }
}

// Browsers without `field-sizing: content` (Firefox, older Safari) get the
// same grow-with-the-message behaviour from a measured height.
const autoGrow = () => {
  const ta = chatInputRef.value
  if (!ta || CSS.supports?.('field-sizing', 'content')) return
  ta.style.height = 'auto'
  ta.style.height = `${Math.min(ta.scrollHeight, 224)}px`
}
watch(inputMessage, () => nextTick(autoGrow))

const onInputChange = () => {
  autoGrow()
  // Open the picker as soon as the input starts with "/" so suggestions
  // appear while the user is typing; close it again if they erase the slash.
  slashPickerOpen.value = inputMessage.value.trimStart().startsWith('/')
}

const onInputBlur = () => {
  // Delay so click/mousedown on picker items still registers.
  setTimeout(() => {
    slashPickerOpen.value = false
  }, 150)
}

// Touch keyboards: Enter inserts a newline and the send button sends — the
// convention on phones, where an accidental send is costlier than a tap.
// Physical keyboards keep Enter-to-send.
const coarsePointer = window.matchMedia('(pointer: coarse)')

const onKeydown = (e) => {
  // Let the picker handle navigation keys first when it's open.
  if (slashPickerOpen.value && slashPickerRef.value?.handleKeydown(e)) return

  if (e.key === 'Enter' && !e.shiftKey) {
    if (coarsePointer.matches) return
    e.preventDefault()
    sendMessage()
  }
}

const onSlashSelect = (cmd) => {
  // Auto-run on pick — the commands take no arguments today.
  inputMessage.value = cmd
  slashPickerOpen.value = false
  sendMessage()
}

const runInlineSlashCommand = async (input) => {
  chatStore.addMessage(collectionStore.currentCollectionId, { role: 'user', content: input })
  await scrollToBottom()

  const result = await runSlashCommand(input, {
    collectionId: collectionStore.currentCollectionId,
    collection: collectionStore.currentCollection,
  })

  chatStore.addAssistantMessage(
    collectionStore.currentCollectionId,
    { role: 'assistant', content: result.content, slashCommand: result.cmd },
    [],
    null,
  )
  await scrollToBottom()
}

// Set by "Regenerate" / "Go deeper": the next send bypasses the semantic
// answer cache (and its result replaces the entry).
const forceFresh = ref(false)

// Ask the question behind an answer again — always fresh, optionally at a
// different depth ("Go deeper" reruns a quick answer as research).
const rerunAnswer = async (index, { depth: nextDepth } = {}) => {
  if (loading.value) return
  const msgs = messages.value
  let userIdx = -1
  for (let i = index - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') { userIdx = i; break }
  }
  if (userIdx === -1) return
  const question = msgs[userIdx].content
  // Drop the question + its answer, then resend as a fresh exchange.
  msgs.splice(userIdx, msgs.length - userIdx)
  inputMessage.value = question
  forceFresh.value = true
  depthOverride.value = nextDepth || ''
  try {
    await sendMessage()
  } finally {
    forceFresh.value = false
    depthOverride.value = ''
  }
}

const sendMessage = async () => {
  if (!inputMessage.value.trim() || loading.value) return

  const userContent = inputMessage.value.trim()

  inputMessage.value = ''
  error.value = ''
  errorDetail.value = ''
  slashPickerOpen.value = false

  // Intercept slash commands before hitting the LLM — zero tokens, instant.
  if (isSlashCommand(userContent)) {
    await runInlineSlashCommand(userContent)
    return
  }

  // Refuse to send if no provider is configured.
  if (!hasAnyProvider.value) {
    error.value = 'Configure an AI provider in Settings to chat.'
    return
  }

  const collectionId = collectionStore.currentCollectionId
  chatStore.addMessage(collectionId, { role: 'user', content: userContent })
  await scrollToBottom()

  loading.value = true

  // Add the in-flight placeholder message immediately so the UI shows it.
  chatStore.addStreamingMessage(collectionId)
  await scrollToBottom()

  try {
    const providerHeaders = buildProviderHeaders(selectedProvider.value)
    // Pass only role+content to the API (strip UI-only fields like timestamps).
    const apiMessages = messages.value
      .filter(m => !m.streaming)
      .map(m => ({ role: m.role, content: m.content }))

    abortController = new AbortController()
    const response = await fetch(
      `/api/chat/stream?collection_id=${collectionId}`,
      {
        method: 'POST',
        signal: abortController.signal,
        headers: { 'Content-Type': 'application/json', ...providerHeaders },
        body: JSON.stringify({
          messages: apiMessages,
          provider: getAPIProviderName(selectedProvider.value),
          top_k: topK.value,
          mode: searchMode.value,
          scope: scope.value,
          rerank: rerank.value,
          use_cache: !forceFresh.value,
          cache_threshold: typeof cacheThreshold.value === 'number' ? cacheThreshold.value : null,
          depth: depthOverride.value || depth.value,
          related: true,
          // Selected sources bound the whole turn server-side (retrieval,
          // tool calls, tables, overview, cache). Ids belong to the current
          // collection, so they only travel with scope=current.
          document_ids: scope.value === 'current' && selectionStore.active ? selectionStore.currentIds : null,
        }),
      }
    )

    if (!response.ok) {
      const errBody = await response.json().catch(() => ({}))
      // 429 = rate limit or daily token budget (shared backend contract:
      // {"detail", "retry_after_seconds"}). This is a raw fetch, so the
      // axios interceptor never sees it — handle it here, in the thread.
      if (response.status === 429) {
        const secs = errBody.retry_after_seconds
        throw new Error(
          errBody.detail ||
          `Rate limit reached — try again in ${secs ? `${secs}s` : 'a moment'}.`
        )
      }
      throw new Error(errBody.detail || `HTTP ${response.status}`)
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      // SSE lines end with \n\n — split and process complete events.
      const parts = buffer.split('\n\n')
      buffer = parts.pop() // last part may be incomplete

      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const raw = line.slice(5).trim()
        if (!raw || raw === '[DONE]') continue

        let event
        try { event = JSON.parse(raw) } catch { continue }

        // Scrolling during streaming is handled by the throttled deep watch
        // on messages — no per-event scroll calls here.
        if (event.type === 'tool_start') {
          chatStore.addStreamingToolCall(collectionId, event.tool, event.args || {})
        } else if (event.type === 'tool_end') {
          chatStore.resolveStreamingToolCall(collectionId, event.tool, event.result || {})
        } else if (event.type === 'thinking') {
          chatStore.addStreamingThinking(collectionId, event.text || '')
        } else if (event.type === 'text_delta') {
          chatStore.appendStreamingText(collectionId, event.delta || '')
        } else if (event.type === 'sources') {
          // Sources will be committed in 'done'
        } else if (event.type === 'related') {
          chatStore.setStreamingRelated(collectionId, event.questions || [])
        } else if (event.type === 'done') {
          chatStore.finalizeStreamingMessage(collectionId, {
            sources: event.sources || [],
            usage: event.usage || {},
            structuredResults: event.structured_results || [],
            cached: event.cached || false,
            cachedQuestion: event.cached_question || '',
            cachedSimilarity: typeof event.cached_similarity === 'number' ? event.cached_similarity : null,
            relatedQuestions: event.related_questions || [],
            depth: event.depth || (depthOverride.value || depth.value),
          })
          // Stamp the last assistant message with provider/scope for the badge
          const msgs = messages.value
          for (let i = msgs.length - 1; i >= 0; i--) {
            if (msgs[i].role === 'assistant') {
              msgs[i].provider = selectedProvider.value
              msgs[i].scope = scope.value
              break
            }
          }
        } else if (event.type === 'error') {
          chatStore.removeLastStreamingMessage(collectionId)
          setChatError(event.message)
        }
      }
    }
  } catch (err) {
    if (err?.name === 'AbortError') {
      // The reader stopped: keep whatever prose arrived, drop an empty turn.
      const partial = messages.value.find(m => m.streaming)
      if (partial?.content) chatStore.finalizeStreamingMessage(collectionId, { depth: depthOverride.value || depth.value })
      else chatStore.removeLastStreamingMessage(collectionId)
    } else {
      chatStore.removeLastStreamingMessage(collectionId)
      setChatError(err.message)
    }
  } finally {
    abortController = null
    loading.value = false
  }
}

// Stop the in-flight answer. Aborting the fetch closes the SSE reader; the
// server notices the dropped connection and stops the provider call.
let abortController = null
const stopStreaming = () => {
  abortController?.abort()
}

const formatTokens = (n) => {
  if (!Number.isFinite(n)) return '0'
  if (n >= 10000) return `${(n / 1000).toFixed(0)}k`
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

const setChatError = (message) => {
  const presented = presentChatError(message)
  error.value = presented.message
  errorDetail.value = presented.detail
}

const dismissError = () => {
  error.value = ''
  errorDetail.value = ''
}

const clearChat = () => {
  if (confirm('Clear this conversation? This cannot be undone.')) {
    chatStore.clearMessages(collectionStore.currentCollectionId)
  }
}

// Persist options
watch(topK, (v) => localStorage.setItem('chat_top_k', String(v)))
watch(searchMode, (v) => localStorage.setItem('chat_search_mode', v))
watch(scope, (v) => localStorage.setItem('chat_scope', v))
watch(rerank, (v) => localStorage.setItem('chat_rerank', String(v)))
watch(cacheThreshold, (v) => {
  if (seedingCacheThreshold || typeof v !== 'number') return
  localStorage.setItem('chat_cache_threshold', String(v))
})
onMounted(seedCacheThreshold)
watch(depth, (v) => localStorage.setItem('chat_depth', v))

watch(messages, () => { throttledAutoScroll() }, { deep: true })

const handlePrefill = (e) => {
  const prompt = e?.detail?.prompt
  if (!prompt) return
  inputMessage.value = prompt
  nextTick(() => {
    const ta = document.querySelector('textarea[placeholder*="message" i], textarea')
    if (ta) ta.focus()
  })
}

// Re-resolve providers whenever any surface changes provider config —
// providerStore.version bumps on every write (replaces the old window
// 'clio:provider-changed' event).
watch(() => providerStore.version, refreshProviders)

onMounted(async () => {
  scrollToBottom()
  window.addEventListener('clio:prefill-chat', handlePrefill)

  // Pick up providers whose key lives on the server (team deployments):
  // they become selectable without the user ever entering a key.
  await providerStore.loadServerProviders()
  refreshProviders()
})

onBeforeUnmount(() => {
  window.removeEventListener('clio:prefill-chat', handlePrefill)
  if (autoScrollTimer) {
    clearTimeout(autoScrollTimer)
    autoScrollTimer = null
  }
})
</script>
