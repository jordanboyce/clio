<template>
  <div class="space-y-6">
    <header>
      <h1 class="text-xl font-semibold">Connect your AI tools</h1>
      <p class="mt-2 text-sm text-base-content/60 max-w-2xl">
        <template v-if="oauth.enabled">
          Search your sources from Claude Desktop, claude.ai, ChatGPT, Claude Code, Codex, AnythingLLM, or VS Code using MCP.
        </template>
        <template v-else>
          Search your sources from AnythingLLM, Claude Code, Codex, or VS Code using MCP.
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
              Pick a default collection, then copy the configuration into your client.
              This selects where searches start; it does not restrict the token to that collection.
            </p>
          </div>

          <div v-if="activeTokens.length" class="form-control max-w-xs">
            <label class="label p-0 pb-1" for="mcp-export-token"><span class="label-text font-medium">Token</span></label>
            <select id="mcp-export-token" v-model="exportTokenId" class="select select-bordered select-sm w-full">
              <option v-for="t in activeTokens" :key="t.id" :value="t.id">{{ t.name }}</option>
            </select>
          </div>
          <div v-else class="alert py-2">
            <span class="text-sm">Generate a token above first — the snippets below need one to authenticate.</span>
          </div>

          <div class="form-control max-w-xs">
            <label class="label p-0 pb-1" for="mcp-export-collection"><span class="label-text font-medium">Collection</span></label>
            <select id="mcp-export-collection" v-model="exportCollectionId" class="select select-bordered select-sm w-full">
              <option v-for="c in mcpCollections" :key="c.id" :value="c.id">{{ c.name }}</option>
            </select>
          </div>

          <div class="rounded-lg border border-base-300 bg-base-100 overflow-hidden">
            <!-- Tab bar -->
            <div class="flex flex-wrap border-b border-base-300 bg-base-200/30">
              <button
                v-for="tab in configTabs"
                :key="tab.key"
                class="px-4 py-2 text-xs font-medium transition-colors relative"
                :class="activeTab === tab.key
                  ? 'text-primary border-b-2 border-primary -mb-px bg-base-100/50'
                  : 'text-base-content/50 hover:text-base-content/80'"
                @click="activeTab = tab.key"
              >
                {{ tab.label }}
              </button>
            </div>

            <!-- Config content -->
            <div class="p-3 space-y-2">
              <div class="flex items-center justify-between">
                <span class="text-xs text-base-content/50">{{ activeTabMeta.filename }}</span>
                <div class="flex items-center gap-1">
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
                    @click="downloadText(activeTabMeta.content, activeTabMeta.filename)"
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
                    <option value="semantic">Semantic</option>
                    <option value="keyword">Keyword</option>
                    <option value="hybrid">Hybrid</option>
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
const userStore = useUserStore()

// MCP state
const mcpLoading = ref(false)
const mcpSaving = ref(false)
const mcpError = ref('')
const mcpStatus = ref('')
const mcpCollections = ref([])
const exportCollectionId = ref('default')
const activeTab = ref('claude')

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
  mcp_mode: 'semantic',
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

const configTabs = [
  { key: 'claude', label: 'Claude Code' },
  { key: 'desktop', label: 'Claude Desktop' },
  { key: 'cursor', label: 'Cursor' },
  { key: 'codex', label: 'Codex' },
  { key: 'copilot', label: 'VS Code' },
  { key: 'windsurf', label: 'Windsurf' },
  { key: 'anythingllm', label: 'AnythingLLM' },
]

// Client configs are generated locally — the server URL plus a collection_id
// query param is all a client needs; server defaults cover the rest.
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

const serverId = computed(() => sanitizeServerId(`clio-${exportCollectionId.value}`))
const serverUrl = computed(() =>
  `${window.location.origin}/mcp/?collection_id=${encodeURIComponent(exportCollectionId.value)}`
)

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

const authHeaderValue = computed(() =>
  selectedTokenPlaintext.value || '<PASTE_YOUR_TOKEN — shown once, right after you generate it>'
)

