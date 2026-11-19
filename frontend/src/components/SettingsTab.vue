<template>
  <div class="max-w-4xl mx-auto">

    <!-- ── Page header: title + basic/advanced depth switch ── -->
    <header class="flex items-end justify-between gap-6 flex-wrap pb-5 mb-6 border-b border-base-300/60">
      <div class="min-w-0">
        <h1 class="text-[22px] leading-none font-semibold tracking-tight">Settings</h1>
        <p class="mt-2 text-xs text-base-content/50">
          {{ advanced ? 'Everything, including expert tuning' : 'The essentials — switch to Advanced for expert tuning' }}
        </p>
      </div>

      <div class="join" role="group" aria-label="Settings depth">
        <button
          class="btn btn-xs join-item px-3"
          :class="!advanced ? 'btn-active' : 'btn-ghost'"
          @click="settingsMode = 'basic'"
          :aria-pressed="!advanced"
        >Basic</button>
        <button
          class="btn btn-xs join-item px-3"
          :class="advanced ? 'btn-active' : 'btn-ghost'"
          @click="settingsMode = 'advanced'"
          :aria-pressed="advanced"
        >Advanced</button>
      </div>
    </header>

    <div class="flex flex-col sm:flex-row gap-4 sm:gap-10 items-stretch sm:items-start">

      <!-- ── Section rail ── -->
      <nav class="w-40 flex-shrink-0 sticky top-2 hidden sm:block" aria-label="Settings sections">
        <ul class="space-y-0.5">
          <li v-for="s in SECTIONS" :key="s.id">
            <button
              class="w-full text-left px-2.5 py-1.5 rounded-md text-[13px] transition-colors"
              :class="activeSection === s.id
                ? 'bg-base-200 font-semibold text-base-content'
                : 'text-base-content/55 hover:text-base-content hover:bg-base-200/50'"
              @click="activeSection = s.id"
              :aria-current="activeSection === s.id ? 'true' : undefined"
            >{{ s.label }}</button>
          </li>
        </ul>
      </nav>

      <!-- Mobile: section picker replaces the rail (inline at the top, so
           it never collides with the bottom tab bar) -->
      <div class="sm:hidden w-full">
        <select
          v-model="activeSection"
          class="select select-bordered select-sm w-full"
          aria-label="Settings section"
        >
          <option v-for="s in SECTIONS" :key="s.id" :value="s.id">{{ s.label }}</option>
        </select>
      </div>

      <!-- ── Content column ── -->
      <div class="flex-1 min-w-0 max-w-2xl pb-8">

        <!-- ═══ General ═══ -->
        <section v-show="activeSection === 'general'" aria-labelledby="settings-general">
          <h2 id="settings-general" class="sr-only">General</h2>

          <!-- Theme -->
          <div class="pb-8">
            <div class="text-sm font-medium">Theme</div>
            <p class="text-xs text-base-content/55 mt-1 mb-4">System matches your device's light or dark setting.</p>
            <div class="grid gap-2 grid-cols-[repeat(auto-fill,minmax(104px,1fr))] max-w-2xl">
              <button
                v-for="t in THEMES"
                :key="t.id"
                type="button"
                class="rounded-lg p-0 overflow-hidden text-left border transition-all"
                :class="selectedTheme === t.id
                  ? 'border-base-content/50 ring-1 ring-base-content/25'
                  : 'border-base-300 hover:border-base-content/30'"
                @click="selectedTheme = t.id; applyTheme()"
                :aria-pressed="selectedTheme === t.id"
                :aria-label="`Theme: ${t.label}`"
              >
                <!-- The inner div renders IN the candidate theme, so the
                     swatch is a live preview, not a hard-coded palette. -->
                <div :data-theme="t.id === 'system' ? systemTheme() : t.id" class="bg-base-100 px-3 pt-3 pb-2.5">
                  <div class="flex gap-1" aria-hidden="true">
                    <span class="w-3.5 h-3.5 rounded-full bg-primary"></span>
                    <span class="w-3.5 h-3.5 rounded-full bg-secondary"></span>
                    <span class="w-3.5 h-3.5 rounded-full bg-accent"></span>
                  </div>
                  <div class="mt-2 text-[11px] font-medium text-base-content flex items-center justify-between gap-1">
                    {{ t.label }}
                    <Check v-if="selectedTheme === t.id" :size="11" aria-hidden="true" />
                  </div>
                </div>
              </button>
            </div>
          </div>

          <!-- Interface (advanced) -->
          <div v-if="advanced" class="pt-6 border-t border-base-300/60">
            <p class="text-xs uppercase tracking-[0.14em] font-semibold text-base-content/50 mb-4">Advanced</p>
            <div class="flex items-start justify-between gap-8">
              <div class="min-w-0">
                <div class="text-sm font-medium">Chat tab</div>
                <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                  The built-in chat interface. Turn off if you work through Claude Desktop or another MCP client instead.
                </p>
              </div>
              <input
                type="checkbox"
                class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
                v-model="chatTabEnabled"
                @change="saveChatTabSetting"
                :disabled="serverConfigLocked"
                :title="serverConfigLocked ? 'Deployment-wide setting — admin only' : undefined"
                aria-label="Enable chat tab"
              />
            </div>
          </div>
        </section>

        <!-- ═══ Layout ═══ -->
        <section v-show="activeSection === 'layout'" aria-labelledby="settings-layout" data-testid="settings-layout">
          <h2 id="settings-layout" class="sr-only">Layout</h2>

          <div class="pb-6 space-y-4">
            <div>
              <div class="text-sm font-medium">Ribbon</div>
              <p class="text-xs text-base-content/55 mt-1 max-w-[56ch] leading-relaxed">
                The column of icons on the left edge. Choose what it holds and in what order, or right-click it
                any time. On a phone the bottom tab bar takes its place.
              </p>
            </div>
            <div class="flex items-start justify-between gap-8 max-w-xl">
              <div class="min-w-0">
                <div class="text-sm">Show names next to the icons</div>
                <p class="text-xs text-base-content/55 mt-0.5">Wider, and easier for anyone who does not know the icons yet.</p>
              </div>
              <input
                type="checkbox"
                class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
                :checked="layoutStore.ribbon.labels"
                @change="layoutStore.setRibbonLabels($event.target.checked)"
                aria-label="Show names next to the ribbon icons"
              />
            </div>
            <button class="btn btn-sm btn-outline" @click="layoutStore.customizeOpen = true">Customize the ribbon…</button>
          </div>

          <div class="pt-6 border-t border-base-300/60 space-y-3">
            <div>
              <div class="text-sm font-medium">Sources panel</div>
              <p class="text-xs text-base-content/55 mt-1 max-w-[56ch] leading-relaxed">
                Folders follow where sources came from: a synced folder or repository keeps its structure, links
                group by site, uploads sit at the top. The detailed list shows pages, labels and review status on
                every row.
              </p>
            </div>
            <div class="join" role="group" aria-label="Show sources as">
              <button class="btn btn-sm join-item" :class="layoutStore.sourcesView === 'tree' ? 'btn-active' : ''" :aria-pressed="layoutStore.sourcesView === 'tree'" @click="layoutStore.setSourcesView('tree')">Folders</button>
              <button class="btn btn-sm join-item" :class="layoutStore.sourcesView === 'list' ? 'btn-active' : ''" :aria-pressed="layoutStore.sourcesView === 'list'" @click="layoutStore.setSourcesView('list')">Detailed list</button>
            </div>
          </div>
        </section>

        <!-- ═══ AI Providers ═══ -->
        <section v-show="activeSection === 'providers'" aria-labelledby="settings-providers">
          <h2 id="settings-providers" class="sr-only">AI Providers</h2>

          <p v-if="!systemInfo.offline_mode" class="text-sm text-base-content/60 leading-relaxed max-w-[60ch] mb-6">
            The model that writes answers in Ask, Find and Reports. Keys stay in this browser and,
            once connected, on your own server so background work can use them.
          </p>
          <p v-else class="text-sm text-base-content/60 leading-relaxed max-w-[60ch] mb-6">
            <ShieldCheck :size="14" class="inline -mt-0.5 mr-1" aria-hidden="true" />This
            deployment runs air-gapped, so only models on your own network are offered: Ollama, or
            a self-hosted OpenAI-compatible server.
          </p>

          <!-- Empty state: nothing connected yet -->
          <p v-if="providerStore.serverLoaded && !hasAnyProvider" class="text-sm text-base-content/70 leading-relaxed max-w-[60ch] mb-6" data-testid="providers-empty">
            Nothing is connected yet. Search works without a model; connect one below to get written answers.
          </p>

          <!-- Providers, grouped by where the text goes -->
          <div class="space-y-8">
            <section v-for="group in providerGroups" :key="group.id" :aria-labelledby="`provider-group-${group.id}`" :data-testid="`provider-group-${group.id}`">
              <h3 :id="`provider-group-${group.id}`" class="text-sm font-semibold">{{ group.title }}</h3>
              <p class="text-xs text-base-content/60 mt-1 mb-3 max-w-[60ch] leading-relaxed">{{ group.note }}</p>

              <ul class="rounded-box border border-base-300 bg-base-100 divide-y divide-base-300 overflow-hidden">
                <li v-for="row in group.rows" :key="row.key" :data-testid="`provider-row-${row.key}`">
                  <!-- Deployment provider: configured on the server, shared by everyone. -->
                  <div v-if="row.kind === 'deployment'" class="px-4 py-3">
                    <div class="flex items-start gap-3">
                      <div class="flex-1 min-w-0">
                        <div class="font-medium text-sm">{{ row.name }}</div>
                        <div class="text-xs text-base-content/60 mt-0.5 truncate">
                          {{ deploymentDefault.model || 'Model chosen by the server' }}<template v-if="deploymentDefault.base_url"> · {{ deploymentDefault.base_url }}</template>
                        </div>
                      </div>
                      <div class="flex items-center gap-3 flex-shrink-0 text-xs">
                        <span v-if="deploymentIsDefault" class="font-medium text-base-content">Default</span>
                        <button v-else class="btn btn-ghost btn-sm" @click="setDefaultProvider(deploymentDefault.provider)">Use as default</button>
                        <span class="inline-flex items-center gap-1 text-base-content/70"><Check :size="13" aria-hidden="true" />Set by this server</span>
                      </div>
                    </div>
                    <p class="text-xs text-base-content/60 mt-2 max-w-[60ch] leading-relaxed">
                      Whoever runs this server set it up for everyone; there is nothing to enter here.
                    </p>
                  </div>

                  <template v-else>
                    <button
                      type="button"
                      class="w-full flex items-start gap-3 px-4 py-3 text-left hover:bg-base-200/50 transition-colors"
                      :aria-expanded="expandedProvider === row.id"
                      @click="toggleExpand(row.id)"
                    >
                      <span class="flex-1 min-w-0">
                        <span class="block font-medium text-sm">{{ row.name }}</span>
                        <span class="block text-xs text-base-content/60 mt-0.5 line-clamp-2 break-words">{{ rowDetail(row) }}</span>
                      </span>
                      <span class="flex items-center gap-3 flex-shrink-0 text-xs pt-0.5">
                        <span v-if="activeProviderId === row.id" class="font-medium text-base-content">Default</span>
                        <span v-if="row.kind === 'builtin' && isServerProvider(row.id) && !hasLocalKey(row.id)" class="inline-flex items-center gap-1 text-base-content/70"><Check :size="13" aria-hidden="true" />Team key</span>
                        <span v-else-if="row.connected" class="inline-flex items-center gap-1 text-base-content/70"><Check :size="13" aria-hidden="true" />Connected</span>
                        <span v-else class="text-base-content/50">Not connected</span>
                        <ChevronDown :size="14" class="text-base-content/40 transition-transform" :class="{ 'rotate-180': expandedProvider === row.id }" aria-hidden="true" />
                      </span>
                    </button>

                    <div v-if="expandedProvider === row.id" class="border-t border-base-300 bg-base-200/30 px-4 py-4 space-y-4">

                      <!-- Ollama -->
                      <template v-if="row.id === 'ollama'">
                        <div class="form-control">
                          <label class="label p-0 pb-1" for="ollama-base-url"><span class="label-text text-sm font-medium">Address</span></label>
                          <input id="ollama-base-url" v-model="editBuffer.baseUrl" type="url" placeholder="http://localhost:11434" class="input input-bordered input-sm w-full" />
                          <p class="text-xs text-base-content/60 mt-1">Change it to use Ollama on another machine.</p>
                        </div>

                        <p v-if="ollamaCheckStatus === 'checking'" class="flex items-center gap-2 text-sm text-base-content/70" role="status">
                          <span class="loading loading-spinner loading-xs" aria-hidden="true"></span> Looking for Ollama…
                        </p>
                        <AnswerModelSelect
                          v-else-if="ollamaCheckStatus === 'available'"
                          select-id="ollama-model"
                          :models="modelsFor('ollama')"
                          :model-value="editBuffer.model"
                          :allow-default="false"
                          :allow-custom="false"
                          @select="onModelSelect('ollama', $event)"
                          @refresh="detectOllama"
                        />
                        <p v-else-if="ollamaCheckStatus === 'unavailable'" class="notice notice-warning text-sm" role="alert">
                          <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
                          <span>Ollama did not answer at <code class="font-mono">{{ editBuffer.baseUrl || 'http://localhost:11434' }}</code>. <a href="https://ollama.com" target="_blank" rel="noopener" class="link">Install Ollama</a><template v-if="ollamaError"> · {{ ollamaError }}</template></span>
                        </p>

                        <div class="flex items-center gap-2 flex-wrap">
                          <button class="btn btn-sm btn-primary" :disabled="savingProvider === 'ollama' || ollamaCheckStatus === 'checking'" @click="connectOllama">
                            <span v-if="savingProvider === 'ollama'" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                            Connect
                          </button>
                          <button v-if="isProviderConfigured('ollama') && activeProviderId !== 'ollama'" class="btn btn-sm btn-ghost" @click="setDefaultProvider('ollama')">Use as default</button>
                          <button v-if="isProviderConfigured('ollama')" class="btn btn-sm btn-ghost text-error ml-auto" @click="removeProvider('ollama')">Remove</button>
                        </div>
                      </template>

                      <!-- A company's service: key + model -->
                      <template v-else-if="row.kind === 'builtin'">
                        <p v-if="isServerProvider(row.id)" class="text-xs text-base-content/60">
                          This server already has a team key for {{ row.name }}. Add your own only to use a different one.
                        </p>
                        <div class="form-control">
                          <label class="label p-0 pb-1" :for="`provider-apikey-${row.id}`">
                            <span class="label-text text-sm font-medium">API key</span>
                            <a v-if="row.def.keyLink" :href="row.def.keyLink" target="_blank" rel="noopener" class="label-text-alt link text-xs">Get a key</a>
                          </label>
                          <div class="join w-full">
                            <input
                              :id="`provider-apikey-${row.id}`"
                              v-model="editBuffer.apiKey"
                              :type="showEditKey ? 'text' : 'password'"
                              :placeholder="row.def.keyPlaceholder || 'API key…'"
                              autocomplete="off"
                              class="input input-bordered input-sm join-item flex-1 min-w-0"
                              @input="editBuffer.keyDirty = true; editBuffer.keyStatus = ''; editBuffer.keyError = ''"
                            />
                            <button class="btn btn-sm join-item" :aria-label="showEditKey ? 'Hide API key' : 'Show API key'" @click="showEditKey = !showEditKey">{{ showEditKey ? 'Hide' : 'Show' }}</button>
                          </div>
                        </div>
                        <AnswerModelSelect
                          :select-id="`provider-model-${row.id}`"
                          :models="modelsFor(row.id)"
                          v-model="editBuffer.model"
                          :custom-mode="!!editBuffer.customModelMode"
                          :loading="!!modelState[row.id]?.loading"
                          :error="modelState[row.id]?.error || ''"
                          @select="onModelSelect(row.id, $event)"
                          @commit="saveModelIfConfigured(row.id)"
                          @refresh="loadModels(row.id, { force: true })"
                        />
                        <div class="flex items-center gap-2 flex-wrap">
                          <button class="btn btn-sm btn-primary" :disabled="connectingProvider === row.id || !editBuffer.apiKey?.trim()" @click="connectProvider(row.id)">
                            <span v-if="connectingProvider === row.id" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                            {{ connectingProvider === row.id ? 'Checking…' : 'Connect' }}
                          </button>
                          <button v-if="isProviderConfigured(row.id) && activeProviderId !== row.id" class="btn btn-sm btn-ghost" @click="setDefaultProvider(row.id)">Use as default</button>
                          <span v-if="editBuffer.keyStatus === 'valid'" class="inline-flex items-center gap-1 text-sm" role="status"><Check :size="14" class="text-success" aria-hidden="true" />Connected</span>
                          <span v-if="editBuffer.keyError" class="text-error text-sm" role="alert">{{ editBuffer.keyError }}</span>
                          <button v-if="isProviderConfigured(row.id) && !editBuffer.keyDirty" class="btn btn-sm btn-ghost text-error ml-auto" @click="removeProvider(row.id)">Remove</button>
                        </div>
                        <!-- P5: test the choice, not only the key. -->
                        <div v-if="isProviderConfigured(row.id)" class="space-y-1" data-testid="model-job-check">
                          <button class="btn btn-xs btn-ghost" :disabled="jobChecks[row.id]?.running" @click="checkModelJobs(row.id)">
                            <span v-if="jobChecks[row.id]?.running" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                            Check what the model can do
                          </button>
                          <ul v-if="jobChecks[row.id]?.out?.jobs" class="text-xs space-y-0.5">
                            <li v-for="(r, job) in jobChecks[row.id].out.jobs" :key="job" class="flex gap-1.5">
                              <Check v-if="r.ok" :size="13" class="text-success shrink-0 mt-px" aria-hidden="true" />
                              <X v-else :size="13" class="text-warning shrink-0 mt-px" aria-hidden="true" />
                              <span><span class="font-medium">{{ JOB_NAMES[job] || job }}</span>: {{ r.detail }}</span>
                            </li>
                          </ul>
                          <p v-if="jobChecks[row.id]?.out?.error" class="text-xs text-error" role="alert">{{ jobChecks[row.id].out.error }}</p>
                        </div>
                      </template>

                      <!-- Your own OpenAI-compatible endpoint -->
                      <template v-else>
                        <p class="text-xs text-base-content/60 break-all">Endpoint <code class="font-mono">{{ row.cp.baseUrl }}</code></p>
                        <AnswerModelSelect
                          :select-id="`custom-model-${row.id}`"
                          :models="modelsFor(row.id)"
                          v-model="editBuffer.model"
                          :custom-mode="!!editBuffer.customModelMode"
                          :loading="!!modelState[row.id]?.loading"
                          :error="modelState[row.id]?.error || ''"
                          default-label="Use the endpoint’s default"
                          @select="onModelSelect(row.id, $event)"
                          @commit="saveModelIfConfigured(row.id)"
                          @refresh="loadModels(row.id, { force: true })"
                        />
                        <div class="flex items-center gap-2 flex-wrap">
                          <button v-if="activeProviderId !== row.id" class="btn btn-sm btn-ghost" @click="setDefaultProvider(row.id)">Use as default</button>
                          <button class="btn btn-sm btn-ghost text-error ml-auto" @click="removeProvider(row.id)">Remove</button>
                        </div>
                      </template>
                    </div>
                  </template>
                </li>
              </ul>
            </section>
          </div>

          <!-- Add your own endpoint -->
          <div v-if="showAddEndpoint" class="rounded-box border border-base-300 bg-base-100 px-4 py-4 space-y-3 mt-4">
            <p class="text-sm font-medium">Your own endpoint</p>
            <p class="text-xs text-base-content/60 max-w-[60ch] leading-relaxed">Any server that speaks the OpenAI API. One on your network joins that group; a hosted one is listed with the hosted providers.</p>
            <div class="form-control">
              <label class="label p-0 pb-1" for="endpoint-preset">
                <span class="label-text font-medium">Preset</span>
                <a v-if="selectedPreset?.keyLink" :href="selectedPreset.keyLink" target="_blank" rel="noopener" class="label-text-alt link text-xs">Get a key</a>
              </label>
              <select id="endpoint-preset" v-model="newEndpoint.presetId" class="select select-bordered select-sm w-full max-w-xs" @change="onPresetChange">
                <option v-for="p in availableEndpointPresets" :key="p.id" :value="p.id">{{ p.name }}</option>
              </select>
            </div>
            <div class="grid gap-3 sm:grid-cols-2">
              <div class="form-control">
                <label class="label p-0 pb-1" for="endpoint-name"><span class="label-text font-medium">Display name</span></label>
                <input id="endpoint-name" v-model="newEndpoint.name" type="text" placeholder="My vLLM Server" class="input input-bordered input-sm w-full" />
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1" for="endpoint-apikey">
                  <span class="label-text font-medium">API key <span v-if="!selectedPreset?.needsKey" class="font-normal text-base-content/60">(optional)</span></span>
                </label>
                <input id="endpoint-apikey" v-model="newEndpoint.apiKey" type="password" placeholder="none or your key" class="input input-bordered input-sm w-full" />
              </div>
            </div>
            <div class="form-control">
              <label class="label p-0 pb-1" for="endpoint-baseurl"><span class="label-text font-medium">Base URL</span></label>
              <input id="endpoint-baseurl" v-model="newEndpoint.baseUrl" type="url" placeholder="http://localhost:8000/v1" class="input input-bordered input-sm w-full" />
              <p class="text-xs text-base-content/60 mt-1">The part of the address before <code>/chat/completions</code>.</p>
            </div>
            <p v-if="addEndpointError" class="text-error text-sm" role="alert">{{ addEndpointError }}</p>
            <div class="flex gap-2">
              <button
                class="btn btn-sm btn-primary"
                @click="testAndAddEndpoint"
                :disabled="addingEndpoint || !newEndpoint.baseUrl?.trim() || !newEndpoint.name?.trim()"
              >
                <span v-if="addingEndpoint" class="loading loading-spinner loading-xs"></span>
                {{ addingEndpoint ? 'Testing…' : 'Test and add' }}
              </button>
              <button class="btn btn-sm btn-ghost" @click="showAddEndpoint = false">Cancel</button>
            </div>
          </div>
          <button v-else class="btn btn-ghost btn-sm gap-1.5 mt-4" @click="showAddEndpoint = true">
            <Plus :size="14" aria-hidden="true" /> Connect your own endpoint
          </button>

          <!-- Ollama context window (advanced) -->
          <div v-if="advanced" class="mt-8 pt-6 border-t border-base-300/60">
            <p class="text-xs uppercase tracking-[0.14em] font-semibold text-base-content/50 mb-4">Advanced</p>
            <div class="flex items-start justify-between gap-8">
              <div class="min-w-0">
                <div class="text-sm font-medium">Ollama context window</div>
                <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                  Tokens of context sent to local Ollama models. Ollama's default is small (~2048)
                  and silently truncates longer prompts — raise this so the model sees your full
                  documents. Higher uses more memory. Applies to chat, search, and OCR cleanup.
                </p>
              </div>
              <input
                id="ollama-num-ctx"
                v-model.number="ollamaNumCtx"
                type="number"
                min="512"
                step="512"
                placeholder="8192"
                class="input input-bordered input-sm w-28 flex-shrink-0 tabular-nums"
                @change="saveOllamaNumCtx"
                aria-label="Ollama context window (tokens)"
              />
            </div>
          </div>
        </section>

        <!-- ═══ Indexing ═══ -->
        <section v-show="activeSection === 'indexing'" aria-labelledby="settings-indexing">
          <h2 id="settings-indexing" class="sr-only">Indexing</h2>

          <p class="text-[13px] text-base-content/55 leading-relaxed max-w-[60ch] mb-5">
            How documents in <span class="font-medium text-base-content/75">{{ collectionStore.currentCollection?.name || 'Default' }}</span>
            are processed and indexed.
          </p>

          <!-- These settings apply to the whole deployment; the backend
               rejects non-admin writes under private collections, so the
               controls disable here instead of 403-ing on save. -->
          <div v-if="serverConfigLocked" class="alert py-2 mb-5 text-sm" role="note">
            <ShieldAlert :size="16" aria-hidden="true" />
            <span>Deployment settings are managed by an admin. You can view them, but changes are admin-only.</span>
          </div>
          <fieldset :disabled="serverConfigLocked" class="contents">

          <!-- OCR toggle -->
          <div class="flex items-start justify-between gap-8 pb-6">
            <div class="min-w-0">
              <div class="text-sm font-medium">OCR scanned PDFs</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Pages with little or no native text are read automatically. Applies to new uploads
                and re-indexing.<span v-if="!advanced"> Engine selection is under Advanced.</span>
              </p>
              <p v-if="ocrSettingsSaved" class="text-success text-xs mt-1.5" role="status">Saved — applies to new uploads</p>
            </div>
            <input
              type="checkbox"
              class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
              v-model="ocrEnabled"
              @change="saveOCRSettings"
              aria-label="OCR scanned PDFs"
            />
          </div>

          <!-- Re-index (owner only: it rebuilds the owner's index) -->
          <div
            v-if="!collectionStore.canConfigureCurrent"
            class="flex items-start justify-between gap-8 py-6 border-t border-base-300/50"
          >
            <div class="min-w-0">
              <div class="text-sm font-medium">How this collection is built</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]" data-testid="configure-blocked-reason">
                <template v-if="collectionStore.currentCollection?.team">
                  {{ collectionStore.currentConfigureBlockedReason }} Re-indexing and its indexing settings are theirs;
                  make your own copy if you need different ones.
                </template>
                <template v-else>
                  {{ collectionStore.currentCollection?.name || 'This collection' }} belongs to someone else,
                  so its indexing settings are theirs to change. Make your own copy if you need different ones.
                </template>
              </p>
            </div>
            <button class="btn btn-sm btn-outline flex-shrink-0" @click="$emit('clone-collection')">
              Make a copy
            </button>
          </div>

          <div v-else class="flex items-start justify-between gap-8 py-6 border-t border-base-300/50">
            <div class="min-w-0">
              <div class="text-sm font-medium">Re-index this collection</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Rebuilds the search index from the original files, applying any changed settings.
                Runs in the background; progress is shown in the status bar.
              </p>
              <p v-if="reindexSuccess" class="text-success text-xs mt-1.5" role="status">Re-indexing started</p>
              <p v-if="reindexCompleted" class="text-success text-xs mt-1.5" role="status">Re-index complete — sources refreshed</p>
              <p v-if="reindexError" class="text-error text-xs mt-1.5" role="alert">{{ reindexError }}</p>
            </div>
            <button
              class="btn btn-sm btn-outline flex-shrink-0"
              @click="startReindex"
              :disabled="reindexing || reindexInProgress"
            >
              <span v-if="reindexing || reindexInProgress" class="loading loading-spinner loading-xs"></span>
              {{ reindexing ? 'Starting…' : (reindexInProgress ? 'Re-indexing…' : 'Re-index') }}
            </button>
          </div>

          <!-- Search model (the embedding model) -->
          <div class="py-6 border-t border-base-300/50" data-testid="search-index-settings">
            <div class="text-sm font-medium">Search model</div>
            <p class="text-xs text-base-content/60 mt-1 leading-relaxed max-w-[60ch]">
              The embedding model turns every source into vectors so search finds passages by meaning.
              It applies to the whole server, and changing it means re-indexing.
            </p>
            <p v-if="embeddingInUse" class="text-xs text-base-content/70 mt-2" data-testid="embedding-in-use">
              In use: <span class="font-medium text-base-content">{{ embeddingInUse }}</span>
            </p>

            <div v-if="embeddingCatalogError" class="notice notice-warning text-xs mt-3" role="alert">
              <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
              <span>{{ embeddingCatalogError }}</span>
            </div>

            <div class="mt-5 space-y-5">
              <fieldset v-for="group in embeddingGroups" :key="group.id" :data-testid="`embedding-group-${group.id}`">
                <legend class="text-sm font-semibold">{{ group.title }}</legend>
                <p class="text-xs text-base-content/60 mt-0.5 mb-2 max-w-[60ch]">{{ group.note }}</p>
                <div class="rounded-box border border-base-300 bg-base-100 divide-y divide-base-300 overflow-hidden" role="radiogroup" :aria-label="group.title">
                  <label
                    v-for="p in group.providers"
                    :key="p.id"
                    class="flex items-start gap-3 px-3 py-2.5 transition-colors"
                    :class="[
                      embeddingProvider === p.id ? 'bg-base-200/70' : 'hover:bg-base-200/40',
                      p.blocked_offline ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer',
                    ]"
                  >
                    <input
                      type="radio"
                      class="radio radio-primary radio-sm mt-0.5 flex-shrink-0"
                      :value="p.id"
                      v-model="embeddingProvider"
                      :disabled="p.blocked_offline"
                      @change="onEmbeddingProviderChange"
                    />
                    <span class="min-w-0 flex-1">
                      <span class="flex items-baseline gap-x-2 gap-y-0.5 flex-wrap">
                        <span class="text-sm font-medium">{{ p.label }}</span>
                        <span class="text-xs text-base-content/60">{{ p.cost_label }}</span>
                        <span v-if="p.id === loadedEmbeddingProvider" class="text-xs font-medium">In use</span>
                        <span v-if="p.blocked_offline" class="text-xs text-base-content/60">Not allowed offline</span>
                        <span v-else-if="p.needs_key && p.key_configured" class="text-xs text-base-content/60">Key on file</span>
                        <span v-else-if="p.needs_key" class="text-xs text-base-content/60">Needs a key</span>
                      </span>
                      <span v-if="embeddingProvider === p.id" class="block text-xs text-base-content/70 mt-1 leading-relaxed">{{ p.blurb }}</span>
                    </span>
                  </label>
                </div>
              </fieldset>
            </div>

            <!-- Details for the chosen provider -->
            <div v-if="selectedEmbeddingProvider" class="mt-5 space-y-4 max-w-lg">

              <!-- Ollama: address + detect -->
              <div v-if="embeddingProvider === 'ollama'" class="form-control">
                <label class="label p-0 pb-1" for="embedding-ollama-url"><span class="label-text text-sm font-medium">Ollama address</span></label>
                <div class="flex gap-2 items-start">
                  <input id="embedding-ollama-url" v-model="ollamaBaseUrl" type="url" placeholder="http://localhost:11434" class="input input-bordered input-sm w-full" @change="embeddingTestResult = null" />
                  <button class="btn btn-sm btn-ghost flex-shrink-0" :disabled="embeddingOllamaStatus === 'checking'" @click="detectEmbeddingOllama">
                    <span v-if="embeddingOllamaStatus === 'checking'" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                    <span v-else>Detect</span>
                  </button>
                </div>
                <p v-if="embeddingOllamaStatus === 'error'" class="text-xs text-base-content/70 mt-1" role="alert">
                  Ollama did not answer at <code>{{ ollamaBaseUrl }}</code>. Check that it is running.
                  <span v-if="ollamaBaseUrl.includes('localhost') || ollamaBaseUrl.includes('127.0.0.1')">
                    In Docker, use <code>http://host.docker.internal:11434</code> instead.
                  </span>
                </p>
                <p v-else-if="embeddingOllamaStatus === 'ok' && !embeddingOllamaModels.length" class="text-xs text-base-content/70 mt-1" role="status">
                  Ollama is running but has no embedding model.<template v-if="embeddingOllamaSuggested"> Run <code>ollama pull {{ embeddingOllamaSuggested }}</code> there first.</template>
                </p>
              </div>

              <!-- Custom endpoint: address, then ask it what it serves -->
              <div v-if="embeddingProvider === 'openai_compatible'" class="form-control">
                <label class="label p-0 pb-1" for="embedding-base-url"><span class="label-text text-sm font-medium">Endpoint address</span></label>
                <div class="flex gap-2 items-start">
                  <input id="embedding-base-url" v-model="embeddingBaseUrl" type="url" placeholder="https://api.example.com/v1" class="input input-bordered input-sm w-full" @change="embeddingTestResult = null; customEmbeddingList = null" />
                  <button class="btn btn-sm btn-ghost flex-shrink-0" :disabled="!embeddingBaseUrl.trim() || customEmbeddingListing" @click="listCustomEmbeddingModels">
                    <span v-if="customEmbeddingListing" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                    <span v-else>List models</span>
                  </button>
                </div>
                <p class="text-xs text-base-content/60 mt-1">
                  The part of the address before <code>/embeddings</code>.
                  <template v-if="customEmbeddingList?.message"> {{ customEmbeddingList.message }}</template>
                </p>
              </div>

              <!-- Model -->
              <div class="form-control">
                <label class="label p-0 pb-1" for="embedding-model"><span class="label-text text-sm font-medium">Model</span></label>
                <select
                  v-if="embeddingModelChoices.length"
                  id="embedding-model"
                  v-model="embeddingModelChoice"
                  class="select select-bordered select-sm w-full"
                  @change="embeddingTestResult = null"
                >
                  <option v-for="m in embeddingModelChoices" :key="m.id" :value="m.id">{{ m.label }}{{ m.recommended ? ' · recommended' : '' }}</option>
                  <option value="__other__">Another model…</option>
                </select>
                <input
                  v-if="embeddingModelChoice === '__other__' || !embeddingModelChoices.length"
                  :id="embeddingModelChoices.length ? 'embedding-model-custom' : 'embedding-model'"
                  v-model="embeddingCustomModel"
                  type="text"
                  :placeholder="embeddingProvider === 'local' ? 'a sentence-transformers model name' : 'model name as the provider spells it'"
                  class="input input-bordered input-sm w-full mt-2"
                  :aria-label="embeddingModelChoices.length ? 'Custom model name' : 'Model'"
                  @input="embeddingTestResult = null"
                />
                <p v-if="embeddingFacts.length" class="text-xs text-base-content/70 mt-1.5" data-testid="embedding-model-facts">{{ embeddingFacts.join(' · ') }}</p>
                <p v-else-if="embeddingProvider === 'local'" class="text-xs text-base-content/60 mt-1.5">
                  Any <a href="https://www.sbert.net/docs/pretrained_models.html" target="_blank" rel="noopener" class="link">sentence-transformers</a> model; it downloads on first use.
                </p>
                <p v-else-if="embeddingProvider === 'ollama'" class="text-xs text-base-content/60 mt-1.5">Must already be pulled on that Ollama server.</p>
              </div>

              <!-- Passages longer than the model reads -->
              <div v-if="embeddingChunkFit && !embeddingChunkFit.fits" class="notice notice-warning text-xs" role="alert" data-testid="embedding-chunk-warning">
                <AlertTriangle :size="14" class="text-warning" aria-hidden="true" />
                <span>
                  Passages in {{ collectionStore.currentCollection?.name || 'this collection' }} are about
                  {{ embeddingChunkFit.passageTokens.toLocaleString() }} tokens ({{ Number(chunkSize).toLocaleString() }} characters),
                  but this model reads only the first {{ embeddingChunkFit.maxInputTokens.toLocaleString() }} of each, so the rest is
                  not searched by meaning. Choose a model that reads more, or lower the chunk size<template v-if="advanced"> under Chunking below</template>.
                </span>
              </div>

              <!-- What a switch costs, from real counts -->
              <p v-if="embeddingSwitchPending && embeddingSwitchSentence" class="notice text-xs leading-relaxed" role="status" data-testid="embedding-switch-cost">
                <RefreshCw :size="14" class="text-base-content/60" aria-hidden="true" />
                <span>{{ embeddingSwitchSentence }}</span>
              </p>

              <!-- API key -->
              <div v-if="selectedEmbeddingProvider.needs_key || embeddingProvider === 'openai_compatible'" class="form-control">
                <label class="label p-0 pb-1" for="embedding-api-key">
                  <span class="label-text text-sm font-medium">
                    API key
                    <span v-if="!selectedEmbeddingProvider.needs_key || embeddingKeyOnFile" class="font-normal text-base-content/60">(optional)</span>
                  </span>
                </label>
                <input
                  id="embedding-api-key"
                  v-model="embeddingApiKey"
                  type="password"
                  :placeholder="embeddingKeyPlaceholder"
                  class="input input-bordered input-sm w-full"
                  autocomplete="off"
                  @input="embeddingTestResult = null"
                />
                <p class="text-xs text-base-content/60 mt-1">
                  <template v-if="embeddingApiKeyClear">
                    The saved key will be removed when you save.
                    <button class="link" @click="embeddingApiKeyClear = false">Keep it</button>
                  </template>
                  <template v-else-if="selectedEmbeddingProvider.key_source === 'provider_card'">
                    Using the {{ selectedEmbeddingProvider.label }} key saved under
                    <button class="link" @click="activeSection = 'providers'">AI Providers</button>.
                    Enter a key here only to use a different one for search.
                  </template>
                  <template v-else-if="selectedEmbeddingProvider.key_source === 'embedding'">
                    A key is saved for search. Leave blank to keep it, or
                    <button class="link" @click="embeddingApiKeyClear = true">remove it</button>.
                  </template>
                  <template v-else-if="selectedEmbeddingProvider.key_link">
                    Get a key at
                    <a :href="selectedEmbeddingProvider.key_link" target="_blank" rel="noopener" class="link">{{ embeddingKeyHost(selectedEmbeddingProvider.key_link) }}</a>.
                    <template v-if="selectedEmbeddingProvider.pricing_link">
                      Limits and prices: <a :href="selectedEmbeddingProvider.pricing_link" target="_blank" rel="noopener" class="link">pricing page</a>.
                    </template>
                  </template>
                  <template v-else>
                    Leave blank if the endpoint does not need one.
                  </template>
                </p>
              </div>

              <!-- Test + Save -->
              <div class="flex items-center gap-3 flex-wrap">
                <button class="btn btn-sm btn-outline" :disabled="embeddingTesting || embeddingSaving" @click="testEmbeddingSettings">
                  <span v-if="embeddingTesting" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                  {{ embeddingTesting ? 'Embedding a test passage…' : 'Test' }}
                </button>
                <button class="btn btn-sm btn-primary" :disabled="embeddingSaving || embeddingTesting" @click="saveEmbeddingSettings">
                  <span v-if="embeddingSaving" class="loading loading-spinner loading-xs" aria-hidden="true"></span>
                  {{ embeddingSaving ? 'Saving…' : (embeddingSwitchPending ? 'Switch model' : 'Save') }}
                </button>
                <span v-if="embeddingSettingsSaved && !embeddingNeedsReindex" class="inline-flex items-center gap-1 text-sm" role="status"><Check :size="14" class="text-success" aria-hidden="true" />Saved</span>
              </div>
              <p v-if="embeddingTestResult && embeddingTestResult.ok" class="flex items-start gap-1.5 text-xs" role="status" data-testid="embedding-test-ok">
                <Check :size="14" class="text-success flex-shrink-0" aria-hidden="true" />
                <span>Embedded a test passage with {{ embeddingTestResult.model }}: {{ Number(embeddingTestResult.dimensions).toLocaleString() }} dimensions, {{ embeddingTestResult.seconds }} s.</span>
              </p>
              <p v-else-if="embeddingTestResult" class="text-xs text-error" role="alert">{{ embeddingTestResult.error }}</p>
              <p v-if="embeddingSaveError" class="text-xs text-error" role="alert">{{ embeddingSaveError }}</p>
              <p v-if="embeddingSettingsSaved && embeddingNeedsReindex" class="notice text-xs" role="status">
                <Check :size="14" class="text-success" aria-hidden="true" />
                <span>Saved. Each collection keeps searching with its current index until you re-index it with Re-index, above.</span>
              </p>
            </div>
          </div>

          <!-- Advanced indexing -->
          <div v-if="advanced" class="pt-6 border-t border-base-300/60 space-y-8">
            <p class="text-xs uppercase tracking-[0.14em] font-semibold text-base-content/50">Advanced</p>

            <!-- OCR engine -->
            <div>
              <div class="text-sm font-medium">OCR engine</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[56ch]">
                Used when OCR is on. Vision AI reads each page with a multimodal model — most
                accurate for complex layouts and degraded scans. Providers come from
                <button class="link link-primary" @click="activeSection = 'providers'">AI Providers</button>;
                keys and a vision-capable model are reused automatically.
              </p>

              <div class="grid gap-4 grid-cols-1 sm:grid-cols-2 mt-3">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="vision-provider">
                    <span class="label-text text-xs font-medium">Engine</span>
                  </label>
                  <select id="vision-provider" v-model="visionProvider" class="select select-bordered select-sm w-full" @change="onOcrProviderChange">
                    <option value="none">Local — Docling / Tesseract (free, offline)</option>
                    <option v-for="opt in ocrConfiguredProviders" :key="opt.value" :value="opt.value">
                      Vision AI — {{ opt.label }}
                    </option>
                  </select>
                </div>

                <!-- Ollama (local): detect vision-capable models -->
                <div class="form-control" v-if="visionProvider === 'ollama'">
                  <label class="label p-0 pb-1" for="vision-model-ollama">
                    <span class="label-text text-xs font-medium">Vision model</span>
                    <button
                      class="label-text-alt btn btn-xs btn-ghost"
                      @click="refreshOcrOllamaVisionModels"
                      :disabled="ocrOllamaVisionLoading"
                      aria-label="Refresh Ollama vision models"
                    >
                      {{ ocrOllamaVisionLoading ? '…' : 'Refresh' }}
                    </button>
                  </label>
                  <select id="vision-model-ollama" v-if="ocrOllamaVisionModels.length" v-model="visionModel" class="select select-bordered select-sm w-full" @change="saveOCRSettings">
                    <option v-for="m in ocrOllamaVisionModels" :key="m.name" :value="m.name">{{ m.name }}</option>
                  </select>
                  <div v-else-if="ocrOllamaVisionLoading" class="text-xs text-base-content/60 py-2">Checking models…</div>
                  <div v-else class="alert alert-warning py-2 text-xs">
                    <span v-if="ocrOllamaVisionTotal > 0">
                      {{ ocrOllamaVisionTotal }} model(s) installed but none support vision.<template v-if="ocrOllamaVisionSuggested"> Try: <code>ollama pull {{ ocrOllamaVisionSuggested }}</code></template>
                    </span>
                    <span v-else>Ollama not running or no models installed.<template v-if="ocrOllamaVisionSuggested"> <code>ollama pull {{ ocrOllamaVisionSuggested }}</code></template></span>
                  </div>
                </div>
              </div>
            </div>

            <!-- Chunking (owner only) -->
            <div v-if="collectionStore.canConfigureCurrent" class="pt-6 border-t border-base-300/50">
              <div class="text-sm font-medium">Chunking</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[56ch]">
                Per-collection chunking for search. Re-index to apply changes.
              </p>

              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
                <div class="form-control">
                  <label class="label py-1">
                    <span class="label-text text-xs font-medium">Chunk size</span>
                    <span class="label-text-alt text-xs text-base-content/50">characters</span>
                  </label>
                  <input
                    type="number"
                    min="100"
                    max="4000"
                    step="50"
                    class="input input-sm input-bordered tabular-nums"
                    v-model.number="chunkSize"
                    @change="saveChunkSettings"
                    aria-label="Chunk size (characters)"
                  />
                  <p class="text-xs text-base-content/60 mt-1">
                    Smaller = more precise matches. Larger = more context per chunk.
                  </p>
                  <p v-if="savedModelChunkFit && !savedModelChunkFit.fits" class="text-xs text-base-content/80 mt-1 flex items-start gap-1.5" role="alert" data-testid="chunk-too-long">
                    <AlertTriangle :size="13" class="text-warning flex-shrink-0 mt-0.5" aria-hidden="true" />
                    <span>About {{ savedModelChunkFit.passageTokens.toLocaleString() }} tokens: longer than the {{ savedModelChunkFit.maxInputTokens.toLocaleString() }} the search model reads, so the end of each passage is not searched by meaning.</span>
                  </p>
                </div>
                <div class="form-control">
                  <label class="label py-1">
                    <span class="label-text text-xs font-medium">Chunk overlap</span>
                    <span class="label-text-alt text-xs text-base-content/50">characters</span>
                  </label>
                  <input
                    type="number"
                    min="0"
                    max="1000"
                    step="25"
                    class="input input-sm input-bordered tabular-nums"
                    v-model.number="chunkOverlap"
                    @change="saveChunkSettings"
                    aria-label="Chunk overlap (characters)"
                  />
                  <p class="text-xs text-base-content/50 mt-1">
                    Overlap between adjacent chunks to preserve context across boundaries.
                  </p>
                </div>
              </div>

              <p v-if="chunkSettingsSaved" class="text-success text-xs mt-2" role="status">Saved — re-index to apply</p>
              <p v-if="chunkSettingsError" class="text-error text-xs mt-2" role="alert">{{ chunkSettingsError }}</p>
            </div>

            <!-- Table column inference -->
            <div class="pt-6 border-t border-base-300/50">
              <div class="flex items-start justify-between gap-8">
                <div class="min-w-0">
                  <div class="text-sm font-medium">AI column detection</div>
                  <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                    When importing CSV or Excel files, column names and sample values are sent to
                    your AI provider to identify roles that heuristics can't determine.
                  </p>
                  <p v-if="inferenceSettingsSaved" class="text-success text-xs mt-1.5" role="status">Saved</p>
                </div>
                <input
                  type="checkbox"
                  class="toggle toggle-primary toggle-sm flex-shrink-0 mt-0.5"
                  v-model="llmSchemaInferenceEnabled"
                  @change="saveInferenceSettings"
                  aria-label="Enable AI column detection"
                />
              </div>
            </div>
          </div>
          </fieldset>

          <!-- Retrieval coverage: readable by anyone with access, run/edited by owners -->
          <div v-if="collectionStore.currentCollection?.id" class="pt-6 mt-6 border-t border-base-300/50">
            <CoverageReportPanel
              :collection-id="collectionStore.currentCollection.id"
              :can-run="collectionStore.canConfigureCurrent"
              :who-can-run="collectionStore.currentCollection?.team ? 'An admin' : undefined"
            />
          </div>
        </section>

        <!-- ═══ Features ═══ -->
        <section v-show="activeSection === 'features'" aria-labelledby="settings-features">
          <h2 id="settings-features" class="sr-only">Features</h2>
          <FeatureSettings
            :locked="serverConfigLocked"
            :active="activeSection === 'features'"
            :offline="systemInfo.offline_mode"
            @open-section="activeSection = $event"
            @switch-tab="$emit('switch-tab', $event)"
            @changed="onFeatureChanged"
          />
        </section>

        <!-- ═══ Custom file types ═══ -->
        <section v-show="activeSection === 'custom-file-types'" aria-labelledby="settings-custom-file-types">
          <CustomFileTypes />
        </section>

        <!-- ═══ Data ═══ -->
        <section v-show="activeSection === 'data'" aria-labelledby="settings-data">
          <h2 id="settings-data" class="sr-only">Data</h2>

          <div class="flex items-start justify-between gap-8">
            <div class="min-w-0">
              <div class="text-sm font-medium text-error">Clear collection data</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Permanently deletes every source and index in
                <span class="font-medium text-base-content/75">{{ collectionStore.currentCollection?.name || 'Default' }}</span>.
                The collection itself remains. This cannot be undone. Library reviews and saved reports are kept: adding the same file back brings its reviews back. To remove them too, use the option below.
              </p>
              <div v-if="clearJob" class="mt-2 max-w-[52ch]" role="status" data-testid="clear-progress">
                <progress
                  class="progress progress-error w-full h-1.5 block"
                  :value="clearJob.queuePosition ? undefined : clearJob.progress"
                  max="100"
                ></progress>
                <p class="text-xs text-base-content/60 mt-1">
                  {{ clearJob.queuePosition ? 'Waiting for another job on this collection to finish.' : (clearJob.phaseDetail || 'Starting…') }}
                  You can keep working; this carries on in the background.
                </p>
              </div>
              <p v-if="clearSuccess" class="text-success text-xs mt-1.5" role="status">All data cleared</p>
              <p v-if="clearError" class="text-error text-xs mt-1.5" role="alert">{{ clearError }}</p>
            </div>
            <button
              class="btn btn-sm btn-outline btn-error flex-shrink-0"
              @click="confirmClearAll"
              :disabled="clearing || !collectionStore.canEditCurrent"
              :title="collectionStore.canEditCurrent ? undefined : 'This collection is shared with you read-only'"
            >
              <span v-if="clearing" class="loading loading-spinner loading-xs"></span>
              {{ clearing ? 'Clearing…' : 'Clear data' }}
            </button>
          </div>

          <div class="flex items-start justify-between gap-8 mt-6" data-testid="outputs-row">
            <div class="min-w-0">
              <div class="text-sm font-medium text-error">Delete generated reviews and reports</div>
              <p class="text-xs text-base-content/55 mt-1 leading-relaxed max-w-[52ch]">
                Removes what was generated from this collection, including reviews of sources since deleted, which no list shows. Sources stay. This cannot be undone.
              </p>
              <p v-if="outputs" class="text-xs text-base-content/70 mt-1.5 tabular-nums" data-testid="outputs-counts">
                {{ outputsSummary }}
              </p>
              <p v-if="outputs?.reports_held" class="text-xs text-base-content/55 mt-1">
                {{ outputs.reports_held.toLocaleString() }} held for review {{ outputs.reports_held === 1 ? 'stays' : 'stay' }}: only someone who can review {{ outputs.reports_held === 1 ? 'it' : 'them' }} can delete {{ outputs.reports_held === 1 ? 'it' : 'them' }}.
              </p>
              <p v-if="outputsDone" class="text-success text-xs mt-1.5" role="status">{{ outputsDone }}</p>
              <p v-if="outputsError" class="text-error text-xs mt-1.5" role="alert">{{ outputsError }}</p>
            </div>
            <button
              class="btn btn-sm btn-outline btn-error flex-shrink-0"
              data-testid="delete-outputs"
              @click="confirmDeleteOutputs"
              :disabled="!collectionStore.canEditCurrent || !hasOutputs"
              :title="collectionStore.canEditCurrent ? undefined : 'This collection is shared with you read-only'"
            >
              Delete…
            </button>
          </div>
        </section>

        <!-- ═══ System ═══ -->
        <section v-show="activeSection === 'system'" aria-labelledby="settings-system">
          <h2 id="settings-system" class="sr-only">System</h2>

          <p class="text-[13px] text-base-content/55 leading-relaxed mb-5">
            Server configuration — set via <code class="text-xs">.env</code> or the environment, read-only here.
          </p>

          <dl class="divide-y divide-base-300/50 text-sm">
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Deployment</dt>
              <dd class="font-medium flex items-center gap-1.5">
                <ShieldCheck v-if="systemInfo.offline_mode" :size="14" class="text-success" aria-hidden="true" />
                {{ systemInfo.offline_mode ? 'Air-gapped (offline mode)' : 'Standard' }}
              </dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Database</dt>
              <dd class="font-medium">{{ systemInfo.db_backend === 'postgresql' ? 'PostgreSQL' : 'SQLite' }}</dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">Private collections</dt>
              <dd class="font-medium">{{ systemInfo.private_collections ? 'On' : 'Off' }}</dd>
            </div>
            <div class="flex items-center justify-between gap-8 py-3">
              <dt class="text-base-content/55">User</dt>
              <dd class="font-medium">{{ systemInfo.user_id || 'default' }}</dd>
            </div>
          </dl>

          <p v-if="systemInfo.offline_mode" class="text-xs text-base-content/50 mt-4 leading-relaxed max-w-[56ch]">
            Cloud AI providers are disabled and models load from the local cache only.
            This deployment makes no outbound connections beyond the endpoints configured on your network.
          </p>
        </section>

        <!-- (The Access section moved to the Admin tab, next to usage.) -->

      </div>
    </div>

    <!-- Delete generated reviews and reports -->
    <dialog ref="outputsModal" class="modal" aria-labelledby="delete-outputs-title">
      <div class="modal-box">
        <h3 id="delete-outputs-title" class="font-bold text-lg text-error">Delete generated work</h3>
        <p class="py-3 text-sm">
          From <strong>{{ collectionStore.currentCollection?.name || 'Default' }}</strong>. Its sources stay. Generating these again costs model time.
        </p>
        <label class="flex items-center gap-2 py-1 text-sm cursor-pointer">
          <input v-model="deleteReviews" type="checkbox" class="checkbox checkbox-sm" data-testid="outputs-reviews" />
          {{ countOf(outputs?.reviews || 0, 'Library review', 'Library reviews') }} and {{ countOf(outputs?.screenings || 0, 'screening verdict', 'screening verdicts') }}
        </label>
        <label class="flex items-center gap-2 py-1 text-sm cursor-pointer">
          <input v-model="deleteReports" type="checkbox" class="checkbox checkbox-sm" data-testid="outputs-reports" />
          {{ countOf(outputs?.reports || 0, 'saved report', 'saved reports') }}, with any files
        </label>
        <div class="modal-action">
          <button class="btn" @click="outputsModal?.close()" :disabled="deletingOutputs">Cancel</button>
          <button
            class="btn btn-error"
            data-testid="outputs-confirm"
            @click="deleteOutputs"
            :disabled="deletingOutputs || !(deleteReviews || deleteReports)"
          >
            <span v-if="deletingOutputs" class="loading loading-spinner"></span>
            Delete permanently
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button :disabled="deletingOutputs">close</button>
      </form>
    </dialog>

    <!-- Clear All Confirmation Modal -->
    <dialog ref="clearModal" class="modal" aria-labelledby="clear-all-title">
      <div class="modal-box">
        <h3 id="clear-all-title" class="font-bold text-lg text-error">Clear All Data</h3>
        <p class="py-4">
          This will <strong>permanently delete</strong> all sources and indexes in <strong>{{ collectionStore.currentCollection?.name || 'Default' }}</strong>.
        </p>
        <p class="text-sm text-error font-semibold">
          This action cannot be undone.
        </p>
        <div class="modal-action">
          <button class="btn" @click="closeClearModal" :disabled="clearStarting">Cancel</button>
          <button class="btn btn-error" @click="clearAllData" :disabled="clearing">
            <span v-if="clearStarting" class="loading loading-spinner"></span>
            Delete Everything
          </button>
        </div>
      </div>
      <form method="dialog" class="modal-backdrop">
        <button @click="closeClearModal">close</button>
      </form>
    </dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import http from '../utils/http'
