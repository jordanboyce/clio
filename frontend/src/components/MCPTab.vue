<template>
  <div class="space-y-6">
    <header>
      <h1 class="text-xl font-semibold">Connect your AI tools</h1>
      <p class="mt-2 text-sm text-base-content/60 max-w-2xl">
        <template v-if="oauth.enabled">
          Search your sources from Claude Desktop, claude.ai, ChatGPT, Claude Code, Codex, AnythingLLM, or VS Code using MCP.
        </template>
        <template v-else>
          Search your sources from Claude Code, Codex, Cursor, VS Code, Gemini CLI, the agent SDKs and forty other MCP clients. Pick yours under Connect a client below.
        </template>
        Your client receives retrieved passages, so choose a client you trust with this data.
      </p>
    </header>

    <!-- Enable toggle -->
    <div class="card bg-base-200">
      <div class="card-body py-4">
        <div class="flex items-center justify-between">
          <div>
            <span class="font-semibold text-sm">MCP Server</span>
            <span :class="mcpSettings.enable_mcp ? 'badge badge-success badge-sm ml-2' : 'badge badge-ghost badge-sm ml-2'">
              {{ mcpSettings.enable_mcp ? 'Enabled' : 'Disabled' }}
            </span>
          </div>
          <input v-if="userStore.adminConsole" v-model="mcpSettings.enable_mcp" aria-label="Enable MCP server" type="checkbox" class="toggle toggle-primary" @change="saveMcpToggle" />
        </div>
      </div>
    </div>

    <div v-if="!mcpSettings.enable_mcp" class="alert">
      <span class="text-sm">{{ userStore.adminConsole ? 'Enable MCP to connect your AI tools.' : 'Ask your administrator to enable MCP for this deployment.' }}</span>
    </div>

    <template v-else>

      <!-- Sign in from the client (OAuth). Shown when the deployment names an
           authorization server for /mcp; the app is only the resource server,
           so there is nothing to mint here — just the URL to paste. -->
      <div v-if="oauth.enabled" class="card bg-base-200" data-testid="mcp-oauth">
        <div class="card-body space-y-3">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <h3 class="card-title text-base">Sign in from your client</h3>
            <span class="badge badge-success badge-sm">OAuth</span>
          </div>
          <p class="text-sm text-base-content/60">
            Claude Desktop, claude.ai, ChatGPT and Claude Code need only this URL. They send you to
            <span class="font-medium text-base-content/80">{{ oauthIssuerHost }}</span> to sign in, and then see
            exactly the collections you can. No token to copy, nothing to revoke here — sign out at your
            identity provider.
          </p>
          <div class="flex items-center gap-2 max-w-2xl">
            <code id="mcp-oauth-url" class="font-mono text-xs bg-base-100 rounded px-2 py-1.5 flex-1 overflow-x-auto whitespace-nowrap">{{ oauth.resource }}</code>
            <button class="btn btn-xs" @click="copyText(oauth.resource, 'Server URL')">Copy</button>
          </div>
          <ul class="text-xs text-base-content/60 space-y-1 max-w-2xl">
            <li>
              <span class="font-medium text-base-content/80">Claude Desktop, claude.ai:</span>
              Settings → Connectors → Add custom connector → paste the URL. If your identity provider does not
              register clients automatically, enter the client ID it gave you under Advanced settings.
            </li>
            <li>
              <span class="font-medium text-base-content/80">ChatGPT:</span>
              Settings → Apps &amp; Connectors → Developer mode → Create → paste the URL, authentication OAuth.
            </li>
            <li>
              <span class="font-medium text-base-content/80">Claude Code:</span> <code class="font-mono">claude mcp add --transport http clio {{ oauth.resource }}</code>,
              then <code class="font-mono">claude mcp login clio</code>.
            </li>
          </ul>
          <p v-if="oauth.scope" class="text-xs text-base-content/50">
            Tokens must carry the <code class="font-mono">{{ oauth.scope }}</code> scope; clients request it automatically.
          </p>
        </div>
      </div>

      <!-- Personal access tokens -->
      <div class="card bg-base-200">
        <div class="card-body space-y-4">
          <div>
            <h3 class="card-title text-base">Personal access tokens</h3>
            <p class="mt-1 text-sm text-base-content/60">
              Create a token for each device. Tokens let clients read the collections you can access.
              You can revoke them here at any time; adding or updating sources is optional.
            </p>
          </div>

          <div v-if="newTokenPlaintext" class="alert alert-warning py-3">
            <div class="space-y-2 w-full">
              <p class="text-sm font-medium">Copy this now — it won't be shown again.</p>
              <div class="flex items-center gap-2">
                <code class="font-mono text-xs bg-base-100 rounded px-2 py-1 flex-1 overflow-x-auto whitespace-nowrap">{{ newTokenPlaintext }}</code>
                <button class="btn btn-xs" @click="copyText(newTokenPlaintext, 'Token')">Copy</button>
                <button class="btn btn-xs btn-ghost" @click="newTokenPlaintext = ''">Dismiss</button>
              </div>
            </div>
          </div>

          <div class="flex items-end gap-2 max-w-md">
            <div class="form-control flex-1">
              <label class="label p-0 pb-1" for="mcp-token-name"><span class="label-text font-medium">Name this connection</span></label>
              <input
                id="mcp-token-name"
                v-model="newTokenName"
                type="text"
                placeholder="e.g. Work laptop — Claude Code"
                class="input input-bordered input-sm w-full"
                @keyup.enter="createToken"
              />
            </div>
            <button class="btn btn-sm btn-primary" :disabled="tokenCreating" @click="createToken">
              <span v-if="tokenCreating" class="loading loading-spinner loading-xs"></span>
              Generate token
            </button>
          </div>

          <!-- Writes are opt-in per token: a leaked read token can search,
               a leaked write token can plant content. -->
          <label class="flex items-start gap-2.5 max-w-xl cursor-pointer">
            <input v-model="newTokenCanWrite" type="checkbox" class="checkbox checkbox-sm checkbox-warning mt-0.5" />
            <span>
              <span class="block text-sm font-medium">Allow adding and updating sources</span>
              <span class="block text-xs text-base-content/55">
                Enables the <code class="font-mono">write_document</code> tool for this token, so an agent can save
                notes, summaries, or markdown into a collection. Off, the token can only read. Collection write
                permissions and the content policy still apply.
              </span>
            </span>
          </label>

          <!-- Lifetime: "never" stays a deliberate choice, not the silent default
               of a form that has no other option. -->
          <div class="form-control max-w-xs">
            <label class="label p-0 pb-1" for="mcp-token-expiry"><span class="label-text font-medium">Expires</span></label>
            <select id="mcp-token-expiry" v-model="newTokenExpiryDays" class="select select-bordered select-sm w-full">
              <option v-for="opt in expiryOptions" :key="String(opt.value)" :value="opt.value">{{ opt.label }}</option>
            </select>
          </div>

          <!-- Allowlist scoping: the token sees ONLY the ticked collections,
               whatever their sensitivity. Off, it sees everything its owner can
               (plus any restricted grants below). -->
          <label class="flex items-start gap-2.5 max-w-xl cursor-pointer">
            <input id="mcp-token-allowlist" v-model="newTokenAllowlist" type="checkbox" class="checkbox checkbox-sm mt-0.5" />
            <span>
              <span class="block text-sm font-medium">Limit this token to specific collections</span>
              <span class="block text-xs text-base-content/55">
                The agent will only see the collections you tick, nothing else. Use this for a client
                that should work on one project or archive.
              </span>
            </span>
          </label>
          <div v-if="newTokenAllowlist" class="rounded-lg border border-base-300 bg-base-100 p-3 max-w-xl">
            <p class="text-xs font-medium">Collections this token may see</p>
            <p v-if="!newTokenScope.length" class="text-[11px] text-warning mt-0.5">Tick at least one collection.</p>
            <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2">
              <label v-for="c in mcpCollections" :key="c.id" class="flex items-center gap-1.5 text-xs cursor-pointer">
                <input v-model="newTokenScope" type="checkbox" class="checkbox checkbox-xs" :value="c.id" />
                {{ c.name }}
                <span v-if="c.sensitivity === 'restricted'" class="badge badge-xs badge-error badge-outline">restricted</span>
              </label>
            </div>
          </div>

          <!-- Restricted collections are invisible to every MCP client unless
               a token is explicitly scoped to them: the grant is made here,
               per token, and shows in the table so it can be revoked knowingly. -->
          <div v-if="!newTokenAllowlist && restrictedCollections.length" class="rounded-lg border border-error/30 bg-error/5 p-3 max-w-xl">
            <p class="text-xs font-medium flex items-center gap-1.5">
              <ShieldAlert :size="13" class="text-error" aria-hidden="true" />
              Grant this token access to restricted collections
            </p>
            <p class="text-[11px] text-base-content/55 mt-0.5">
              Restricted collections never appear over MCP by default. Tick one to let this specific token reach it.
            </p>
            <div class="flex flex-wrap gap-x-4 gap-y-1 mt-2">
              <label v-for="c in restrictedCollections" :key="c.id" class="flex items-center gap-1.5 text-xs cursor-pointer">
                <input v-model="newTokenScope" type="checkbox" class="checkbox checkbox-xs checkbox-error" :value="c.id" />
                {{ c.name }}
              </label>
            </div>
          </div>

          <div v-if="tokens.length" class="overflow-x-auto">
            <table class="table table-sm">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Token</th>
                  <th>Access</th>
                  <th>Collections</th>
                  <th>Created</th>
                  <th>Expires</th>
                  <th>Last used</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="t in tokens" :key="t.id" :class="{ 'opacity-50': t.revoked_at || isExpired(t) }">
                  <td>{{ t.name }}</td>
                  <td class="font-mono text-xs text-base-content/60">{{ t.token_prefix }}…</td>
                  <td class="text-xs whitespace-nowrap">
                    <span v-if="t.can_write" class="badge badge-xs badge-warning badge-outline">read + write</span>
                    <span v-else class="text-base-content/40">read-only</span>
                  </td>
                  <td class="text-xs">
                    <span v-if="t.allowlist" class="flex flex-wrap gap-1">
                      <span class="badge badge-xs badge-outline">only</span>
                      <span v-for="cid in t.collection_scope || []" :key="cid" class="badge badge-xs badge-outline">{{ collectionName(cid) }}</span>
                    </span>
                    <span v-else-if="!t.collection_scope || !t.collection_scope.length" class="text-base-content/40">all visible</span>
                    <span v-else class="flex flex-wrap gap-1">
                      <span class="text-base-content/40">all visible +</span>
                      <span v-for="cid in t.collection_scope" :key="cid" class="badge badge-xs badge-error badge-outline">{{ collectionName(cid) }}</span>
                    </span>
                  </td>
                  <td class="text-xs text-base-content/60">{{ formatDate(t.created_at) }}</td>
                  <td class="text-xs whitespace-nowrap">
                    <span v-if="!t.expires_at" class="text-base-content/40">Never</span>
                    <span v-else-if="isExpired(t)" class="badge badge-xs badge-error badge-outline">Expired</span>
                    <span v-else class="text-base-content/60">{{ formatDate(t.expires_at) }}</span>
                  </td>
                  <td class="text-xs text-base-content/60 whitespace-nowrap">
                    <template v-if="t.last_used_at">{{ formatDate(t.last_used_at) }}<span v-if="t.use_count" class="text-base-content/40"> · {{ t.use_count }} {{ t.use_count === 1 ? 'call' : 'calls' }}</span></template>
                    <template v-else>Never</template>
                  </td>
                  <td class="text-right">
                    <span v-if="t.revoked_at" class="badge badge-ghost badge-sm">Revoked</span>
                    <button v-else class="btn btn-xs btn-ghost text-error" @click="revokeToken(t)">Revoke</button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <p v-else class="text-xs text-base-content/40">No tokens yet — generate one above to connect an MCP client.</p>
        </div>
      </div>

      <!-- Connect a client -->
      <div class="card bg-base-200">
        <div class="card-body space-y-4">
          <div>
            <h3 class="card-title text-base">Connect a client</h3>
            <p class="mt-1 text-sm text-base-content/60">
              Take exactly what your client needs: one value, the server entry to merge into a file you already have,
              or a whole file or command in the client's own format. Nothing here changes the server.
            </p>
          </div>

          <!-- Every connection fact on its own, so a person can copy one value -->
          <div class="rounded-lg border border-base-300 bg-base-100 p-3" data-testid="mcp-connection-details">
            <div class="mb-2 text-xs font-medium text-base-content/60">Connection details</div>
            <dl class="space-y-1 text-xs">
              <div v-for="fact in connectionFacts" :key="fact.label" class="flex items-center gap-2 min-w-0">
                <dt class="w-36 shrink-0 text-base-content/50">{{ fact.label }}</dt>
                <dd class="flex-1 min-w-0 truncate font-mono" :title="fact.value">{{ fact.value }}</dd>
                <button type="button" class="btn btn-ghost btn-xs" :aria-label="`Copy ${fact.label}`" @click="copyText(fact.value, fact.label)">Copy</button>
              </div>
            </dl>
          </div>

          <!-- Choices that shape every recipe -->
          <div class="grid gap-3 sm:grid-cols-2" data-testid="mcp-export-options">
            <div class="space-y-2">
              <div class="form-control">
                <label class="label p-0 pb-1" for="mcp-export-credential"><span class="label-text font-medium">Credential</span></label>
                <select id="mcp-export-credential" v-model="credentialMode" class="select select-bordered select-sm w-full">
                  <option value="token">Token written into the config</option>
                  <option value="env">Token from an environment variable</option>
                  <option value="none">No credential (open server, or sign in with OAuth)</option>
                </select>
              </div>
              <div v-if="credentialMode !== 'none' && activeTokens.length" class="form-control">
                <label class="label p-0 pb-1" for="mcp-export-token"><span class="label-text text-xs">Token</span></label>
                <select id="mcp-export-token" v-model="exportTokenId" class="select select-bordered select-sm w-full">
                  <option v-for="t in activeTokens" :key="t.id" :value="t.id">{{ t.name }}</option>
                </select>
              </div>
              <div v-if="credentialMode === 'env'" class="form-control">
                <label class="label p-0 pb-1" for="mcp-export-envvar"><span class="label-text text-xs">Variable name</span></label>
                <input id="mcp-export-envvar" v-model.trim="envVarName" class="input input-bordered input-sm w-full font-mono" spellcheck="false" />
              </div>
              <p v-if="credentialMode !== 'none' && !activeTokens.length && openDeployment" class="text-xs text-base-content/50">
                This server is open, so a client can connect without a token. One is still worth making to keep a client read-only,
                limit it to some collections, or see its calls in the audit trail.
              </p>
              <p v-else-if="credentialMode !== 'none' && !activeTokens.length" class="text-xs text-warning">
                Generate a token above first; until then the recipes carry a placeholder.
              </p>
            </div>
            <div class="space-y-2">
              <div class="form-control">
                <label class="label p-0 pb-1" for="mcp-export-name"><span class="label-text font-medium">Server name</span></label>
                <input id="mcp-export-name" v-model.trim="serverNameInput" class="input input-bordered input-sm w-full font-mono" :placeholder="defaultServerName" spellcheck="false" />
              </div>
              <div class="form-control">
                <label class="label p-0 pb-1" for="mcp-export-transport"><span class="label-text font-medium">Transport</span></label>
                <select id="mcp-export-transport" v-model="transport" class="select select-bordered select-sm w-full">
                  <option value="http">Streamable HTTP (the standard)</option>
                  <option value="sse">Legacy HTTP+SSE (older clients)</option>
                </select>
              </div>
            </div>
          </div>

          <fieldset class="rounded-lg border border-base-300 bg-base-100 p-3" data-testid="mcp-export-collections">
            <legend class="px-1 text-xs font-medium text-base-content/60">Collections</legend>
            <p class="mb-2 text-xs text-base-content/50">
              Tick none and the agent picks collections itself with list_collections. Tick one to start searches there.
              Tick several to get one server entry per collection. This only sets where searches start; the token decides what is visible.
            </p>
            <div class="flex flex-wrap gap-x-4 gap-y-1">
              <label v-for="c in mcpCollections" :key="c.id" class="label cursor-pointer gap-2 p-0">
                <input v-model="exportCollectionIds" type="checkbox" class="checkbox checkbox-xs" :value="c.id" :aria-label="`Collection ${c.name}`" />
                <span class="label-text text-xs">{{ c.name }}</span>
              </label>
            </div>
          </fieldset>

          <div class="rounded-lg border border-base-300 bg-base-100 overflow-hidden">
            <!-- Client picker: one row per group, every client a small button.
                 Which clients are listed is this browser's own preference
                 (layoutStore.hiddenMcpClients), edited right here. -->
            <div class="border-b border-base-300 bg-base-200/30 px-3 py-2 space-y-1.5" data-testid="mcp-client-picker">
              <div
                v-for="group in visibleClientGroups"
                :key="group.title"
                class="flex flex-wrap items-center gap-1"
              >
                <span class="w-full sm:w-36 shrink-0 text-[11px] uppercase tracking-wide text-base-content/40">
                  {{ group.title }}
                </span>
                <button
                  v-for="client in group.clients"
                  :key="client.key"
                  type="button"
                  class="btn btn-xs"
                  :class="activeTab === client.key ? 'btn-primary' : 'btn-ghost text-base-content/60'"
                  :aria-pressed="activeTab === client.key"
                  @click="activeTab = client.key"
                >
                  {{ client.label }}
                </button>
              </div>
              <div class="flex items-center justify-between gap-2 pt-1 text-[11px] text-base-content/50">
                <span v-if="hiddenClientCount">{{ hiddenClientCount }} of {{ MCP_CLIENTS.length }} clients hidden</span>
                <span v-else>Showing all {{ MCP_CLIENTS.length }} clients</span>
                <button
                  type="button"
                  class="btn btn-ghost btn-xs"
                  :aria-expanded="chooseClientsOpen"
                  aria-controls="mcp-choose-clients"
                  @click="chooseClientsOpen = !chooseClientsOpen"
                >{{ chooseClientsOpen ? 'Done' : 'Choose clients…' }}</button>
              </div>
            </div>

            <!-- Which clients to list: a preference of this browser, so the
                 row above holds the two or three tools a person actually uses -->
            <div v-if="chooseClientsOpen" id="mcp-choose-clients" class="border-b border-base-300 bg-base-100 px-3 py-3 space-y-3" data-testid="mcp-choose-clients">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <p class="text-xs text-base-content/60">
                  Tick the clients you use. Hidden ones are only hidden from this list, in this browser; a client added in a later release shows by default.
                </p>
                <div class="join">
                  <button type="button" class="btn btn-xs join-item" @click="setAllClientsHidden(false)">Show all</button>
                  <button type="button" class="btn btn-xs join-item" @click="setAllClientsHidden(true)">Hide all</button>
                </div>
              </div>
              <div v-for="group in MCP_CLIENT_GROUPS" :key="group.title" class="space-y-1">
                <div class="flex items-center gap-2">
                  <span class="text-[11px] uppercase tracking-wide text-base-content/40">{{ group.title }}</span>
                  <button type="button" class="btn btn-ghost btn-xs h-5 min-h-0 px-1 text-[11px]" @click="setGroupHidden(group, false)">all</button>
                  <button type="button" class="btn btn-ghost btn-xs h-5 min-h-0 px-1 text-[11px]" @click="setGroupHidden(group, true)">none</button>
                </div>
                <div class="flex flex-wrap gap-x-4 gap-y-1">
                  <label v-for="client in group.clients" :key="client.key" class="label cursor-pointer gap-1.5 p-0">
                    <input
                      type="checkbox"
                      class="checkbox checkbox-xs"
                      :checked="!isClientHidden(client.key)"
                      :aria-label="`Show ${client.label}`"
                      @change="layoutStore.setMcpClientHidden(client.key, !$event.target.checked)"
                    />
                    <span class="label-text text-xs">{{ client.label }}</span>
                  </label>
                </div>
              </div>
            </div>

            <!-- The client's documented formats, and how much of one to take -->
            <div class="p-3 space-y-2">
              <div class="flex flex-wrap items-center gap-2" data-testid="mcp-export-formats">
                <div v-if="activeTabMeta.formats.length > 1" class="join" role="tablist" aria-label="Format">
                  <button
                    v-for="f in activeTabMeta.formats"
                    :key="f.key"
                    type="button"
                    role="tab"
                    class="btn btn-xs join-item"
                    :class="activeTabMeta.format === f.key ? 'btn-active' : ''"
                    :aria-selected="activeTabMeta.format === f.key"
                    @click="activeFormat = f.key"
                  >{{ f.label }}</button>
                </div>
                <label v-if="activeTabMeta.entryOnlySupported" class="label cursor-pointer gap-2 p-0">
                  <input id="mcp-export-entry-only" v-model="entryOnly" type="checkbox" class="toggle toggle-xs" />
                  <span class="label-text text-xs">Server entry only (to merge into an existing file)</span>
                </label>
              </div>
              <div class="flex items-center justify-between gap-2">
                <span class="text-xs text-base-content/50 truncate">{{ activeTabMeta.filename }}</span>
                <div class="flex items-center gap-1">
                  <a v-if="activeTabMeta.docs" :href="activeTabMeta.docs" target="_blank" rel="noopener noreferrer" class="btn btn-xs btn-ghost">Docs</a>
                  <button
                    class="btn btn-xs btn-ghost gap-1"
                    @click="copyText(activeTabMeta.content, activeTabMeta.label)"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
                    </svg>
                    Copy
                  </button>
                  <button
                    class="btn btn-xs btn-ghost gap-1"
                    @click="downloadText(activeTabMeta.content, downloadName(activeTabMeta))"
                  >
                    <svg class="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                    </svg>
                    Download
                  </button>
                </div>
              </div>
              <pre
                class="bg-neutral text-neutral-content rounded-lg p-3 text-xs font-mono overflow-x-auto whitespace-pre select-all leading-relaxed"
              >{{ activeTabMeta.content }}</pre>
              <p v-if="activeTabMeta.note" class="text-xs text-warning">{{ activeTabMeta.note }}</p>
              <p class="text-xs text-base-content/40">{{ activeTabMeta.hint }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Advanced / Global server defaults -->
      <div v-if="userStore.adminConsole" class="card bg-base-200">
        <div class="card-body p-0">
          <details class="group">
            <summary class="cursor-pointer px-5 py-4 text-sm font-medium text-base-content/60 hover:text-base-content select-none list-none flex items-center justify-between">
              <span>Advanced server defaults</span>
              <span class="text-xs text-base-content/40">Search behavior for MCP calls</span>
            </summary>
            <div class="px-5 pb-5 space-y-4 border-t border-base-300 pt-4">
              <p class="text-xs text-base-content/50">
                Defaults applied to MCP tool calls. Clients can override any of them per request
                via URL query params (e.g. <code class="font-mono">?collection_id=…&amp;top_k=…&amp;mode=…</code>).
              </p>
              <div class="grid gap-3 md:grid-cols-2">
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-collection"><span class="label-text font-medium">Default collection</span></label>
                  <select id="mcp-default-collection" v-model="mcpSettings.mcp_default_collection" class="select select-bordered w-full">
                    <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-mode"><span class="label-text font-medium">Default search mode</span></label>
                  <select id="mcp-default-mode" v-model="mcpSettings.mcp_mode" class="select select-bordered w-full">
                    <option value="auto">Automatic (recommended)</option>
                    <option value="hybrid">Meaning + keywords (fixed blend)</option>
                    <option value="semantic">Meaning only</option>
                    <option value="keyword">Keywords only</option>
                  </select>
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-default-topk"><span class="label-text font-medium">Default top K</span></label>
                  <input id="mcp-default-topk" v-model.number="mcpSettings.mcp_top_k" type="number" min="1" max="20" class="input input-bordered w-full" />
                </div>
                <div class="form-control">
                  <label class="label p-0 pb-1" for="mcp-excerpt-length"><span class="label-text font-medium">Default excerpt length</span></label>
                  <input id="mcp-excerpt-length" v-model.number="mcpSettings.mcp_max_source_length" type="number" min="100" max="2000" step="50" class="input input-bordered w-full" />
                </div>
              </div>
              <div v-if="mcpSettings.mcp_mode === 'hybrid'" class="form-control">
                <label class="label p-0 pb-1" for="mcp-semantic-weight">
                  <span class="label-text font-medium">Semantic weight</span>
                  <span class="label-text-alt">{{ mcpSettings.mcp_semantic_weight.toFixed(2) }}</span>
                </label>
                <input id="mcp-semantic-weight" v-model.number="mcpSettings.mcp_semantic_weight" type="range" min="0" max="1" step="0.05" class="range range-primary range-sm" :aria-valuetext="`${Math.round(mcpSettings.mcp_semantic_weight * 100)} percent semantic`" />
              </div>
              <label class="label cursor-pointer justify-start gap-3 p-0">
                <input v-model="mcpSettings.mcp_include_sources" type="checkbox" class="checkbox checkbox-sm checkbox-primary" />
                <span class="label-text">Include excerpts by default</span>
              </label>
              <div class="flex justify-end">
                <button class="btn btn-sm btn-primary" @click="saveMcpSettings" :disabled="mcpSaving || mcpLoading">
                  <span v-if="mcpSaving" class="loading loading-spinner loading-xs"></span>
                  Save server defaults
                </button>
              </div>
            </div>
          </details>
        </div>
      </div>

    </template>

    <!-- What this server exposes: read live from the server so the list is
         never stale. Each tool shows its title, first line of description,
         and whether it reads or writes. -->
    <details class="rounded-lg border border-base-300 px-4 py-3" data-testid="mcp-catalog">
      <summary class="cursor-pointer text-sm font-medium">
        What agents can do here
        <span v-if="catalog" class="font-normal text-base-content/50">· {{ catalog.tools.length }} tools · {{ catalog.resources.length }} resources · {{ catalog.prompts.length }} prompts</span>
      </summary>
      <div v-if="catalogError" class="mt-3 text-xs text-base-content/60">{{ catalogError }}</div>
      <div v-else-if="!catalog" class="mt-3 space-y-2">
        <div v-for="n in 4" :key="n" class="skeleton h-5 w-full rounded" aria-hidden="true"></div>
      </div>
      <div v-else class="mt-3 space-y-5">
        <section v-for="group in catalogGroups" :key="group.title">
          <h3 class="side-label text-base-content/50 mb-1">{{ group.title }}</h3>
          <ul class="divide-y divide-base-300/50">
            <li v-for="tool in group.tools" :key="tool.name" class="py-2 grid grid-cols-[minmax(0,14rem)_1fr] gap-x-4 gap-y-0.5 text-xs">
              <div class="min-w-0">
                <code class="font-mono text-[12px] text-base-content/90 break-all">{{ tool.name }}</code>
                <span v-if="!tool.read_only" class="ml-1.5 badge badge-xs badge-warning badge-outline align-middle">writes</span>
              </div>
              <p class="text-base-content/65 leading-relaxed">{{ tool.description }}</p>
            </li>
          </ul>
        </section>
        <section v-if="catalog.resources.length">
          <h3 class="side-label text-base-content/50 mb-1">Resources <span class="font-normal">(load as context)</span></h3>
          <ul class="divide-y divide-base-300/50">
            <li v-for="r in catalog.resources" :key="r.uri_template" class="py-1.5 grid grid-cols-[minmax(0,14rem)_1fr] gap-x-4 text-xs">
              <code class="font-mono text-[12px] text-base-content/90 break-all">{{ r.uri_template }}</code>
              <span class="text-base-content/65">{{ r.description || r.name }}</span>
            </li>
          </ul>
        </section>
        <section v-if="catalog.prompts.length">
          <h3 class="side-label text-base-content/50 mb-1">Prompts <span class="font-normal">(ready-made workflows)</span></h3>
          <ul class="divide-y divide-base-300/50">
            <li v-for="pr in catalog.prompts" :key="pr.name" class="py-1.5 grid grid-cols-[minmax(0,14rem)_1fr] gap-x-4 text-xs">
              <code class="font-mono text-[12px] text-base-content/90 break-all">{{ pr.name }}</code>
              <span class="text-base-content/65">{{ pr.description || pr.title }}</span>
            </li>
          </ul>
        </section>
        <p class="text-[11px] text-base-content/45">
          ChatGPT connectors call <code>search</code> and <code>fetch</code>; every other client sees the full set. Write tools only work for tokens that allow adding sources.
        </p>
      </div>
    </details>

    <div v-if="mcpError" class="alert alert-error py-2">
      <span>{{ mcpError }}</span>
    </div>
    <div v-if="mcpStatus" class="alert alert-success py-2">
      <span>{{ mcpStatus }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import { ShieldAlert } from 'lucide-vue-next'
import http from '../utils/http'
import { useUserStore } from '../stores/userStore'
import { useLayoutStore } from '../stores/layoutStore'
import { MCP_CLIENT_GROUPS, MCP_CLIENTS, buildClientConfig } from '../utils/mcpClients'
const userStore = useUserStore()
const layoutStore = useLayoutStore()

// MCP state
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpError = ref('')
const mcpStatus = ref('')
const mcpCollections = ref([])
// What the recipes are built from. Collections ticked (none = the agent
// chooses), how the credential is written, the transport, the server name,
// and which of the client's documented formats to show.
const exportCollectionIds = ref([])
const credentialMode = ref('token')
const envVarName = ref('CLIO_TOKEN')
const serverNameInput = ref('')
const transport = ref('http')
const activeTab = ref('claude')
const activeFormat = ref('')
const entryOnly = ref(false)

// Personal access tokens
const tokens = ref([])
const newTokenName = ref('')
const newTokenScope = ref([])
const newTokenCanWrite = ref(false)
const newTokenAllowlist = ref(false)
// null = never expires; otherwise days until the token stops working.
const newTokenExpiryDays = ref(90)
const expiryOptions = [
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 365, label: '1 year' },
  { value: null, label: 'Never' },
]
const newTokenPlaintext = ref('')
const tokenCreating = ref(false)
const exportTokenId = ref('')
const mcpSettings = ref({
  enable_mcp: true,
  mcp_default_collection: 'default',
  mcp_top_k: 5,
  mcp_mode: 'auto',
  mcp_semantic_weight: 0.7,
  mcp_include_sources: true,
  mcp_max_source_length: 500,
})

// OAuth on /mcp: read-only here. Whether connector clients can sign in is a
// deployment setting (IDENTITY_PROVIDER=oidc or MCP_OAUTH_ISSUER), so the tab
// only reports it and hands over the URL.
const oauth = ref({ enabled: false, resource: '', authorization_server: '', scope: '' })
const oauthIssuerHost = computed(() => {
  try {
    return new URL(oauth.value.authorization_server).host
  } catch {
    return oauth.value.authorization_server
  }
})

// Client configs are generated locally from the recipes in utils/mcpClients.js
// — the server URL plus a collection_id query param is all a client needs;
// server defaults cover the rest.
const sanitizeServerId = (value) =>
  (value.toLowerCase().replace(/[^a-z0-9-_]+/g, '-').replace(/^[-_]+|[-_]+$/g, '')) || 'clio'

const activeTokens = computed(() => tokens.value.filter((t) => !t.revoked_at))
const restrictedCollections = computed(() =>
  mcpCollections.value.filter((c) => c.sensitivity === 'restricted')
)
const collectionName = (cid) => mcpCollections.value.find((c) => c.id === cid)?.name || cid

// Timestamps from the app database are naive UTC; without a zone suffix the
// browser would read them as local time and misjudge expiry by the offset.
const parseUtc = (iso) => new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
const isExpired = (t) => Boolean(t.expires_at) && parseUtc(t.expires_at) <= new Date()

// Leaving allowlist mode drops the non-restricted picks: in grant mode only
// restricted collections mean anything in the scope.
watch(newTokenAllowlist, (on) => {
  if (!on) {
    const restricted = new Set(restrictedCollections.value.map((c) => c.id))
    newTokenScope.value = newTokenScope.value.filter((cid) => restricted.has(cid))
  }
})

const defaultServerName = computed(() => {
  const ids = exportCollectionIds.value
  return ids.length === 1 ? sanitizeServerId(`clio-${ids[0]}`) : 'clio'
})
const serverName = computed(() => sanitizeServerId(serverNameInput.value || defaultServerName.value))

// Bare /mcp is served directly (no redirect), which is the form every client
// documents; the legacy SSE transport lives beside it at /mcp/sse. A
// collection_id query parameter says where searches start.
const mcpBase = computed(() => `${window.location.origin}/mcp`)
const urlFor = (path, collectionId) =>
  collectionId ? `${window.location.origin}${path}?collection_id=${encodeURIComponent(collectionId)}` : `${window.location.origin}${path}`

// One server per ticked collection; a single unscoped server when none is.
const exportServers = computed(() => {
  const ids = exportCollectionIds.value
  if (!ids.length) return [{ id: serverName.value, collectionId: null, url: urlFor('/mcp'), sseUrl: urlFor('/mcp/sse') }]
  return ids.map((cid) => ({
    id: ids.length === 1 ? serverName.value : sanitizeServerId(`${serverName.value}-${cid}`),
    collectionId: cid,
    url: urlFor('/mcp', cid),
    sseUrl: urlFor('/mcp/sse', cid),
  }))
})

// The selected token's plaintext is only ever known right after creation.
// Once a page reload happens the server has only the hash, so snippets for
// an older token fall back to a placeholder the user fills in by hand.
const lastCreatedTokenId = ref('')
const selectedTokenPlaintext = computed(() => {
  if (newTokenPlaintext.value && exportTokenId.value === lastCreatedTokenId.value) {
    return newTokenPlaintext.value
  }
  return ''
})

const TOKEN_PLACEHOLDER = '<PASTE_YOUR_TOKEN — shown once, right after you generate it>'
const tokenForConfig = computed(() => selectedTokenPlaintext.value || TOKEN_PLACEHOLDER)

const exportContext = computed(() => ({
  servers: exportServers.value,
  transport: transport.value,
  credential: {
    mode: credentialMode.value,
    token: tokenForConfig.value,
    envVar: (envVarName.value || 'CLIO_TOKEN').replace(/[^A-Za-z0-9_]/g, '_'),
  },
  oauthEnabled: Boolean(oauth.value.enabled),
}))

// The client list, less the ones this browser chose to hide. The picker never
// goes empty: with everything hidden it shows the generic entry, so there is
// always a recipe on screen.
const chooseClientsOpen = ref(false)
const hiddenClientSet = computed(() => new Set(layoutStore.hiddenMcpClients))
const isClientHidden = (key) => hiddenClientSet.value.has(key)
const hiddenClientCount = computed(() => MCP_CLIENTS.filter((c) => isClientHidden(c.key)).length)
const visibleClientGroups = computed(() => {
  const groups = MCP_CLIENT_GROUPS
    .map((g) => ({ title: g.title, clients: g.clients.filter((c) => !isClientHidden(c.key)) }))
    .filter((g) => g.clients.length)
  if (groups.length) return groups
  const generic = MCP_CLIENTS.find((c) => c.key === 'generic') || MCP_CLIENTS[0]
  return [{ title: 'Any MCP client', clients: [generic] }]
})
const setAllClientsHidden = (hidden) => layoutStore.setHiddenMcpClients(hidden ? MCP_CLIENTS.map((c) => c.key) : [])
const setGroupHidden = (group, hidden) => {
  const keys = new Set(group.clients.map((c) => c.key))
  const rest = layoutStore.hiddenMcpClients.filter((k) => !keys.has(k))
  layoutStore.setHiddenMcpClients(hidden ? [...rest, ...keys] : rest)
}
// Hiding the selected client moves the selection to the first one still shown.
watch(visibleClientGroups, (groups) => {
  const shown = groups.flatMap((g) => g.clients.map((c) => c.key))
  if (!shown.includes(activeTab.value)) activeTab.value = shown[0]
}, { immediate: true })

const activeTabMeta = computed(() => buildClientConfig(activeTab.value, exportContext.value, {
  format: activeFormat.value,
  entryOnly: entryOnly.value,
}))
// A new client starts on its first documented format.
watch(activeTab, () => { activeFormat.value = '' })

// The facts a person copies one at a time: the URL(s), the header, the ids.
const connectionFacts = computed(() => {
  const facts = []
  const servers = exportServers.value
  if (servers.length === 1) {
    facts.push({ label: 'Server URL', value: servers[0].url })
    facts.push({ label: 'Legacy SSE URL', value: servers[0].sseUrl })
  } else {
    facts.push({ label: 'Server URL (unscoped)', value: mcpBase.value })
    for (const s of servers) facts.push({ label: `URL · ${collectionName(s.collectionId)}`, value: s.url })
  }
  if (credentialMode.value !== 'none') {
    facts.push({ label: 'Authorization header', value: `Authorization: Bearer ${tokenForConfig.value}` })
    if (selectedTokenPlaintext.value) facts.push({ label: 'Token', value: selectedTokenPlaintext.value })
  }
  if (exportCollectionIds.value.length) facts.push({ label: 'Collection id' + (exportCollectionIds.value.length > 1 ? 's' : ''), value: exportCollectionIds.value.join(', ') })
  facts.push({ label: 'Tool catalog', value: `${window.location.origin}/api/mcp/catalog` })
  return facts
})

// A download needs a real file name; "connector" or "terminal" is not one.
const downloadName = (meta) => {
  const base = meta.filename.split('/').pop()
  if (/\.[a-z0-9]+$/i.test(base)) return base
  const ext = { json: 'json', toml: 'toml', yaml: 'yaml', bash: 'sh', python: 'py', typescript: 'ts' }[meta.language] || 'txt'
  return `clio-${meta.key}.${ext}`
}

// Live tool catalog from the server. Grouped by what an agent is trying to
// do rather than by implementation, so a reader can scan it.
const catalog = ref(null)
const catalogError = ref('')
const CATALOG_GROUPS = [
  { title: 'Search and research', match: /^(search|fetch|search_|research_|find_)/ },
  { title: 'Read documents', match: /^(get_document|list_recent|get_collection|list_collections|health)/ },
  { title: 'Tables', match: /table/ },
  { title: 'Write, index and jobs', match: /^(write_|reindex|update_|list_index|get_index)/ },
]
const catalogGroups = computed(() => {
  if (!catalog.value) return []
  const seen = new Set()
  const groups = CATALOG_GROUPS.map(g => ({ title: g.title, tools: [] }))
  for (const tool of catalog.value.tools) {
    const idx = CATALOG_GROUPS.findIndex(g => g.match.test(tool.name))
    const target = idx === -1 ? groups[groups.length - 1] : groups[idx]
    target.tools.push(tool); seen.add(tool.name)
  }
  return groups.filter(g => g.tools.length)
})
const loadCatalog = async () => {
  try {
    const { data } = await http.get('/api/mcp/catalog')
    catalog.value = { tools: data.tools || [], resources: data.resources || [], prompts: data.prompts || [] }
  } catch (err) {
    catalogError.value = err?.message || 'Could not load the tool list.'
  }
}
onMounted(loadCatalog)

const loadMcpCollections = async () => {
  try {
    const response = await http.get('/api/collections')
    mcpCollections.value = response.data.collections || []
    const known = new Set(mcpCollections.value.map((c) => c.id))
    exportCollectionIds.value = exportCollectionIds.value.filter((cid) => known.has(cid))
    if (!exportCollectionIds.value.length && mcpCollections.value.length) {
      exportCollectionIds.value = [mcpCollections.value[0].id]
    }
  } catch {
    mcpCollections.value = [{ id: 'default', name: 'Default' }]
    exportCollectionIds.value = ['default']
  }
}

const loadMcpSettings = async () => {
  mcpLoading.value = true
  mcpError.value = ''
  try {
    const response = await http.get('/api/mcp/config')
    mcpSettings.value = { ...mcpSettings.value, ...response.data }
  } catch (error) {
    mcpError.value = error?.message || 'Failed to load MCP settings'
  } finally {
    mcpLoading.value = false
  }
}

// An open deployment (no password, no identity provider) accepts MCP
// clients with no credential at all, so the Connect copy must not claim a
// token is required there. /api/me reports the mode.
const openDeployment = ref(false)
const loadAuthMode = async () => {
  try {
    const { data } = await http.get('/api/me')
    openDeployment.value = data?.authenticated_via === 'open'
    if (openDeployment.value && !activeTokens.value.length) credentialMode.value = 'none'
  } catch { /* keep the stricter wording */ }
}

const loadOAuth = async () => {
  try {
    const response = await http.get('/api/mcp/oauth')
    const data = response?.data || {}
    if (data.enabled) oauth.value = { scope: '', ...data }
  } catch {
    // An older backend has no such endpoint; token-based setup still works.
  }
}

const formatDate = (iso) => {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

const loadTokens = async () => {
  try {
    const response = await http.get('/api/mcp/tokens')
    tokens.value = response.data.tokens || []
    if (!activeTokens.value.find((t) => t.id === exportTokenId.value)) {
      exportTokenId.value = activeTokens.value[0]?.id || ''
    }
  } catch (error) {
    mcpError.value = error?.message || 'Failed to load MCP tokens'
  }
}

const createToken = async () => {
  tokenCreating.value = true
  mcpError.value = ''
  try {
    if (newTokenAllowlist.value && !newTokenScope.value.length) {
      mcpError.value = 'Tick at least one collection for a limited token.'
      return
    }
    const response = await http.post('/api/mcp/tokens', {
      name: newTokenName.value,
      collection_scope: newTokenScope.value,
      can_write: newTokenCanWrite.value,
      expires_in_days: newTokenExpiryDays.value,
      allowlist: newTokenAllowlist.value,
    })
    newTokenPlaintext.value = response.data.token
    lastCreatedTokenId.value = response.data.id
    newTokenName.value = ''
    newTokenScope.value = []
    newTokenCanWrite.value = false
    newTokenAllowlist.value = false
    await loadTokens()
    exportTokenId.value = response.data.id
  } catch (error) {
    mcpError.value = error?.message || 'Failed to create MCP token'
  } finally {
    tokenCreating.value = false
  }
}

const revokeToken = async (token) => {
  mcpError.value = ''
  try {
    await http.delete(`/api/mcp/tokens/${token.id}`)
    if (lastCreatedTokenId.value === token.id) {
      newTokenPlaintext.value = ''
      lastCreatedTokenId.value = ''
    }
    await loadTokens()
    mcpStatus.value = `${token.name} revoked`
  } catch (error) {
    mcpError.value = error?.message || 'Failed to revoke token'
  }
}

const copyText = async (value, label) => {
  if (!value) return
  try {
    await navigator.clipboard.writeText(value)
    mcpStatus.value = `${label} copied`
    mcpError.value = ''
  } catch {
    mcpError.value = `Failed to copy ${label}`
  }
}

const downloadText = (value, filename) => {
  if (!value) return
  const blob = new Blob([value], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
  mcpStatus.value = `${filename} downloaded`
}

const saveMcpToggle = async () => {
  mcpError.value = ''
  mcpStatus.value = ''
  try {
    await http.post('/api/mcp/config', { enable_mcp: mcpSettings.value.enable_mcp })
  } catch (error) {
    mcpError.value = error?.message || 'Failed to update MCP status'
    mcpSettings.value.enable_mcp = !mcpSettings.value.enable_mcp
  }
}

const saveMcpSettings = async () => {
  mcpSaving.value = true
  mcpError.value = ''
  mcpStatus.value = ''

  try {
    const payload = {
      ...mcpSettings.value,
      mcp_top_k: Number(mcpSettings.value.mcp_top_k) || 5,
      mcp_semantic_weight: Number(mcpSettings.value.mcp_semantic_weight) || 0.7,
      mcp_max_source_length: Number(mcpSettings.value.mcp_max_source_length) || 500,
    }

    const response = await http.post('/api/mcp/config', payload)
    if (!response.data.success) {
      mcpError.value = response.data.errors?.join(', ') || 'Failed to save MCP settings'
      return
    }

    mcpStatus.value = 'MCP settings saved'
    await loadMcpSettings()
  } catch (error) {
    mcpError.value = error?.message || 'Failed to save MCP settings'
  } finally {
    mcpSaving.value = false
  }
}

onMounted(() => {
  loadMcpCollections()
  loadMcpSettings()
  loadOAuth()
  loadAuthMode()
  loadTokens()
})
</script>