const activeTabMeta = computed(() => {
  const headers = { 'Authorization': `Bearer ${authHeaderValue.value}` }
  const httpEntry = { type: 'http', url: serverUrl.value, headers }
  const tabs = {
    anythingllm: {
      label: 'AnythingLLM config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: { ...httpEntry, type: 'streamable' } } }, null, 2),
      filename: 'anythingllm_mcp_servers.json',
      hint: 'Merge this entry into anythingllm_mcp_servers.json in your AnythingLLM storage/plugins directory, then reload MCP servers in AnythingLLM. The URL must be reachable from AnythingLLM; localhost inside Docker refers to that container. Keep the credential private.',
    },
    claude: {
      label: 'Claude Code config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: httpEntry } }, null, 2),
      filename: '.mcp.json',
      hint: 'Save as .mcp.json in your project root. This configuration contains a credential; keep it out of version control.',
    },
    codex: {
      label: 'Codex config',
      content: `[mcp_servers.${serverId.value}]\nurl = "${serverUrl.value}"\nhttp_headers = { "Authorization" = "Bearer ${authHeaderValue.value}" }\n`,
      filename: 'config.toml',
      hint: 'Merge this entry into your existing Codex config.toml. Keep the credential private.',
    },
    copilot: {
      label: 'VS Code (GitHub Copilot) config',
      content: JSON.stringify({ servers: { [serverId.value]: httpEntry } }, null, 2),
      filename: '.vscode/mcp.json',
      hint: 'Place this file at .vscode/mcp.json in your project, or merge into your VS Code settings.',
    },
    cursor: {
      label: 'Cursor config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: { url: serverUrl.value, headers } } }, null, 2),
      filename: '.cursor/mcp.json',
      hint: 'Save as .cursor/mcp.json in your project (or ~/.cursor/mcp.json for every project), then enable the server under Cursor Settings → MCP. Keep the credential private.',
    },
    windsurf: {
      label: 'Windsurf config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: { serverUrl: serverUrl.value, headers } } }, null, 2),
      filename: '~/.codeium/windsurf/mcp_config.json',
      hint: 'Merge this entry into ~/.codeium/windsurf/mcp_config.json and refresh the MCP list in Windsurf. Keep the credential private.',
    },
    desktop: {
      label: 'Claude Desktop config',
      content: JSON.stringify({ mcpServers: { [serverId.value]: {
        command: 'npx',
        args: ['-y', 'mcp-remote', serverUrl.value, '--header', `Authorization: Bearer ${authHeaderValue.value}`],
      } } }, null, 2),
      filename: 'claude_desktop_config.json',
      hint: oauth.value.enabled
        ? 'With sign-in enabled you can skip this: add the server URL as a custom connector under Settings → Connectors and sign in from the client. This token-based form needs Node.js for the mcp-remote bridge.'
        : 'Claude Desktop speaks stdio to local servers, so mcp-remote (needs Node.js) bridges it to this URL. Merge into claude_desktop_config.json (Settings → Developer → Edit Config) and restart Claude Desktop.',
    },
  }
  return tabs[activeTab.value]
})

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
    if (mcpCollections.value.length > 0 && !mcpCollections.value.find(c => c.id === exportCollectionId.value)) {
      exportCollectionId.value = mcpCollections.value[0].id
    }
  } catch {
    mcpCollections.value = [{ id: 'default', name: 'Default' }]
  }
}

const loadMcpSettings = async () => {
  mcpLoading.value = true
  mcpError.value = ''
  try {
    const response = await http.get('/api/mcp/config')
    mcpSettings.value = { ...mcpSettings.value, ...response.data }
  } catch (error) {
    mcpError.value = error.response?.data?.detail || 'Failed to load MCP settings'
  } finally {
    mcpLoading.value = false
  }
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
    mcpError.value = error.response?.data?.detail || 'Failed to load MCP tokens'
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
    mcpError.value = error.response?.data?.detail || 'Failed to create MCP token'
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
    mcpError.value = error.response?.data?.detail || 'Failed to revoke token'
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
    mcpError.value = error.response?.data?.detail || 'Failed to update MCP status'
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
    mcpError.value = error.response?.data?.detail || 'Failed to save MCP settings'
  } finally {
    mcpSaving.value = false
  }
}

onMounted(() => {
  loadMcpCollections()
  loadMcpSettings()
  loadOAuth()
  loadTokens()
})
</script>