import { researchApi } from '../utils/researchApi.js'
import { ChevronDown, Check, Plus, RefreshCw, ShieldCheck, ShieldAlert, AlertTriangle, X } from 'lucide-vue-next'
import { useCollectionStore } from '../stores/collectionStore'
import { useBackgroundJobsStore } from '../stores/backgroundJobsStore'
import { useLayoutStore } from '../stores/layoutStore'
import { useUserStore } from '../stores/userStore'
import { useProviderStore } from '../stores/providerStore'
import CustomFileTypes from './CustomFileTypes.vue'
import FeatureSettings from './FeatureSettings.vue'
import CoverageReportPanel from './CoverageReportPanel.vue'
import AnswerModelSelect from './AnswerModelSelect.vue'
import { chunkFit, embeddingModelFacts, switchCostSentence } from '../utils/embeddingChoice.js'
import {
  PROVIDER_DEFS,
  CUSTOM_ENDPOINT_PRESETS,
  getProvidersConfig,
  getProviderConfig,
  upsertProviderConfig,
  removeProviderConfig,
  getConfiguredProviderIds,
  getProviderDisplayName,
  fetchProviderModels,
  invalidateModelCache,
  testProviderConnection,
  testModelJobs,
  fetchDeploymentDefault,
  fetchServerProviderIds,
  migrateLegacySettings,
  isRemoteDeployment,
  getActiveProvider,
  setActiveProviderLS,
  notifyProviderChange,
  isPrivateUrl,
  modelsForJob,
  providerLocation,
} from '../utils/aiProviders.js'

const emit = defineEmits(['data-cleared', 'stats-updated', 'switch-tab', 'chat-tab-toggled', 'clone-collection'])
const props = defineProps({
  // A section to open, asked for from outside the tab: { id, at }. `at` makes
  // asking for the same section twice count as two requests.
  sectionRequest: { type: Object, default: null },
})

const collectionStore = useCollectionStore()
const backgroundJobsStore = useBackgroundJobsStore()

// ── Settings shell: section rail + basic/advanced depth ──
const userStore = useUserStore()
const providerStore = useProviderStore()
const layoutStore = useLayoutStore()

const ALL_SECTIONS = [
  { id: 'general', label: 'General' },
  { id: 'layout', label: 'Layout' },
  { id: 'providers', label: 'AI Providers' },
  { id: 'indexing', label: 'Indexing' },
  { id: 'features', label: 'Features' },
  { id: 'custom-file-types', label: 'Custom file types' },
  { id: 'data', label: 'Data' },
  { id: 'system', label: 'System' },
]
const SECTIONS = computed(() => ALL_SECTIONS)

// "system" is the absence of a choice: no stored theme, light or dark
// following the OS. Without it the picker had no way back once someone
// clicked a swatch, and on first visit it showed Light as "chosen".
const THEMES = [
  { id: 'system', label: 'System' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
  { id: 'cupcake', label: 'Cupcake' },
  { id: 'nord', label: 'Nord' },
  { id: 'dracula', label: 'Dracula' },
]

// Under private collections, deployment-wide settings are ADMIN_EMAILS-only
// (the backend gates POST /api/config); mirror that in the UI.
const serverConfigLocked = computed(() => userStore.privateCollections && !userStore.isAdmin)

const settingsMode = ref(localStorage.getItem('settings_mode') || 'basic')
watch(settingsMode, v => localStorage.setItem('settings_mode', v))
const advanced = computed(() => settingsMode.value === 'advanced')

const savedSection = localStorage.getItem('settings_section')
const activeSection = ref(ALL_SECTIONS.some(s => s.id === savedSection) ? savedSection : 'general')
watch(activeSection, v => localStorage.setItem('settings_section', v))
// The admin sections resolve after /api/user/me returns; if the stored
// section is not available to this person, fall back rather than showing a
// blank column.
watch(SECTIONS, list => {
  if (!list.some(s => s.id === activeSection.value)) activeSection.value = 'general'
}, { immediate: true })

watch(() => props.sectionRequest, (request) => {
  if (request && ALL_SECTIONS.some(s => s.id === request.id)) activeSection.value = request.id
}, { immediate: true })

// UI feature flags
const chatTabEnabled = ref(true)

// OCR settings state — deliberately minimal: on/off + engine (+ auto-picked
// model/key when the engine is a vision provider). Rendering details are fixed
// server-side; page/size cost guards are env-only.
const ocrEnabled = ref(false)
const ocrSettingsSaved = ref(false)

const visionProvider = ref('none')
const visionModel = ref('')
// The key is inherited from the provider's saved config (AI Providers) and
// POSTed to the server so background indexing can use it — no separate input.
const visionApiKey = ref('')

const ocrOllamaVisionModels = ref([])
const ocrOllamaVisionLoading = ref(false)
const ocrOllamaVisionTotal = ref(0)
const ocrOllamaVisionSuggested = ref('')  // a vision model worth pulling, named by the server

// Local Ollama context window (server-side setting; applies to all Ollama calls)
const ollamaNumCtx = ref(8192)

// Embedding settings. The provider catalog (labels, costs, models, whether a
// key is on file) comes from GET /api/embedding/providers so the picker and
// the backend never disagree about what is offered.
const MASKED_SECRET = '********'
const embeddingCatalog = ref([])
const embeddingCatalogError = ref('')
const embeddingProvider = ref('local')
const loadedEmbeddingProvider = ref('local')
const embeddingModel = ref('')                     // saved local model
const remoteEmbeddingModel = ref('')                // saved model for any other provider
const embeddingModelChoice = ref('')                // select value; '__other__' = free text
const embeddingCustomModel = ref('')
const embeddingBaseUrl = ref('')
const embeddingApiKey = ref('')                     // only what the user types this session
const embeddingApiKeySet = ref(false)               // a key is stored server-side
const embeddingApiKeyClear = ref(false)
const ollamaBaseUrl = ref('http://localhost:11434')
const embeddingSettingsSaved = ref(false)
const embeddingNeedsReindex = ref(false)
const embeddingSaving = ref(false)
const embeddingSaveError = ref('')
const embeddingTesting = ref(false)
const embeddingTestResult = ref(null)               // { ok, model, dimensions, seconds } | { ok:false, error }
const embeddingOllamaModels = ref([])
const embeddingOllamaSuggested = ref('')  // an embedding model worth pulling, named by the server
const embeddingOllamaStatus = ref(null)             // null | 'checking' | 'ok' | 'error'

const embeddingProviderOptions = computed(() =>
  embeddingCatalog.value.filter((p) => {
    // "Ollama on your own server" defaults to localhost, which on a hosted
    // deployment is the server, not the reader's machine — hide it there
    // unless it is already the choice.
    if (p.id === 'ollama' && isRemoteDeployment() && embeddingProvider.value !== 'ollama') return false
    return true
  })
)

const selectedEmbeddingProvider = computed(() =>
  embeddingCatalog.value.find((p) => p.id === embeddingProvider.value) || null
)

// The two groups the provider list is split into, by where source text goes.
const EMBEDDING_GROUPS = [
  { id: 'network', title: 'On this server or your network', note: 'Source text stays on machines you run.' },
  { id: 'hosted', title: 'Hosted', note: 'The text of every source, and every search, is sent to the company that runs the model.' },
]
const embeddingGroups = computed(() =>
  EMBEDDING_GROUPS
    .map(g => ({ ...g, providers: embeddingProviderOptions.value.filter(p => (p.hosted ? 'hosted' : 'network') === g.id) }))
    .filter(g => g.providers.length)
)

// What an installed Ollama model holds: the catalogue's facts for the same
// model when it has them, and the context length Ollama itself reports as
// the most it reads per input.
const ollamaEmbeddingChoice = (m) => {
  const known = (selectedEmbeddingProvider.value?.models || []).find(c => c.id === m.name.replace(/:latest$/, ''))
  return {
    ...(known || {}),
    id: m.name,
    label: known ? `${m.name} — ${known.label.replace(/^[^—]*—\s*/, '')}` : m.name,
    max_input_tokens: m.record?.context_tokens || known?.max_input_tokens || null,
    dimensions: m.record?.embedding_dimensions || known?.dimensions || null,
  }
}

const embeddingModelChoices = computed(() => {
  if (embeddingProvider.value === 'ollama' && embeddingOllamaModels.value.length) {
    return embeddingOllamaModels.value.map(ollamaEmbeddingChoice)
  }
  if (embeddingProvider.value === 'openai_compatible' && customEmbeddingList.value?.models?.length) {
    return customEmbeddingList.value.models
  }
  return selectedEmbeddingProvider.value?.models || []
})

// ── The custom endpoint: ask it what it serves ──
// Its /models list goes through the same capability records as chat
// (POST /api/ai/models); embedding models come first, models of unknown kind
// after them, and chat or speech models are left out.
const customEmbeddingList = ref(null)      // { models, message } | null
const customEmbeddingListing = ref(false)
const listCustomEmbeddingModels = async () => {
  customEmbeddingListing.value = true
  const current = effectiveEmbeddingModel.value
  try {
    const { models, error, listed } = await fetchProviderModels('openai_compatible', {
      baseUrl: embeddingBaseUrl.value.trim(),
      apiKey: embeddingApiKey.value.trim() || undefined,
      force: true,
    })
    if (!listed) {
      customEmbeddingList.value = {
        models: [],
        message: `It did not list its models${error ? ` (${error})` : ''}. Type the model name below${embeddingApiKey.value.trim() ? '' : ', or enter its key first'}.`,
      }
      return
    }
    const g = modelsForJob(models, 'embedding')
    const embedding = [...g.recommended, ...g.fits].map(m => ({
      id: m.id, label: m.id, dimensions: m.embedding_dimensions, max_input_tokens: m.max_input_tokens,
    }))
    const unknown = g.unknown.map(m => ({ id: m.id, label: `${m.id} (not known to embed)` }))
    customEmbeddingList.value = {
      models: [...embedding, ...unknown],
      message: embedding.length
        ? `It lists ${embedding.length} embedding ${embedding.length === 1 ? 'model' : 'models'}. Test one to read its dimensions.`
        : `It lists no model known to embed${unknown.length ? '; pick one of the others and test it' : ''}.`,
    }
  } finally {
    customEmbeddingListing.value = false
    setEmbeddingModelFromValue(current)
  }
}

// ── Before a switch: what it costs, and whether passages fit ──
// The saved model for the saved provider, to tell a pending switch from a
// re-save of the same choice.
const savedEmbeddingModel = computed(() => {
  if (loadedEmbeddingProvider.value === 'local') return embeddingModel.value
  const p = embeddingCatalog.value.find(c => c.id === loadedEmbeddingProvider.value)
  return remoteEmbeddingModel.value || p?.default_model || ''
})
const embeddingSwitchPending = computed(() =>
  embeddingProvider.value !== loadedEmbeddingProvider.value
  || (!!effectiveEmbeddingModel.value && effectiveEmbeddingModel.value !== savedEmbeddingModel.value)
)
const embeddingInUse = computed(() => {
  const p = embeddingCatalog.value.find(c => c.id === loadedEmbeddingProvider.value)
  if (!p) return ''
  return [p.label, savedEmbeddingModel.value].filter(Boolean).join(' · ')
})

// Real counts from the server (admin-only; null when not allowed or offline).
const embeddingSwitchCost = ref(null)
const loadEmbeddingSwitchCost = async () => {
  try {
    const { data } = await http.get('/api/embedding/switch-cost')
    embeddingSwitchCost.value = data
  } catch {
    embeddingSwitchCost.value = null
  }
}
const embeddingSwitchSentence = computed(() => {
  const p = selectedEmbeddingProvider.value
  const kind = p?.id === 'local' ? 'local' : (p?.kind === 'ollama' ? 'ollama' : 'hosted')
  return switchCostSentence(embeddingSwitchCost.value, { kind, dimensions: selectedEmbeddingModelInfo.value?.dimensions })
})

const embeddingFacts = computed(() =>
  embeddingModelFacts(selectedEmbeddingModelInfo.value, selectedEmbeddingProvider.value)
)
// The same check for the model in use, shown beside the chunk size.
const savedModelChunkFit = computed(() => {
  const p = embeddingCatalog.value.find(c => c.id === loadedEmbeddingProvider.value)
  const limit = (p?.models || []).find(m => m.id === savedEmbeddingModel.value)?.max_input_tokens
  return limit ? chunkFit(chunkSize.value, limit) : null
})
const embeddingChunkFit = computed(() => {
  const limit = selectedEmbeddingModelInfo.value?.max_input_tokens
  return limit ? chunkFit(chunkSize.value, limit) : null
})

const selectedEmbeddingModelInfo = computed(() =>
  embeddingModelChoices.value.find((m) => m.id === embeddingModelChoice.value) || null
)

const effectiveEmbeddingModel = computed(() =>
  embeddingModelChoice.value === '__other__' || !embeddingModelChoices.value.length
    ? embeddingCustomModel.value.trim()
    : embeddingModelChoice.value
)

// A key is usable without typing one: either saved for embeddings under this
// same provider, or reused from the matching AI Providers card.
const embeddingKeyOnFile = computed(() => {
  const p = selectedEmbeddingProvider.value
  if (!p) return false
  if (p.key_source === 'provider_card') return true
  return p.key_source === 'embedding' && embeddingProvider.value === loadedEmbeddingProvider.value && !embeddingApiKeyClear.value
})

const embeddingKeyPlaceholder = computed(() => {
  const p = selectedEmbeddingProvider.value
  if (!p) return ''
  if (p.key_source === 'provider_card') return `blank = use the ${p.label} key from AI Providers`
  if (embeddingKeyOnFile.value) return 'blank = keep the saved key'
  return p.needs_key ? 'paste your key' : 'blank if not required'
})

const embeddingKeyHost = (url) => {
  try { return new URL(url).host } catch { return url }
}

const setEmbeddingModelFromValue = (value) => {
  const choices = embeddingModelChoices.value
  if (!choices.length) {
    embeddingModelChoice.value = ''
    embeddingCustomModel.value = value || ''
  } else if (choices.some((m) => m.id === value)) {
    embeddingModelChoice.value = value
    embeddingCustomModel.value = ''
  } else if (value) {
    embeddingModelChoice.value = '__other__'
    embeddingCustomModel.value = value
  } else {
    embeddingModelChoice.value = selectedEmbeddingProvider.value?.default_model || choices[0].id
    embeddingCustomModel.value = ''
  }
}

const onEmbeddingProviderChange = () => {
  embeddingTestResult.value = null
  embeddingSaveError.value = ''
  embeddingApiKeyClear.value = false
  embeddingApiKey.value = ''   // a key typed for one provider is not for another
  const p = selectedEmbeddingProvider.value
  if (!p) return
  if (p.id === 'local') {
    setEmbeddingModelFromValue(embeddingModel.value)
  } else if (p.id === loadedEmbeddingProvider.value) {
    setEmbeddingModelFromValue(remoteEmbeddingModel.value)
  } else {
    // A model name saved for another provider means nothing here.
    setEmbeddingModelFromValue(p.default_model)
  }
  if (p.id === 'ollama') detectEmbeddingOllama()
}


// Providers OCR can reuse: everything the user configured for chat, minus
// custom OpenAI-compatible endpoints (those need a base_url OCR doesn't carry yet).
// Local Ollama is handled with its own vision-model detection.
const ocrConfiguredProviders = computed(() =>
  configuredProviderIds.value
    .filter((id) => !(getProviderConfig(id)?.isCustom))
    .map((id) => ({ value: id, label: getProviderDisplayName(id) }))
)

const onOcrProviderChange = async () => {
  const id = visionProvider.value

  if (id === 'none') {
    visionModel.value = ''
    visionApiKey.value = ''
    saveOCRSettings()
    return
  }

  if (id === 'ollama') {
    visionModel.value = ''
    visionApiKey.value = ''
    refreshOcrOllamaVisionModels()
    saveOCRSettings()
    return
  }

  // Cloud / hosted provider: reuse the key from its saved config so the user
  // doesn't re-enter anything. OCR needs image input, so the model is the
  // server's vision default for this provider (or a listed model that reads
  // images) over the chat model, which may be text-only.
  const cfg = getProviderConfig(id) || {}
  visionApiKey.value = cfg.apiKey || ''
  const { models, defaults } = await fetchProviderModels(id)
  if (visionProvider.value !== id) return  // changed again while this loaded
  const readsImages = models.find(m => m.vision === true && m.kinds?.includes('chat'))
  visionModel.value = defaults?.vision || readsImages?.id || cfg.model || ''
  saveOCRSettings()
}

const refreshOcrOllamaVisionModels = async () => {
  ocrOllamaVisionLoading.value = true
  try {
    const response = await http.get('/api/ollama/vision-models')
    ocrOllamaVisionModels.value = response.data.models || []
    ocrOllamaVisionTotal.value = response.data.total_models || 0
    ocrOllamaVisionSuggested.value = response.data.suggested || ''
    if (ocrOllamaVisionModels.value.length > 0 && !ocrOllamaVisionModels.value.find(m => m.name === visionModel.value)) {
      visionModel.value = ocrOllamaVisionModels.value[0].name
    }
  } catch {
    ocrOllamaVisionModels.value = []
    ocrOllamaVisionTotal.value = 0
  } finally {
    ocrOllamaVisionLoading.value = false
  }
}

const saveOllamaNumCtx = async () => {
  let v = Number(ollamaNumCtx.value)
  if (!Number.isFinite(v) || v < 512) v = 8192
  v = Math.round(v)
  ollamaNumCtx.value = v
  try {
    await http.post('/api/config', { ollama_num_ctx: v })
  } catch {
    // non-fatal; keep the entered value
  }
}

const saveChatTabSetting = async () => {
  try {
    await http.post('/api/config', { enable_chat_tab: chatTabEnabled.value })
    emit('chat-tab-toggled', chatTabEnabled.value)
  } catch {
    // revert on failure
    chatTabEnabled.value = !chatTabEnabled.value
  }
}

const loadEmbeddingCatalog = async () => {
  try {
    const resp = await http.get('/api/embedding/providers')
    embeddingCatalog.value = resp.data?.providers || []
    embeddingCatalogError.value = ''
  } catch (error) {
    embeddingCatalog.value = []
    embeddingCatalogError.value = 'Could not load the list of embedding providers. Reload the page to try again.'
    console.error('Failed to load embedding providers:', error?.message || error)
  }
}

// The built-in model the server's embedding catalog names as its default.
const localEmbeddingDefault = () =>
  embeddingCatalog.value.find((p) => p.id === 'local')?.default_model || ''

const loadEmbeddingSettings = async (data) => {
  embeddingProvider.value = data.embedding_provider || 'local'
  loadedEmbeddingProvider.value = embeddingProvider.value
  embeddingModel.value = data.embedding_model || ''
  remoteEmbeddingModel.value = data.remote_embedding_model || ''
  embeddingBaseUrl.value = data.embedding_base_url || ''
  ollamaBaseUrl.value = data.ollama_base_url || 'http://localhost:11434'
  // Secrets never round-trip: the server sends a mask plus a "set" flag.
  embeddingApiKey.value = ''
  embeddingApiKeyClear.value = false
  embeddingApiKeySet.value = Boolean(data.embedding_api_key_set)
  embeddingTestResult.value = null
  await Promise.all([loadEmbeddingCatalog(), loadEmbeddingSwitchCost()])
  if (!embeddingModel.value) embeddingModel.value = localEmbeddingDefault()
  if (embeddingProvider.value === 'local') {
    setEmbeddingModelFromValue(embeddingModel.value)
  } else {
    setEmbeddingModelFromValue(remoteEmbeddingModel.value)
  }
}

// The payload /api/config and /api/embedding/test both accept.
const embeddingPayload = () => {
  const payload = {
    embedding_provider: embeddingProvider.value,
    ollama_base_url: ollamaBaseUrl.value,
    embedding_base_url: embeddingBaseUrl.value,
  }
  if (embeddingProvider.value === 'local') {
    payload.embedding_model = effectiveEmbeddingModel.value || localEmbeddingDefault()
  } else {
    payload.remote_embedding_model = effectiveEmbeddingModel.value
  }
  const typed = embeddingApiKey.value.trim()
  if (typed) {
    payload.embedding_api_key = typed
  } else if (embeddingApiKeyClear.value || embeddingProvider.value !== loadedEmbeddingProvider.value) {
    // A stored key belongs to the provider it was saved for; switching
    // providers without a new key drops it rather than sending a Google
    // key to Mistral.
    payload.embedding_api_key = ''
  } else if (embeddingApiKeySet.value) {
    payload.embedding_api_key = MASKED_SECRET // leave unchanged
  }
  return payload
}

const testEmbeddingSettings = async () => {
  embeddingTesting.value = true
  embeddingTestResult.value = null
  try {
    const resp = await http.post('/api/embedding/test', embeddingPayload())
    embeddingTestResult.value = resp.data
  } catch (error) {
    embeddingTestResult.value = {
      ok: false,
      error: error?.message || 'The test could not be run. Check the server log.',
    }
  } finally {
    embeddingTesting.value = false
  }
}

const saveEmbeddingSettings = async () => {
  embeddingSaving.value = true
  embeddingSaveError.value = ''
  try {
    // Never persist a configuration that cannot embed: run the same probe
    // as "Test connection" first, so a bad key or model fails here with a
    // readable reason instead of at the next upload.
    const payload = embeddingPayload()
    const probe = await http.post('/api/embedding/test', payload)
    embeddingTestResult.value = probe.data
    if (!probe.data?.ok) {
      embeddingSaveError.value = 'Not saved — fix the problem above and try again.'
      return
    }
    const result = await http.post('/api/config', payload)
    if (result.data?.success === false) {
      embeddingSaveError.value = (result.data.errors || []).join(' ') || 'Could not save.'
      return
    }
    embeddingSettingsSaved.value = true
    embeddingNeedsReindex.value = result.data.requires_reindex || false
    setTimeout(() => { embeddingSettingsSaved.value = false }, 12000)
    // Re-read so "key on file" and the stored model reflect what was saved,
    // keeping the probe result visible as confirmation of what was saved.
    const response = await http.get('/api/config')
    await loadEmbeddingSettings(response.data || {})
    embeddingTestResult.value = probe.data
  } catch (error) {
    embeddingSaveError.value = error?.message || 'Could not save embedding settings.'
    console.error('Failed to save embedding settings:', error?.message || error)
  } finally {
    embeddingSaving.value = false
  }
}

const detectEmbeddingOllama = async () => {
  embeddingOllamaStatus.value = 'checking'
  embeddingOllamaModels.value = []
  const current = effectiveEmbeddingModel.value
  // Ollama says which of its models embed (the server's capability record);
  // the server falls back to name patterns only for Ollama too old to say.
  const { models, suggested, listed } = await fetchProviderModels('ollama', {
    baseUrl: ollamaBaseUrl.value?.trim() || undefined,
    force: true,
  })
  embeddingOllamaSuggested.value = suggested?.embedding || ''
  if (listed) {
    embeddingOllamaModels.value = models
      .filter((m) => m.kinds?.includes('embedding'))
      .map((m) => ({ name: m.id, record: m }))
    embeddingOllamaStatus.value = 'ok'
  } else {
    embeddingOllamaStatus.value = 'error'
  }
  setEmbeddingModelFromValue(current)
}

const loadOCRSettings = (data) => {
  chatTabEnabled.value = data.enable_chat_tab ?? true
  ollamaNumCtx.value = data.ollama_num_ctx ?? 8192
  ocrEnabled.value = data.enable_ocr || false
  visionProvider.value = data.vision_ocr_provider || 'none'
  visionModel.value = data.vision_ocr_model || ''
  visionApiKey.value = data.vision_ocr_api_key || ''

  // If no key is stored server-side, inherit it from the matching provider's
  // saved config so OCR works without re-entering credentials.
  if (!visionApiKey.value && visionProvider.value !== 'none' && visionProvider.value !== 'ollama') {
    const cfg = getProviderConfig(visionProvider.value)
    if (cfg?.apiKey) {
      visionApiKey.value = cfg.apiKey
    }
    if (!visionModel.value) {
      visionModel.value = OCR_DEFAULT_MODELS[visionProvider.value] || cfg?.model || ''
    }
  }

  if (visionProvider.value === 'ollama') {
    refreshOcrOllamaVisionModels()
  }
}

const saveOCRSettings = async () => {
  try {
    await http.post('/api/config', {
      enable_ocr: ocrEnabled.value,
      vision_ocr_provider: visionProvider.value,
      vision_ocr_model: visionModel.value,
      vision_ocr_api_key: visionApiKey.value,
    })
    ocrSettingsSaved.value = true
    setTimeout(() => { ocrSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save OCR settings:', error?.message || error)
  }
}

// LLM schema inference settings state.
// Note: the inference *threshold* (llm_schema_inference_threshold) is
// server-configured (config.py default 0.5, override via .env) and no longer
// sent from the UI.
const llmSchemaInferenceEnabled = ref(false)
const inferenceSettingsSaved = ref(false)

const loadInferenceSettings = (data) => {
  llmSchemaInferenceEnabled.value = data.enable_llm_schema_inference ?? false
}

const saveInferenceSettings = async () => {
  try {
    await http.post('/api/config', {
      enable_llm_schema_inference: Boolean(llmSchemaInferenceEnabled.value),
    })
    inferenceSettingsSaved.value = true
    setTimeout(() => { inferenceSettingsSaved.value = false }, 5000)
  } catch (error) {
    console.error('Failed to save inference settings:', error?.message || error)
  }
}

// Single /api/config fetch on mount, fanned out to the per-domain loaders.
const loadServerConfig = async () => {
  try {
    const response = await http.get('/api/config')
    const data = response.data || {}
    loadEmbeddingSettings(data)
    loadOCRSettings(data)
    loadInferenceSettings(data)
  } catch {
    // Use defaults
  }
}

// The Features section changed something another section also shows.
const onFeatureChanged = (id) => {
  if (id === 'ocr') loadServerConfig()
  if (id === 'ai') fetchDeploymentDefault().then((info) => { deploymentDefault.value = info; bumpConfig() })
}

// Re-index collection
const reindexing = ref(false)
const reindexSuccess = ref(false)
const reindexCompleted = ref(false)
const reindexError = ref('')

const reindexInProgress = computed(() => {
  const job = backgroundJobsStore.reindexJob
  return job && (job.status === 'pending' || job.status === 'running')
})

// Chunk settings (per-collection)
const chunkSize = ref(600)
const chunkOverlap = ref(100)
const chunkSettingsSaved = ref(false)
const chunkSettingsError = ref('')

const loadChunkSettings = () => {
  const c = collectionStore.currentCollection
  if (c) {
    chunkSize.value = c.chunk_size ?? 600
    chunkOverlap.value = c.chunk_overlap ?? 100
  }
}

const saveChunkSettings = async () => {
  const collectionId = collectionStore.currentCollection?.id
  if (!collectionId) return
  const size = Number(chunkSize.value)
  const overlap = Number(chunkOverlap.value)
  if (!Number.isFinite(size) || size < 100 || size > 4000) {
    chunkSettingsError.value = 'Chunk size must be between 100 and 4000.'
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  if (!Number.isFinite(overlap) || overlap < 0 || overlap >= size) {
    chunkSettingsError.value = 'Overlap must be 0 or greater and less than chunk size.'
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
    return
  }
  try {
    await collectionStore.updateCollection(collectionId, {
      chunk_size: size,
      chunk_overlap: overlap,
    })
    chunkSettingsSaved.value = true
    setTimeout(() => { chunkSettingsSaved.value = false }, 4000)
  } catch (err) {
    chunkSettingsError.value = err?.message || 'Failed to save chunk settings.'
    setTimeout(() => { chunkSettingsError.value = '' }, 5000)
  }
}

// One watcher: the collection object changes whenever its id does (and
// also when its settings are refreshed), so watching both ran this twice.
watch(() => collectionStore.currentCollection, loadChunkSettings, { immediate: true })

// Surface reindex completion inline (sidebar refreshes independently)
watch(() => backgroundJobsStore.reindexJob?.status, (newStatus, oldStatus) => {
  if (newStatus === 'completed' && oldStatus && oldStatus !== 'completed') {
    reindexCompleted.value = true
    setTimeout(() => { reindexCompleted.value = false }, 8000)
  }
})

const startReindex = async () => {
  const collectionId = collectionStore.currentCollection?.id || 'default'
  reindexing.value = true
  reindexError.value = ''
  reindexSuccess.value = false
  try {
    const response = await http.post(`/api/collections/${collectionId}/reindex`)
    backgroundJobsStore.setReindexJob({
      id: response.data.job_id,
      collection_id: collectionId,
      status: 'pending',
      total_documents: 0,
      processed_documents: 0,
      current_file: null,
      started_at: new Date().toISOString(),
    })
    backgroundJobsStore.startPolling()
    reindexSuccess.value = true
    setTimeout(() => { reindexSuccess.value = false }, 8000)
  } catch (error) {
    reindexError.value = error?.message || 'Failed to start re-indexing'
    setTimeout(() => { reindexError.value = '' }, 8000)
  } finally {
    reindexing.value = false
  }
}

// System info (read-only; offline flag comes from /health)
const systemInfo = ref({ db_backend: 'sqlite', private_collections: false, user_id: 'default', offline_mode: false })

// Air-gapped deployments: the backend rejects cloud providers, so don't
// offer them. Only local Ollama and self-hosted endpoints remain.
const visibleProviderDefs = computed(() => {
  if (systemInfo.value.offline_mode) return PROVIDER_DEFS.filter(d => d.type === 'local')
  // Hosted: hide "local machine" providers — localhost is the server there,
  // not the machine the user is browsing from. Server-side .env still works.
  if (isRemoteDeployment()) return PROVIDER_DEFS.filter(d => d.type !== 'local')
  return PROVIDER_DEFS
})
const availableEndpointPresets = computed(() => {
  let presets = CUSTOM_ENDPOINT_PRESETS
  if (systemInfo.value.offline_mode) presets = presets.filter(p => !p.baseUrl || !p.baseUrl.startsWith('https://'))
  if (isRemoteDeployment()) presets = presets.filter(p => !p.baseUrl || !p.baseUrl.includes('localhost'))
  return presets
})

async function loadSystemInfo() {
  try {
    const response = await http.get('/api/user/me')
    systemInfo.value = { ...systemInfo.value, ...response.data }
  } catch (err) {
    console.error('Failed to load system info:', err)
  }
  try {
    const health = await http.get('/health')
    systemInfo.value.offline_mode = !!health.data.offline_mode
  } catch {
    // leave default
  }
}

// Theme
const selectedTheme = ref('light')

// Clear data. The job belongs to the server, so "clearing" is read from the
// jobs store: it stays right after leaving this tab and coming back, and a
// refresh or another tab sees the same thing.
const clearStarting = ref(false)
const clearSuccess = ref(false)
const clearError = ref('')
const clearModal = ref(null)
const clearJob = computed(() => backgroundJobsStore.allJobs.find(j =>
  j.type === 'delete' && j.deletingEverything &&
  j.collectionId === collectionStore.currentCollectionId &&
  backgroundJobsStore.isActiveStatus(j.status)
))
const clearing = computed(() => clearStarting.value || !!clearJob.value)

// Bumped after every provider-config write so computeds/template re-read
// localStorage (which is not reactive by itself). Also broadcasts to the
// other surfaces (top-bar pill, open tabs) via the shared change event.
const configVersion = ref(0)
const bumpConfig = () => {
  configVersion.value++
  notifyProviderChange()
}

// ── App-wide default provider ──
// The "Use as default" control writes ai_settings.provider — step 2 of the
// resolution chain every surface goes through (see aiProviders.js).
const activeProviderId = computed(() => {
  configVersion.value // reactivity hook
  return getActiveProvider()
})

const setDefaultProvider = (id) => {
  setActiveProviderLS(id)
  bumpConfig()
}

// A freshly connected provider becomes the default when none was ever chosen,
// so the top-bar pill and "Default" badge reflect what the chain resolves to.
const ensureDefaultProvider = (id) => {
  if (!getActiveProvider()) setActiveProviderLS(id)
}

// Providers with a server-stored team key (hosted/multi-user instances).
const serverProviderIds = ref([])
const isServerProvider = (id) => serverProviderIds.value.includes(id)

// The provider this deployment configured for everyone (on-prem private
// model). Read-only here: it lives in the server's environment.
const deploymentDefault = ref({ configured: false })

// It carries the "Default" badge when it is what the resolution chain lands
// on — which includes the common on-prem case of a browser that has chosen
// nothing, since the deployment's provider is first in the configured list.
const deploymentIsDefault = computed(() =>
  deploymentDefault.value.configured &&
  (activeProviderId.value === deploymentDefault.value.provider || !activeProviderId.value)
)

// Ids of all currently-configured providers
const configuredProviderIds = computed(() => {
  configVersion.value // reactivity hook
  return getConfiguredProviderIds()
})
const hasAnyProvider = computed(() => configuredProviderIds.value.length > 0)

// Expand/edit state
const expandedProvider = ref(null)
const editBuffer = ref({})
const showEditKey = ref(false)
const connectingProvider = ref(null)
const savingProvider = ref(null)

// Live model lists, keyed by provider id: { loading, models, error }
const modelState = ref({})

// Ollama detection state
const ollamaCheckStatus = ref(null) // null | 'checking' | 'available' | 'unavailable'
const ollamaError = ref('')

// "Add your own endpoint" form
const showAddEndpoint = ref(false)
const addingEndpoint = ref(false)
const addEndpointError = ref('')
const newEndpoint = ref({ presetId: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', apiKey: '' })
const selectedPreset = computed(() => CUSTOM_ENDPOINT_PRESETS.find(p => p.id === newEndpoint.value.presetId) || null)

const onPresetChange = () => {
  const p = selectedPreset.value
  addEndpointError.value = ''
  if (!p) return
  newEndpoint.value.name = p.id === 'other' ? '' : p.name
  newEndpoint.value.baseUrl = p.baseUrl || ''
}

// Derived: all custom provider configs
const customProvidersConfig = computed(() => {
  configVersion.value // reactivity hook
  return getProvidersConfig().filter(p => p.isCustom)
})

const hasLocalKey = (id) => !!getProviderConfig(id)?.apiKey

const isProviderConfigured = (id) => {
  const cfg = getProviderConfig(id)
  const def = PROVIDER_DEFS.find(d => d.id === id)
  if (!def) return !!cfg?.baseUrl  // custom
  if (def.type === 'local') return !!cfg?.available
  return !!cfg?.apiKey || isServerProvider(id)
}

// ── The provider list, grouped by where the text goes ──
// "On your network" first: for a regulated team that is the question that
// decides everything else. Within a group the deployment's own provider
// leads, then whatever is connected, then the rest in catalogue order.
const PROVIDER_GROUPS = [
  {
    id: 'network',
    title: 'On your network',
    note: 'Questions and the passages they draw on stay on machines you or your organisation run.',
  },
  {
    id: 'hosted',
    title: 'Hosted',
    note: 'Questions and the passages they draw on are sent to the company that runs the model.',
  },
]

const providerGroups = computed(() => {
  configVersion.value // reactivity hook
  serverProviderIds.value // reactivity hook: team keys count as connected
  const rows = []
  if (deploymentDefault.value.configured) {
    rows.push({
      kind: 'deployment', key: 'deployment', id: deploymentDefault.value.provider,
      name: deploymentDefault.value.label, connected: true, rank: 0,
      location: providerLocation(deploymentDefault.value.provider),
    })
  }
  visibleProviderDefs.value.forEach((def, i) => {
    const connected = isProviderConfigured(def.id)
    rows.push({
      kind: 'builtin', key: def.id, id: def.id, name: def.name, def, connected,
      rank: (connected ? 1 : 3) + i / 100, location: def.type === 'local' ? 'network' : 'hosted',
    })
  })
  for (const cp of customProvidersConfig.value) {
    rows.push({
      kind: 'custom', key: cp.id, id: cp.id, name: cp.name, cp, connected: true,
      rank: 2, location: isPrivateUrl(cp.baseUrl) ? 'network' : 'hosted',
    })
  }
  return PROVIDER_GROUPS
    .map(g => ({ ...g, rows: rows.filter(r => r.location === g.id).sort((a, b) => a.rank - b.rank) }))
    .filter(g => g.rows.length)
})

// The second line of a row: the model in use once connected, else what the
// provider is.
const rowDetail = (row) => {
  const model = getProviderConfig(row.id)?.model || ''
  if (row.kind === 'custom') return model ? `${model} · ${row.cp.baseUrl}` : row.cp.baseUrl
  if (row.connected) return model || 'Provider’s default model'
  return row.def?.description || ''
}

// ── Live model lists ──
// Everything, recommendations included, comes from fetchProviderModels
// (backend POST /api/ai/models); the server answers from its own table when
// the provider cannot be asked. Each picker keeps only the models that can
// do its job (modelsForJob), so an embedding or speech model is never
// offered for answers.

const modelsFor = (id) => modelState.value[id]?.models || []
const firstChatModel = (models) => {
  const g = modelsForJob(models, 'answers')
  return (g.recommended[0] || g.fits[0] || g.unknown[0] || null)?.id || ''
}

const loadModels = async (id, { force = false } = {}) => {
  modelState.value = { ...modelState.value, [id]: { ...(modelState.value[id] || {}), loading: true } }
  const { models, error } = await fetchProviderModels(id, force ? { force: true } : {})
  modelState.value = { ...modelState.value, [id]: { loading: false, models, error } }
  // A stored model the provider doesn't list is shown as a custom id.
  if (expandedProvider.value === id && editBuffer.value.model && !models.some(m => m.id === editBuffer.value.model)) {
    editBuffer.value.customModelMode = true
  }
}

const onModelSelect = (id, value) => {
  if (value === '__custom__') {
    editBuffer.value.customModelMode = true
    return
  }
  editBuffer.value.customModelMode = false
  editBuffer.value.model = value
  saveModelIfConfigured(id)
}

const saveModelIfConfigured = (id) => {
  if (!isProviderConfigured(id)) return
  upsertProviderConfig(id, { model: editBuffer.value.model?.trim() || '' })
  bumpConfig()
}

const toggleExpand = (id) => {
  if (expandedProvider.value === id) {
    expandedProvider.value = null
    return
  }
  expandedProvider.value = id
  showEditKey.value = false
  // Init edit buffer from stored config
  const cfg = getProviderConfig(id) || {}
  editBuffer.value = {
    apiKey: cfg.apiKey || '',
    model: cfg.model || '',
    baseUrl: cfg.baseUrl || (id === 'ollama' ? 'http://localhost:11434' : ''),
    name: cfg.name || '',
    keyDirty: false,
    keyStatus: '',
    keyError: '',
    customModelMode: false,
  }
  if (id === 'ollama') {
    if (ollamaCheckStatus.value === null) detectOllama()
  } else if (isProviderConfigured(id) && !modelState.value[id]) {
    loadModels(id)
  }
}

// P5: what the chosen model can do, by the job it may be picked for.
const JOB_NAMES = { answers: 'Answers', tools: 'Deep search and agent mode', vision: 'Scanned pages and figures' }
const jobChecks = ref({})
async function checkModelJobs(id) {
  const cfg = getProviderConfig(id) || {}
  jobChecks.value = { ...jobChecks.value, [id]: { running: true } }
  const out = await testModelJobs(id, { apiKey: cfg.apiKey || '', baseUrl: cfg.baseUrl || '', model: cfg.model || '' })
  jobChecks.value = { ...jobChecks.value, [id]: { running: false, out } }
}

const connectProvider = async (id) => {
  const key = editBuffer.value.apiKey?.trim()
  if (!key) return
  connectingProvider.value = id
  editBuffer.value.keyStatus = ''
  editBuffer.value.keyError = ''

  const model = editBuffer.value.model?.trim() || ''
  const { valid, error } = await testProviderConnection(id, { apiKey: key, model })
  if (!valid) {
    editBuffer.value.keyStatus = 'invalid'
    editBuffer.value.keyError = error || 'Connection failed'
    connectingProvider.value = null
    return
  }

  upsertProviderConfig(id, { apiKey: key, model })
  ensureDefaultProvider(id)
  bumpConfig()
  editBuffer.value.keyStatus = 'valid'
  editBuffer.value.keyDirty = false

  // Best-effort: also store the key server-side so background features
  // (OCR, indexing) can use it. Failure is non-fatal — browser key still works.
  try {
    await http.post('/api/agent/config', { provider: id, api_key: key })
  } catch (err) {
    console.warn('Could not store key server-side (browser key still works):', err?.message || err)
  }

  invalidateModelCache(id)
  await loadModels(id, { force: true })
  connectingProvider.value = null
}

const removeProvider = (id) => {
  removeProviderConfig(id)
  invalidateModelCache(id)
  const next = { ...modelState.value }
  delete next[id]
  modelState.value = next
  // Removing the app-wide default hands the role to the next configured
  // provider (or clears it) instead of leaving a dangling id in ai_settings.
  if (getActiveProvider() === id) {
    setActiveProviderLS(getConfiguredProviderIds()[0] || '')
  }
  bumpConfig()
  expandedProvider.value = null
}

// Ollama: probe via the shared model-list helper — non-empty list = running.
const detectOllama = async () => {
  ollamaCheckStatus.value = 'checking'
  ollamaError.value = ''
  const baseUrl = editBuffer.value.baseUrl?.trim() || 'http://localhost:11434'
  const { models, defaults, error } = await fetchProviderModels('ollama', { baseUrl, force: true })
  if (models.length > 0) {
    ollamaCheckStatus.value = 'available'
    modelState.value = { ...modelState.value, ollama: { loading: false, models, error: null } }
    // Pre-select the stored model if still present, else the server's
    // default (an installed chat model), else the first chat model: the
    // first one listed may be an embedding model, which cannot answer.
    const stored = getProviderConfig('ollama')?.model
    if (stored && models.some(m => m.id === stored)) {
      editBuffer.value.model = stored
    } else if (!models.some(m => m.id === editBuffer.value.model)) {
      editBuffer.value.model = defaults?.model || firstChatModel(models)
    }
    return true
  }
  ollamaCheckStatus.value = 'unavailable'
  ollamaError.value = error || ''
  return false
}

const connectOllama = async () => {
  savingProvider.value = 'ollama'
  const ok = await detectOllama()
  if (ok) {
    upsertProviderConfig('ollama', {
      baseUrl: editBuffer.value.baseUrl?.trim() || 'http://localhost:11434',
      model: editBuffer.value.model || '',
      available: true,
    })
    ensureDefaultProvider('ollama')
    bumpConfig()
    invalidateModelCache('ollama')
  }
  savingProvider.value = null
}

// Add a custom OpenAI-compatible endpoint: test first, save only on success.
const testAndAddEndpoint = async () => {
  const name = newEndpoint.value.name?.trim()
  const baseUrl = newEndpoint.value.baseUrl?.trim()
  if (!name || !baseUrl) return
  addingEndpoint.value = true
  addEndpointError.value = ''
  const apiKey = newEndpoint.value.apiKey?.trim() || ''
  const id = `custom_${Date.now()}`

  const { valid, error } = await testProviderConnection(id, { apiKey, baseUrl })
  if (!valid) {
    addEndpointError.value = error || 'Could not reach the endpoint'
    addingEndpoint.value = false
    return
  }

  // Same stored shape as before (ChatTab/SearchTab rely on it)
  upsertProviderConfig(id, { id, name, baseUrl, apiKey, model: '', isCustom: true })
  ensureDefaultProvider(id)
  bumpConfig()

  const { models } = await fetchProviderModels(id, { force: true })
  modelState.value = { ...modelState.value, [id]: { loading: false, models, error: null } }
  const first = firstChatModel(models)
  if (first) {
    upsertProviderConfig(id, { model: first })
    bumpConfig()
  }

  newEndpoint.value = { presetId: 'lmstudio', name: 'LM Studio', baseUrl: 'http://localhost:1234/v1', apiKey: '' }
  showAddEndpoint.value = false
  addingEndpoint.value = false
  toggleExpand(id)
}

const loadAIState = () => {
  migrateLegacySettings()
}

// Theme
const systemTheme = () =>
  window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
const applyTheme = () => {
  if (selectedTheme.value === 'system') {
    document.documentElement.setAttribute('data-theme', systemTheme())
    localStorage.removeItem('theme')
  } else {
    document.documentElement.setAttribute('data-theme', selectedTheme.value)
    localStorage.setItem('theme', selectedTheme.value)
  }
  window.dispatchEvent(new CustomEvent('theme-changed'))
}

// Clear data
const confirmClearAll = () => {
  clearSuccess.value = false
  clearError.value = ''
  clearModal.value?.showModal()
}

const closeClearModal = () => {
  if (!clearStarting.value) {
    clearModal.value?.close()
  }
}

const clearAllData = async () => {
  clearStarting.value = true
  clearError.value = ''
  clearSuccess.value = false

  try {
    const job = await backgroundJobsStore.deleteSources({
      collectionId: collectionStore.currentCollectionId,
    })
    clearModal.value?.close()
    // No job means the collection was already empty.
    if (!job) showClearSuccess()
  } catch (error) {
    clearError.value = error?.message || 'Failed to clear data'
    clearModal.value?.close()
  } finally {
    clearStarting.value = false
  }
}

// Generated work: reviews and verdicts (Library), saved reports (artifacts).
const outputs = ref(null)
const outputsModal = ref(null)
const deleteReviews = ref(true)
const deleteReports = ref(true)
const deletingOutputs = ref(false)
const outputsDone = ref('')
const outputsError = ref('')
const countOf = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many}`
const outputsSummary = computed(() => {
  const { reviews = 0, screenings = 0, reports = 0 } = outputs.value || {}
  if (!reviews && !screenings && !reports) return 'Nothing generated yet.'
  return [
    countOf(reviews, 'review', 'reviews'),
    countOf(screenings, 'screening verdict', 'screening verdicts'),
    countOf(reports, 'saved report', 'saved reports'),
  ].join(' · ')
})
const hasOutputs = computed(() => {
  const { reviews = 0, screenings = 0, reports = 0 } = outputs.value || {}
  return reviews + screenings + reports > 0
})

const loadOutputs = async () => {
  const collectionId = collectionStore.currentCollectionId
  try {
    const data = await researchApi.outputs(collectionId)
    // A slower answer for the collection just left must not land on this one.
    if (collectionId === collectionStore.currentCollectionId) outputs.value = data
  } catch {
    outputs.value = null
  }
}
watch([activeSection, () => collectionStore.currentCollectionId], ([section]) => {
  outputsDone.value = ''
  outputsError.value = ''
  if (section === 'data') loadOutputs()
}, { immediate: true })

const confirmDeleteOutputs = () => {
  outputsDone.value = ''
  outputsError.value = ''
  deleteReviews.value = (outputs.value?.reviews || 0) + (outputs.value?.screenings || 0) > 0
  deleteReports.value = (outputs.value?.reports || 0) > 0
  outputsModal.value?.showModal()
}

const deleteOutputs = async () => {
  deletingOutputs.value = true
  try {
    const deleted = await researchApi.deleteOutputs(collectionStore.currentCollectionId, {
      ...(deleteReviews.value ? { reviews: true } : {}),
      ...(deleteReports.value ? { reports: true } : {}),
    })
    const parts = []
    if (deleteReviews.value) {
      parts.push(countOf(deleted.reviews, 'review', 'reviews'), countOf(deleted.screenings, 'screening verdict', 'screening verdicts'))
    }
    if (deleteReports.value) parts.push(countOf(deleted.reports, 'saved report', 'saved reports'))
    outputsDone.value = `Deleted ${parts.join(', ')}`
    await loadOutputs()
  } catch (error) {
    outputsError.value = error?.message || 'Could not delete them'
  } finally {
    deletingOutputs.value = false
    outputsModal.value?.close()
  }
}

const showClearSuccess = () => {
  clearSuccess.value = true
  setTimeout(() => {
    clearSuccess.value = false
  }, 5000)
}

// The job finished, while this tab was showing it.
watch(clearJob, (now, before) => {
  if (now || !before) return
  const finished = backgroundJobsStore.allJobs.find(j => j.type === 'delete' && j.id === before.id)
  if (!finished || backgroundJobsStore.isActiveStatus(finished.status)) return // switched collection
  if (finished.status === 'completed') showClearSuccess()
  else clearError.value = finished.error || 'Failed to clear data'
  emit('data-cleared')
})

// Lifecycle
onMounted(() => {
  loadAIState()
  loadSystemInfo()

  // Which providers have a server-stored team key ("Team key" badge)
  fetchServerProviderIds().then((ids) => {
    serverProviderIds.value = ids
    bumpConfig()
  })

  // The deployment's own provider, shown as an already-connected row
  fetchDeploymentDefault().then((info) => {
    deploymentDefault.value = info
    bumpConfig()
  })

  // Load theme
  selectedTheme.value = localStorage.getItem('theme') || 'system'

  loadServerConfig()
})
</script>

