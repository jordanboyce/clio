// Connection recipes for every MCP client the Connect tab knows about.
//
// The server side is identical for all of them (standard MCP streamable HTTP
// at /mcp, plus the legacy HTTP+SSE shape at /mcp/sse for clients that
// predate streamable HTTP), so this file is only about spelling: how each
// client says "this server, this credential" in its own configuration
// language, and every documented way of saying it (a config file, a CLI
// command, an install link).
//
// Nothing here is all-or-nothing. A recipe is built from a context the
// Connect tab assembles from the person's choices:
//
//   ctx.servers      one entry per chosen collection ([{ id, collectionId,
//                    url, sseUrl }]); a single entry with collectionId null
//                    when the agent should pick collections itself
//   ctx.transport    'http' (streamable HTTP, the standard) or 'sse' (legacy)
//   ctx.credential   { mode: 'token' | 'env' | 'none', token, envVar }
//                    token: the plaintext (or a placeholder) goes in the file
//                    env:   the file references an environment variable, in
//                           the client's own syntax; clients without one fall
//                           back to the token and say so
//                    none:  no Authorization header (open deployment, or the
//                           client signs in with OAuth)
//   ctx.oauthEnabled whether the deployment offers OAuth sign-in on /mcp
//
// and rendered either as the whole file or as just the server entry to merge
// into a file that already has other servers. Entries are generated locally
// in the browser; the server never sees the snippet or the token after it
// was minted.
//
// Keep each recipe a faithful copy of the client's documented shape; a
// snippet that looks plausible but is wrong costs the person an afternoon.
// Where a client documents no way to send a header, say so rather than
// inventing a key, and offer the mcp-remote bridge (a documented stdio
// shim) instead.

const json = (value) => JSON.stringify(value, null, 2)

// A JSON object's members without the enclosing braces, re-indented to the
// top level: what you paste into an existing `"mcpServers": { ... }`.
const members = (obj) => {
  const text = json(obj)
  return text.slice(1, -1).replace(/^\n/, '').replace(/\n$/, '').split('\n').map((l) => l.replace(/^ {2}/, '')).join('\n')
}

const tomlString = (s) => JSON.stringify(String(s))

// ── Credentials ─────────────────────────────────────────────────────────

/**
 * The Authorization header value for a client, or null when none is sent.
 * `envSyntax` is how the client spells an environment-variable reference in
 * its config; a client without one gets the token itself and a note.
 */
export function bearerFor(ctx, envSyntax) {
  const cred = ctx.credential || { mode: 'token', token: '' }
  if (cred.mode === 'none') return { value: null, note: '' }
  if (cred.mode === 'env') {
    const name = cred.envVar || 'CLIO_TOKEN'
    if (envSyntax) return { value: `Bearer ${envSyntax(name)}`, note: '' }
    return {
      value: `Bearer ${cred.token}`,
      note: `This client documents no variable substitution for header values, so the token itself goes in the file (set ${name} elsewhere if a script writes it).`,
    }
  }
  return { value: `Bearer ${cred.token}`, note: '' }
}

const envName = (ctx) => (ctx.credential && ctx.credential.envVar) || 'CLIO_TOKEN'
const envMode = (ctx) => (ctx.credential || {}).mode === 'env'
const noCred = (ctx) => (ctx.credential || {}).mode === 'none'
const rawToken = (ctx) => (ctx.credential || {}).token || ''

// For shell commands: in env mode the shell expands $VAR when the command
// runs; otherwise the token is quoted literally.
const shellBearer = (ctx) => (envMode(ctx) ? `Bearer $${envName(ctx)}` : `Bearer ${rawToken(ctx)}`)

// For code samples: the expression that yields the token.
const pyToken = (ctx) => (envMode(ctx) ? `os.environ["${envName(ctx)}"]` : JSON.stringify(rawToken(ctx)))
const pyBearer = (ctx) => (envMode(ctx) ? `f"Bearer {os.environ['${envName(ctx)}']}"` : JSON.stringify(`Bearer ${rawToken(ctx)}`))
const pyImportOs = (ctx) => (envMode(ctx) ? 'import os\n' : '')
const tsToken = (ctx) => (envMode(ctx) ? `process.env.${envName(ctx)}` : JSON.stringify(rawToken(ctx)))
const tsBearer = (ctx) => (envMode(ctx) ? `\`Bearer \${process.env.${envName(ctx)}}\`` : JSON.stringify(`Bearer ${rawToken(ctx)}`))

const endpoint = (ctx, server) => (ctx.transport === 'sse' ? server.sseUrl : server.url)

const withHeaders = (obj, bearer) => (bearer ? { ...obj, headers: { Authorization: bearer } } : obj)

// ── Shared format builders ──────────────────────────────────────────────

/**
 * A JSON config file whose servers live under `fileKey`. `entry(ctx, server,
 * bearer)` returns one server's object. `extra` adds top-level siblings
 * (a $schema, VS Code's inputs).
 */
function jsonFile({ key = 'file', label, filename, fileKey, entry, extra, envSyntax, transports = ['http'] }) {
  return {
    key, label: label || filename, filename, language: 'json', entryOnly: true, transports,
    render(ctx, { entryOnly } = {}) {
      const bearer = bearerFor(ctx, envSyntax)
      const servers = Object.fromEntries(ctx.servers.map((s) => [s.id, entry(ctx, s, bearer.value)]))
      const body = entryOnly ? members(servers) : json({ ...(extra ? extra(ctx) : {}), [fileKey]: servers })
      return { content: body, note: bearer.note }
    },
  }
}

/** A text config (TOML, YAML, crushrc): one fragment per server, joined. */
function textFile({ key = 'file', label, filename, language, entry, wrap, envSyntax, transports = ['http'] }) {
  return {
    key, label: label || filename, filename, language, entryOnly: Boolean(wrap), transports,
    render(ctx, { entryOnly } = {}) {
      const bearer = bearerFor(ctx, envSyntax)
      const fragments = ctx.servers.map((s) => entry(ctx, s, bearer.value))
      const content = wrap && !entryOnly ? wrap(fragments, ctx) : fragments.join('\n')
      return { content, note: bearer.note }
    },
  }
}

/** A shell command per server. */
function cli({ key = 'cli', label = 'Command line', filename = 'terminal', line, preamble, transports = ['http'] }) {
  return {
    key, label, filename, language: 'bash', entryOnly: false, transports,
    render(ctx) {
      const lines = ctx.servers.map((s) => line(ctx, s))
      const pre = preamble ? preamble(ctx) : ''
      return { content: (pre ? pre + '\n' : '') + lines.join('\n'), note: '' }
    },
  }
}

/** Free text (connector dialogs, code samples). */
function text({ key, label, filename, language = 'text', render, transports = ['http'] }) {
  return {
    key, label, filename, language, entryOnly: false, transports,
    render(ctx) {
      const out = render(ctx)
      return typeof out === 'string' ? { content: out, note: '' } : out
    },
  }
}

// mcp-remote: the documented stdio bridge for clients that only launch
// local servers. The header goes through an environment variable, which is
// the form its README gives (some clients mangle spaces in args).
const mcpRemoteEntry = (ctx, server) => {
  const url = endpoint(ctx, server)
  if (noCred(ctx)) return { command: 'npx', args: ['-y', 'mcp-remote', url] }
  const env = envMode(ctx) ? `Bearer \${${envName(ctx)}}` : `Bearer ${rawToken(ctx)}`
  return { command: 'npx', args: ['-y', 'mcp-remote', url, '--header', 'Authorization:${AUTH_HEADER}'], env: { AUTH_HEADER: env } }
}

const dollarBrace = (name) => `\${${name}}`
const envColon = (name) => `\${env:${name}}`

// ── Clients ─────────────────────────────────────────────────────────────

export const MCP_CLIENT_GROUPS = [
  {
    title: 'Coding agents and IDEs',
    clients: [
      {
        key: 'claude',
        label: 'Claude Code',
        docs: 'https://code.claude.com/docs/en/mcp',
        formats: [
          jsonFile({
            filename: '.mcp.json', fileKey: 'mcpServers', envSyntax: dollarBrace, transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ type: ctx.transport === 'sse' ? 'sse' : 'http', url: endpoint(ctx, s) }, bearer),
          }),
          cli({
            transports: ['http', 'sse'],
            line: (ctx, s) => {
              const header = noCred(ctx) ? '' : ` \\\n  --header "Authorization: ${shellBearer(ctx)}"`
              return `claude mcp add --transport ${ctx.transport === 'sse' ? 'sse' : 'http'} --scope user ${s.id} ${endpoint(ctx, s)}${header}`
            },
          }),
        ],
        hint: '.mcp.json in the project root is shared with the team (keep tokens out of it: reference a variable, ${CLIO_TOKEN}, and set it in your shell). The command stores the server for you alone with --scope user, or for this project with --scope project. An entry with a url must carry a type, or Claude Code reads it as a stdio server and skips it.',
      },
      {
        key: 'codex',
        label: 'Codex',
        docs: 'https://developers.openai.com/codex/mcp',
        formats: [
          textFile({
            filename: '~/.codex/config.toml', language: 'toml',
            entry: (ctx, s) => {
              let t = `[mcp_servers.${s.id}]\nurl = ${tomlString(s.url)}\n`
              if (envMode(ctx)) t += `bearer_token_env_var = ${tomlString(envName(ctx))}\n`
              else if (!noCred(ctx)) t += `http_headers = { "Authorization" = ${tomlString(`Bearer ${rawToken(ctx)}`)} }\n`
              return t
            },
            wrap: (fragments) => fragments.join('\n'),
          }),
          cli({
            preamble: (ctx) => (noCred(ctx) ? '' : envMode(ctx) ? `# ${envName(ctx)} must be set in the environment Codex runs in` : `export ${envName(ctx)}=${JSON.stringify(rawToken(ctx))}`),
            line: (ctx, s) => `codex mcp add ${s.id} --url ${s.url}${noCred(ctx) ? '' : ` --bearer-token-env-var ${envName(ctx)}`}`,
          }),
        ],
        hint: 'Merge the table into ~/.codex/config.toml. bearer_token_env_var names a variable holding the token and keeps it out of the file; http_headers sends static values. The CLI has no flag for arbitrary headers, so it always goes through the variable. Codex speaks streamable HTTP only.',
      },
      {
        key: 'pi',
        label: 'Pi',
        docs: 'https://pi.dev',
        formats: [
          jsonFile({
            filename: '~/.pi/agent/mcp.json', fileKey: 'mcpServers', envSyntax: dollarBrace,
            entry: (ctx, s, bearer) => withHeaders({ type: 'streamable-http', url: s.url }, bearer),
          }),
          cli({
            preamble: (ctx) => (envMode(ctx) || noCred(ctx) ? '' : `export CLIO_TOKEN=${JSON.stringify(rawToken(ctx))}`),
            line: (ctx, s) => `pi mcp add ${s.id} --url ${s.url}${noCred(ctx) ? '' : ` --bearer-token-env-var ${envMode(ctx) ? envName(ctx) : 'CLIO_TOKEN'}`}`,
          }),
        ],
        hint: 'Merge under mcpServers in ~/.pi/agent/mcp.json (or .pi/mcp.json for one project, read once the project is trusted). Headers accept ${VAR}. Pi speaks streamable HTTP only.',
      },
      {
        key: 'gemini',
        label: 'Gemini CLI',
        docs: 'https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md',
        formats: [
          jsonFile({
            filename: '~/.gemini/settings.json', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders(ctx.transport === 'sse' ? { url: s.sseUrl, timeout: 60000 } : { httpUrl: s.url, timeout: 60000 }, bearer),
          }),
          cli({
            transports: ['http', 'sse'],
            line: (ctx, s) => `gemini mcp add --transport ${ctx.transport} --scope user${noCred(ctx) ? '' : ` --header "Authorization: ${shellBearer(ctx)}"`} ${s.id} ${endpoint(ctx, s)}`,
          }),
        ],
        hint: 'Merge under mcpServers in ~/.gemini/settings.json (user) or .gemini/settings.json (project). httpUrl selects streamable HTTP; url selects the legacy SSE transport. Gemini CLI expands ${VAR} inside the env block, not inside headers, so a token in the file is written as is.',
      },
      {
        key: 'qwen',
        label: 'Qwen Code',
        docs: 'https://github.com/QwenLM/qwen-code/blob/main/docs/users/features/mcp.md',
        formats: [
          jsonFile({
            filename: '~/.qwen/settings.json', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders(ctx.transport === 'sse' ? { url: s.sseUrl } : { httpUrl: s.url }, bearer),
          }),
          cli({
            transports: ['http', 'sse'],
            line: (ctx, s) => `qwen mcp add --transport ${ctx.transport} ${s.id} ${endpoint(ctx, s)}${noCred(ctx) ? '' : ` --header "Authorization: ${shellBearer(ctx)}"`}`,
          }),
        ],
        hint: 'Same shape as Gemini CLI: merge under mcpServers in ~/.qwen/settings.json (user scope, the default) or .qwen/settings.json (project). httpUrl is streamable HTTP; url is the legacy SSE transport.',
      },
      {
        key: 'cursor',
        label: 'Cursor',
        docs: 'https://cursor.com/docs/context/mcp',
        formats: [
          jsonFile({
            filename: '.cursor/mcp.json', fileKey: 'mcpServers', envSyntax: envColon, transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ url: endpoint(ctx, s) }, bearer),
          }),
          text({
            key: 'link', label: 'Install link', filename: 'cursor-install-link.txt', transports: ['http', 'sse'],
            render: (ctx) => {
              const bearer = bearerFor(ctx, envColon)
              const links = ctx.servers.map((s) => {
                const config = json(withHeaders({ url: endpoint(ctx, s) }, bearer.value))
                return `cursor://anysphere.cursor-deeplink/mcp/install?name=${encodeURIComponent(s.id)}&config=${encodeURIComponent(toBase64(config))}`
              })
              return { content: links.join('\n'), note: bearer.note }
            },
          }),
        ],
        hint: 'Save as .cursor/mcp.json in the project (or ~/.cursor/mcp.json for every project), then enable the server under Cursor Settings → Tools & MCP. Cursor detects the transport from the URL. ${env:VAR} inside headers reads your environment. The install link opens Cursor with the same entry pre-filled (config is the entry, base64-encoded).',
      },
      {
        key: 'copilot',
        label: 'VS Code',
        docs: 'https://code.visualstudio.com/docs/copilot/customization/mcp-servers',
        formats: [
          jsonFile({
            filename: '.vscode/mcp.json', fileKey: 'servers', envSyntax: envColon, transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ type: ctx.transport === 'sse' ? 'sse' : 'http', url: endpoint(ctx, s) }, bearer),
          }),
          jsonFile({
            key: 'prompt', label: 'mcp.json, prompting for the token', filename: '.vscode/mcp.json', fileKey: 'servers', transports: ['http', 'sse'],
            extra: () => ({ inputs: [{ type: 'promptString', id: 'clio-token', description: 'Clio personal access token', password: true }] }),
            entry: (ctx, s) => ({ type: ctx.transport === 'sse' ? 'sse' : 'http', url: endpoint(ctx, s), headers: { Authorization: 'Bearer ${input:clio-token}' } }),
          }),
          cli({
            transports: ['http', 'sse'],
            line: (ctx, s) => {
              const bearer = bearerFor(ctx, envColon).value
              return `code --add-mcp '${JSON.stringify(withHeaders({ name: s.id, type: ctx.transport === 'sse' ? 'sse' : 'http', url: endpoint(ctx, s) }, bearer))}'`
            },
          }),
        ],
        hint: 'Place at .vscode/mcp.json in the project, or run "MCP: Open User Configuration" from the command palette for the user-wide file. The prompting variant asks for the token once (masked) and stores it in the secret store, so nothing sensitive sits in the file; ${env:VAR} reads your environment instead. GitHub Copilot in VS Code reads these servers.',
      },
      {
        key: 'copilot-cli',
        label: 'Copilot CLI',
        docs: 'https://docs.github.com/copilot/how-tos/copilot-cli',
        formats: [
          jsonFile({
            filename: '~/.copilot/mcp-config.json', fileKey: 'mcpServers',
            entry: (ctx, s, bearer) => ({ ...withHeaders({ type: 'http', url: s.url }, bearer), tools: ['*'] }),
          }),
        ],
        hint: 'Merge under mcpServers in ~/.copilot/mcp-config.json (the CLI\'s own file, not VS Code\'s mcp.json), or run /mcp add inside Copilot CLI and paste the URL and header there.',
      },
      {
        key: 'windsurf',
        label: 'Windsurf',
        docs: 'https://docs.windsurf.com/windsurf/cascade/mcp',
        formats: [
          jsonFile({
            filename: '~/.codeium/windsurf/mcp_config.json', fileKey: 'mcpServers', envSyntax: envColon, transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ serverUrl: endpoint(ctx, s) }, bearer),
          }),
        ],
        hint: 'Merge this entry into ~/.codeium/windsurf/mcp_config.json and refresh the MCP list in Windsurf. ${env:VAR} is replaced from your environment in serverUrl and headers (an unset variable becomes an empty string, so set it before starting Windsurf).',
      },
      {
        key: 'zed',
        label: 'Zed',
        docs: 'https://zed.dev/docs/ai/mcp',
        formats: [
          jsonFile({
            filename: '~/.config/zed/settings.json', fileKey: 'context_servers',
            entry: (ctx, s, bearer) => withHeaders({ url: s.url }, bearer),
          }),
        ],
        hint: 'Merge under context_servers (Zed\'s name for MCP servers) in your Zed settings.json. Leave headers out to let Zed sign in with OAuth instead, when the deployment offers it.',
      },
      {
        key: 'cline',
        label: 'Cline',
        docs: 'https://docs.cline.bot/mcp/adding-and-configuring-servers',
        formats: [
          jsonFile({
            filename: 'cline_mcp_settings.json', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => ({ ...withHeaders({ type: ctx.transport === 'sse' ? 'sse' : 'streamableHttp', url: endpoint(ctx, s) }, bearer), disabled: false }),
          }),
        ],
        hint: 'Cline → MCP Servers → Remote Servers → Edit Configuration opens cline_mcp_settings.json; merge this under mcpServers. The type is streamableHttp; "sse" is the legacy transport.',
      },
      {
        key: 'roo',
        label: 'Roo Code',
        docs: 'https://docs.roocode.com/features/mcp/using-mcp-in-roo',
        formats: [
          jsonFile({
            filename: '.roo/mcp.json', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ type: ctx.transport === 'sse' ? 'sse' : 'streamable-http', url: endpoint(ctx, s) }, bearer),
          }),
        ],
        hint: 'Save as .roo/mcp.json in the project, or merge into the global mcp_settings.json from Roo Code\'s MCP panel. Note the hyphenated type.',
      },
      {
        key: 'kiro',
        label: 'Kiro',
        docs: 'https://kiro.dev/docs/mcp/configuration/',
        formats: [
          jsonFile({
            filename: '.kiro/settings/mcp.json', fileKey: 'mcpServers',
            entry: (ctx, s, bearer) => ({ ...withHeaders({ url: s.url }, bearer), disabled: false }),
          }),
        ],
        hint: 'Save as .kiro/settings/mcp.json in the workspace, or ~/.kiro/settings/mcp.json for every workspace. A url entry is a remote server; without headers Kiro tries OAuth.',
      },
      {
        key: 'opencode',
        label: 'OpenCode',
        docs: 'https://opencode.ai/docs/mcp-servers/',
        formats: [
          jsonFile({
            filename: 'opencode.json', fileKey: 'mcp', envSyntax: (n) => `{env:${n}}`,
            extra: () => ({ $schema: 'https://opencode.ai/config.json' }),
            entry: (ctx, s, bearer) => ({ ...withHeaders({ type: 'remote', url: s.url, enabled: true }, bearer) }),
          }),
        ],
        hint: 'Merge under mcp in opencode.json (project) or ~/.config/opencode/opencode.json (global). {env:VAR} substitutes from your environment.',
      },
      {
        key: 'amp',
        label: 'Amp',
        docs: 'https://ampcode.com/manual#mcp',
        formats: [
          jsonFile({
            filename: '~/.config/amp/settings.json', fileKey: 'amp.mcpServers',
            entry: (ctx, s, bearer) => withHeaders({ url: s.url }, bearer),
          }),
          cli({ line: (ctx, s) => `amp mcp add ${s.id} ${s.url}` }),
        ],
        hint: 'Merge "amp.mcpServers" into ~/.config/amp/settings.json (every project) or .amp/settings.json (this workspace; those need approval the first time), or into VS Code settings.json when Amp runs there. The command registers the URL; add the Authorization header in the settings file afterwards.',
      },
      {
        key: 'droid',
        label: 'Factory Droid',
        docs: 'https://docs.factory.ai/cli/configuration/mcp',
        formats: [
          jsonFile({
            filename: '~/.factory/mcp.json', fileKey: 'mcpServers',
            entry: (ctx, s, bearer) => withHeaders({ type: 'http', url: s.url }, bearer),
          }),
          cli({ line: (ctx, s) => `droid mcp add ${s.id} ${s.url} --type http${noCred(ctx) ? '' : ` --header "Authorization: ${shellBearer(ctx)}"`}` }),
        ],
        hint: 'User-wide in ~/.factory/mcp.json, or .factory/mcp.json in a project. The command writes the same entry; /mcp inside Droid shows the servers and finishes an OAuth sign-in when one is offered.',
      },
      {
        key: 'crush',
        label: 'Crush',
        docs: 'https://github.com/charmbracelet/crush#mcps',
        formats: [
          textFile({
            key: 'file', label: '.crushrc', filename: '.crushrc', language: 'bash', envSyntax: (n) => `$${n}`,
            entry: (ctx, s) => `mcp add ${s.id} --type http --url ${s.url}${noCred(ctx) ? '' : ` --header Authorization ${JSON.stringify(shellBearer(ctx))}`}`,
          }),
          jsonFile({
            key: 'json', label: 'crush.json (older format)', filename: 'crush.json', fileKey: 'mcp', envSyntax: (n) => `$${n}`,
            entry: (ctx, s, bearer) => withHeaders({ type: 'http', url: s.url }, bearer),
          }),
        ],
        hint: 'Add the line to .crushrc in the project or ~/.config/crush/crushrc for every project. Values take shell expansion, so $CLIO_TOKEN is read from your environment when Crush starts. The JSON form still works but Crush marks it deprecated.',
      },
      {
        key: 'vibe',
        label: 'Mistral Vibe',
        docs: 'https://docs.mistral.ai/vibe/code/cli/mcp-servers',
        formats: [
          textFile({
            filename: '~/.vibe/config.toml', language: 'toml',
            entry: (ctx, s) => {
              let t = `[[mcp_servers]]\nname = ${tomlString(s.id)}\ntransport = "streamable-http"\nurl = ${tomlString(s.url)}\n`
              if (!noCred(ctx)) {
                t += `\n[mcp_servers.auth]\ntype = "static"\n`
                t += envMode(ctx)
                  ? `api_key_env = ${tomlString(envName(ctx))}\napi_key_header = "Authorization"\napi_key_format = "Bearer {token}"\n`
                  : `headers = { "Authorization" = ${tomlString(`Bearer ${rawToken(ctx)}`)} }\n`
              }
              return t
            },
            wrap: (fragments) => fragments.join('\n'),
          }),
          cli({
            preamble: (ctx) => (envMode(ctx) || noCred(ctx) ? '' : `export CLIO_TOKEN=${JSON.stringify(rawToken(ctx))}`),
            line: (ctx, s) => `vibe mcp add ${s.id} --url ${s.url} --transport streamable-http${noCred(ctx) ? '' : ` --api-key-env ${envMode(ctx) ? envName(ctx) : 'CLIO_TOKEN'} --api-key-header Authorization --api-key-format "Bearer {token}"`}`,
          }),
        ],
        hint: 'Vibe reads ./.vibe/config.toml, then ~/.vibe/config.toml. The auth table with api_key_env keeps the token in your environment; a static headers table sends it as written. The command writes the same tables.',
      },
      {
        key: 'jetbrains',
        label: 'JetBrains',
        docs: 'https://www.jetbrains.com/help/ai-assistant/mcp.html',
        formats: [
          jsonFile({
            key: 'file', label: 'Remote URL (OAuth or open server)', filename: 'As JSON', fileKey: 'mcpServers',
            entry: (ctx, s) => ({ url: s.url }),
          }),
          jsonFile({
            key: 'bridge', label: 'Via mcp-remote (token)', filename: 'As JSON', fileKey: 'mcpServers',
            entry: (ctx, s) => mcpRemoteEntry(ctx, s),
          }),
        ],
        hint: 'Settings → Tools → AI Assistant → Model Context Protocol (MCP) → Add → As JSON, and paste. The documented remote form is a url entry over streamable HTTP, with sign-in handled by the IDE when the deployment offers OAuth; a static token is not part of that form, so the second variant bridges through mcp-remote (needs Node.js) and sends the header from there.',
      },
      {
        key: 'continue',
        label: 'Continue',
        docs: 'https://docs.continue.dev/customize/deep-dives/mcp',
        formats: [
          textFile({
            key: 'file', label: 'Block (URL)', filename: '.continue/mcpServers/clio.yaml', language: 'yaml',
            entry: (ctx, s) => `  - name: ${s.id}\n    type: streamable-http\n    url: ${JSON.stringify(s.url)}\n`,
            wrap: (fragments) => `name: Clio\nversion: 0.0.1\nschema: v1\nmcpServers:\n${fragments.join('')}`,
          }),
          textFile({
            key: 'bridge', label: 'Block via mcp-remote (token)', filename: '.continue/mcpServers/clio.yaml', language: 'yaml',
            entry: (ctx, s) => {
              const e = mcpRemoteEntry(ctx, s)
              let y = `  - name: ${s.id}\n    command: ${e.command}\n    args:\n${e.args.map((a) => `      - ${JSON.stringify(a)}\n`).join('')}`
              if (e.env) y += `    env:\n      AUTH_HEADER: ${JSON.stringify(e.env.AUTH_HEADER)}\n`
              return y
            },
            wrap: (fragments) => `name: Clio\nversion: 0.0.1\nschema: v1\nmcpServers:\n${fragments.join('')}`,
          }),
        ],
        hint: 'Save the block as its own file under .continue/mcpServers/ in the workspace (YAML or JSON). Continue documents url for streamable-http servers and no header field, so a token goes through the mcp-remote bridge (needs Node.js); a value of the form ${{ secrets.NAME }} is read from Continue\'s secrets instead of the file.',
      },
    ],
  },
  {
    title: 'Desktop and chat apps',
    clients: [
      {
        key: 'desktop',
        label: 'Claude Desktop',
        docs: 'https://support.claude.com/en/articles/10949351-getting-started-with-model-context-protocol-mcp-on-claude-for-desktop',
        formats: [
          jsonFile({
            key: 'file', label: 'claude_desktop_config.json (mcp-remote)', filename: 'claude_desktop_config.json', fileKey: 'mcpServers',
            entry: (ctx, s) => mcpRemoteEntry(ctx, s),
          }),
          text({
            key: 'connector', label: 'Custom connector', filename: 'connector',
            render: (ctx) => ctx.servers.map((s) => `Settings → Connectors → Add custom connector\n\nName: ${s.id}\nRemote MCP server URL: ${s.url}\n`).join('\n'),
          }),
        ],
        hint: (ctx) => ctx.oauthEnabled
          ? 'With sign-in enabled, add the URL as a custom connector and sign in; no token to paste. The config-file form bridges through mcp-remote (needs Node.js) for a token.'
          : 'Claude Desktop launches local servers from claude_desktop_config.json (Settings → Developer → Edit Config), so mcp-remote (needs Node.js) bridges it to this URL with the header; restart Claude Desktop afterwards. A custom connector signs in with OAuth and needs an identity provider on this deployment.',
      },
      {
        key: 'claude-ai',
        label: 'claude.ai',
        docs: 'https://support.claude.com/en/articles/11175166-getting-started-with-custom-connectors-using-remote-mcp',
        formats: [
          text({
            key: 'connector', label: 'Custom connector', filename: 'connector',
            render: (ctx) => ctx.servers.map((s) => `Settings → Connectors → Add custom connector\n\nName: ${s.id}\nRemote MCP server URL: ${s.url}\n`).join('\n'),
          }),
        ],
        hint: (ctx) => ctx.oauthEnabled
          ? 'claude.ai connects from Anthropic\'s servers, so the URL must be reachable from the internet. It signs in through this deployment\'s identity provider; no token to paste.'
          : 'claude.ai custom connectors sign in with OAuth and cannot send a static bearer token. This deployment has no identity provider configured, so set one up (docs/IDENTITY.md) or use Claude Desktop / Claude Code with a token instead.',
      },
      {
        key: 'chatgpt',
        label: 'ChatGPT',
        docs: 'https://platform.openai.com/docs/mcp',
        formats: [
          text({
            key: 'connector', label: 'Connector', filename: 'connector',
            render: (ctx) => ctx.servers.map((s) => `Settings → Connectors → Create (developer mode)\n\nName: ${s.id}\nMCP server URL: ${s.url}\nAuthentication: OAuth\n`).join('\n'),
          }),
        ],
        hint: (ctx) => ctx.oauthEnabled
          ? 'ChatGPT connects from OpenAI\'s servers (public URL required) and signs in through your identity provider. The server exposes the search and fetch tools ChatGPT connectors and deep research expect, scoped like every other tool.'
          : 'ChatGPT custom connectors need OAuth or an unauthenticated endpoint; a pasted bearer token is not an option. Configure an identity provider (docs/IDENTITY.md) first.',
      },
      {
        key: 'grok',
        label: 'Grok',
        docs: 'https://docs.x.ai/docs/guides/tools/remote-mcp-tools',
        formats: [
          text({
            key: 'connector', label: 'Connector', filename: 'connector',
            render: (ctx) => ctx.servers.map((s) => `grok.com → Connectors → New connector → Custom\n\nName: ${s.id}\nMCP server URL: ${s.url}\n`).join('\n'),
          }),
        ],
        hint: 'Grok connects from xAI\'s servers: the URL must be reachable from the public internet (localhost and private addresses are rejected) and authentication happens in the connector dialog, so the deployment needs an identity provider for OAuth. For the xAI API, see the Automation group.',
      },
      {
        key: 'hermes',
        label: 'Hermes Agent',
        docs: 'https://github.com/NousResearch/hermes-agent',
        formats: [
          textFile({
            filename: '~/.hermes/config.yaml', language: 'yaml', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => `  ${s.id}:\n    url: ${JSON.stringify(endpoint(ctx, s))}\n${bearer ? `    headers:\n      Authorization: ${JSON.stringify(bearer)}\n` : ''}`,
            wrap: (fragments) => `mcp_servers:\n${fragments.join('')}`,
          }),
        ],
        hint: 'Merge under mcp_servers in ~/.hermes/config.yaml. Hermes connects over streamable HTTP; the URL must be reachable from where Hermes runs.',
      },
      {
        key: 'goose',
        label: 'Goose',
        docs: 'https://block.github.io/goose/docs/getting-started/using-extensions',
        formats: [
          textFile({
            filename: '~/.config/goose/config.yaml', language: 'yaml', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => `  ${s.id}:\n    enabled: true\n    type: ${ctx.transport === 'sse' ? 'sse' : 'streamable_http'}\n    name: ${s.id}\n    uri: ${JSON.stringify(endpoint(ctx, s))}\n${bearer ? `    headers:\n      Authorization: ${JSON.stringify(bearer)}\n` : ''}    timeout: 300\n`,
            wrap: (fragments) => `extensions:\n${fragments.join('')}`,
          }),
        ],
        hint: 'Merge under extensions in ~/.config/goose/config.yaml, or add it from Goose\'s Extensions screen as a Remote Extension (Streamable HTTP) with the same URL and header.',
      },
      {
        key: 'anythingllm',
        label: 'AnythingLLM',
        docs: 'https://docs.anythingllm.com/mcp-compatibility/overview',
        formats: [
          jsonFile({
            filename: 'anythingllm_mcp_servers.json', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => withHeaders({ type: ctx.transport === 'sse' ? 'sse' : 'streamable', url: endpoint(ctx, s) }, bearer),
          }),
        ],
        hint: 'Merge into anythingllm_mcp_servers.json in AnythingLLM\'s storage/plugins directory, then reload MCP servers. The URL must be reachable from AnythingLLM; localhost inside a container refers to that container.',
      },
      {
        key: 'librechat',
        label: 'LibreChat',
        docs: 'https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/mcp_servers',
        formats: [
          textFile({
            filename: 'librechat.yaml', language: 'yaml', envSyntax: dollarBrace, transports: ['http', 'sse'],
            entry: (ctx, s, bearer) => `  ${s.id}:\n    type: ${ctx.transport === 'sse' ? 'sse' : 'streamable-http'}\n    url: ${JSON.stringify(endpoint(ctx, s))}\n${bearer ? `    headers:\n      Authorization: ${JSON.stringify(bearer)}\n` : ''}    timeout: 60000\n`,
            wrap: (fragments) => `mcpServers:\n${fragments.join('')}`,
          }),
        ],
        hint: 'Merge under mcpServers in librechat.yaml. LibreChat reads ${VAR} inside header values, so the token can live in the container\'s environment instead.',
      },
      {
        key: 'lmstudio',
        label: 'LM Studio',
        docs: 'https://lmstudio.ai/docs/app/mcp',
        formats: [
          jsonFile({
            filename: 'mcp.json', fileKey: 'mcpServers',
            entry: (ctx, s, bearer) => withHeaders({ url: s.url }, bearer),
          }),
        ],
        hint: 'In LM Studio open the Program tab → Install → Edit mcp.json and merge this under mcpServers; servers load when the file is saved. A url entry is a remote server.',
      },
      {
        key: 'warp',
        label: 'Warp',
        docs: 'https://docs.warp.dev/agents/capabilities/mcp',
        formats: [
          jsonFile({
            filename: '~/.warp/.mcp.json', fileKey: 'mcpServers',
            entry: (ctx, s, bearer) => withHeaders({ url: s.url }, bearer),
          }),
        ],
        hint: 'Settings → AI → MCP Servers → + Add and paste the JSON, or keep it in ~/.warp/.mcp.json (every project) or .warp/.mcp.json (this repository). Warp sends the headers object with each request.',
      },
      {
        key: 'openwebui',
        label: 'Open WebUI',
        docs: 'https://docs.openwebui.com/features/plugin/tools/',
        formats: [
          text({
            key: 'ui', label: 'Admin settings', filename: 'admin settings',
            render: (ctx) => ctx.servers.map((s) => `Admin Settings → External Tools → + Add connection\n\nType: MCP (Streamable HTTP)\nURL: ${s.url}\nAuth: Bearer\nKey: ${noCred(ctx) ? '(none)' : envMode(ctx) ? `(the value of ${envName(ctx)})` : rawToken(ctx)}\n`).join('\n'),
          }),
        ],
        hint: 'Open WebUI 0.6.31 or newer speaks MCP natively (older versions need the mcpo proxy). Choose the MCP type, not OpenAPI, and set the key: an empty key sends an empty Authorization header, which is refused.',
      },
    ],
  },
  {
    title: 'Automation, frameworks and APIs',
    clients: [
      {
        key: 'n8n',
        label: 'n8n',
        docs: 'https://docs.n8n.io/integrations/builtin/cluster-nodes/sub-nodes/n8n-nodes-langchain.toolmcp/',
        formats: [
          text({
            key: 'node', label: 'MCP Client Tool node', filename: 'MCP Client Tool node', transports: ['http', 'sse'],
            render: (ctx) => ctx.servers.map((s) => `Node: MCP Client Tool\nServer Transport: ${ctx.transport === 'sse' ? 'SSE' : 'HTTP Streamable'}\nEndpoint: ${endpoint(ctx, s)}\n${noCred(ctx) ? 'Authentication: None\n' : `Authentication: Header Auth\n  Name: Authorization\n  Value: ${envMode(ctx) ? `Bearer <value of ${envName(ctx)}>` : `Bearer ${rawToken(ctx)}`}\n`}`).join('\n'),
          }),
        ],
        hint: 'Store the header as an n8n credential rather than typing it into the node. A 401 means the token does not match; a connection error usually means n8n cannot reach the URL from its container.',
      },
      {
        key: 'langchain',
        label: 'LangChain',
        docs: 'https://github.com/langchain-ai/langchain-mcp-adapters',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'agent.py', language: 'python', transports: ['http', 'sse'],
            render: (ctx) => {
              const entries = ctx.servers.map((s) => `    ${JSON.stringify(s.id)}: {\n        "transport": ${JSON.stringify(ctx.transport === 'sse' ? 'sse' : 'streamable_http')},\n        "url": ${JSON.stringify(endpoint(ctx, s))},\n${noCred(ctx) ? '' : `        "headers": {"Authorization": ${pyBearer(ctx)}},\n`}    },`).join('\n')
              return `${pyImportOs(ctx)}from langchain_mcp_adapters.client import MultiServerMCPClient\n\nclient = MultiServerMCPClient({\n${entries}\n})\ntools = await client.get_tools()   # hand these to create_agent / any LangGraph agent\n`
            },
          }),
        ],
        hint: 'pip install langchain-mcp-adapters. Every tool keeps its server-side scoping; the agent sees only the collections this token may read.',
      },
      {
        key: 'python-sdk',
        label: 'Python SDK',
        docs: 'https://github.com/modelcontextprotocol/python-sdk',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'clio_client.py', language: 'python',
            render: (ctx) => {
              const s = ctx.servers[0]
              return `${pyImportOs(ctx)}from mcp import ClientSession\nfrom mcp.client.streamable_http import streamablehttp_client\n\nasync with streamablehttp_client(${JSON.stringify(s.url)}${noCred(ctx) ? '' : `, headers={"Authorization": ${pyBearer(ctx)}}`}) as (read, write, _):\n    async with ClientSession(read, write) as session:\n        await session.initialize()\n        result = await session.call_tool("search_collection", {"query": "what is the backup window", "top_k": 5})\n        print(result.structuredContent or result.content[0].text)\n`
            },
          }),
        ],
        hint: 'pip install mcp. Call health_check first on a new server, then list_collections; research_documents for multi-part questions, ask_documents (when present) for a finished cited answer. With several collections chosen, open one session per URL.',
      },
      {
        key: 'typescript-sdk',
        label: 'TypeScript SDK',
        docs: 'https://github.com/modelcontextprotocol/typescript-sdk',
        formats: [
          text({
            key: 'ts', label: 'TypeScript', filename: 'clio-client.ts', language: 'typescript',
            render: (ctx) => {
              const s = ctx.servers[0]
              return `import { Client } from "@modelcontextprotocol/sdk/client/index.js";\nimport { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";\n\nconst transport = new StreamableHTTPClientTransport(new URL(${JSON.stringify(s.url)})${noCred(ctx) ? '' : `, {\n  requestInit: { headers: { Authorization: ${tsBearer(ctx)} } },\n}`});\nconst client = new Client({ name: ${JSON.stringify(`${s.id}-client`)}, version: "1.0.0" });\nawait client.connect(transport);\nconst result = await client.callTool({ name: "search_collection", arguments: { query: "what is the backup window", top_k: 5 } });\nconsole.log(result.structuredContent ?? result.content);\n`
            },
          }),
        ],
        hint: 'npm install @modelcontextprotocol/sdk. The same transport class is what Cursor and most TypeScript agents use under the hood.',
      },
      {
        key: 'claude-agent-sdk',
        label: 'Claude Agent SDK',
        docs: 'https://code.claude.com/docs/en/agent-sdk/mcp',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'agent.py', language: 'python',
            render: (ctx) => {
              const entries = ctx.servers.map((s) => `        ${JSON.stringify(s.id)}: {\n            "type": "http",\n            "url": ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `            "headers": {"Authorization": ${pyBearer(ctx)}},\n`}        },`).join('\n')
              const allowed = ctx.servers.map((s) => JSON.stringify(`mcp__${s.id.replace(/[^A-Za-z0-9_-]/g, '_')}__*`)).join(', ')
              return `import asyncio\n${pyImportOs(ctx)}from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage\n\n\nasync def main():\n    options = ClaudeAgentOptions(\n        mcp_servers={\n${entries}\n        },\n        allowed_tools=[${allowed}],\n    )\n    async for message in query(prompt="What is the backup window? Cite the source.", options=options):\n        if isinstance(message, ResultMessage) and message.subtype == "success":\n            print(message.result)\n\n\nasyncio.run(main())\n`
            },
          }),
          text({
            key: 'ts', label: 'TypeScript', filename: 'agent.ts', language: 'typescript',
            render: (ctx) => {
              const entries = ctx.servers.map((s) => `      ${JSON.stringify(s.id)}: {\n        type: "http",\n        url: ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `        headers: { Authorization: ${tsBearer(ctx)} },\n`}      },`).join('\n')
              const allowed = ctx.servers.map((s) => JSON.stringify(`mcp__${s.id.replace(/[^A-Za-z0-9_-]/g, '_')}__*`)).join(', ')
              return `import { query } from "@anthropic-ai/claude-agent-sdk";\n\nfor await (const message of query({\n  prompt: "What is the backup window? Cite the source.",\n  options: {\n    mcpServers: {\n${entries}\n    },\n    allowedTools: [${allowed}],\n  },\n})) {\n  if (message.type === "result" && message.subtype === "success") console.log(message.result);\n}\n`
            },
          }),
        ],
        hint: 'pip install claude-agent-sdk / npm install @anthropic-ai/claude-agent-sdk. MCP tools need explicit permission: the mcp__<server>__* wildcard in allowed_tools approves every tool from this server and nothing else. A .mcp.json in the project (the Claude Code recipe) is picked up too.',
      },
      {
        key: 'openai-agents',
        label: 'OpenAI Agents SDK',
        docs: 'https://openai.github.io/openai-agents-python/mcp/',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'agent.py', language: 'python',
            render: (ctx) => {
              const s = ctx.servers[0]
              return `${pyImportOs(ctx)}from agents import Agent, Runner\nfrom agents.mcp import MCPServerStreamableHttp\n\nasync with MCPServerStreamableHttp(\n    name=${JSON.stringify(s.id)},\n    params={\n        "url": ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `        "headers": {"Authorization": ${pyBearer(ctx)}},\n`}        "timeout": 30,\n    },\n    cache_tools_list=True,\n) as server:\n    agent = Agent(\n        name="Assistant",\n        instructions="Answer from the ${s.id} tools and cite the source.",\n        mcp_servers=[server],\n    )\n    result = await Runner.run(agent, "What is the backup window?")\n    print(result.final_output)\n`
            },
          }),
        ],
        hint: 'pip install openai-agents. The server runs on your side and is connected before Runner.run (an unconnected server yields no tools). For a server OpenAI calls from its own side, use the OpenAI API recipe (the same shape as HostedMCPTool).',
      },
      {
        key: 'vercel-ai',
        label: 'Vercel AI SDK',
        docs: 'https://ai-sdk.dev/docs/ai-sdk-core/mcp-tools',
        formats: [
          text({
            key: 'ts', label: 'TypeScript', filename: 'agent.ts', language: 'typescript',
            render: (ctx) => {
              const s = ctx.servers[0]
              return `import { createMCPClient } from "@ai-sdk/mcp";\nimport { generateText } from "ai";\n\nconst mcpClient = await createMCPClient({\n  transport: {\n    type: "http",\n    url: ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `    headers: { Authorization: ${tsBearer(ctx)} },\n`}  },\n});\ntry {\n  const tools = await mcpClient.tools();\n  const { text } = await generateText({ model, tools, prompt: "What is the backup window? Cite the source." });\n  console.log(text);\n} finally {\n  await mcpClient.close();\n}\n`
            },
          }),
        ],
        hint: 'npm install ai @ai-sdk/mcp. The http transport is streamable HTTP; close the client when the call is done (in streamText, from onEnd).',
      },
      {
        key: 'pydantic-ai',
        label: 'Pydantic AI',
        docs: 'https://ai.pydantic.dev/mcp/client/',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'agent.py', language: 'python',
            render: (ctx) => {
              const toolsets = ctx.servers.map((s) => `MCPToolset(${JSON.stringify(s.url)}${noCred(ctx) ? '' : `, headers={"Authorization": ${pyBearer(ctx)}}`})`)
              return `${pyImportOs(ctx)}from pydantic_ai import Agent\nfrom pydantic_ai.mcp import MCPToolset\n\nagent = Agent("openai:gpt-5", toolsets=[\n${toolsets.map((t) => `    ${t},`).join('\n')}\n])\nresult = agent.run_sync("What is the backup window? Cite the source.")\nprint(result.output)\n`
            },
          }),
        ],
        hint: 'pip install "pydantic-ai-slim[mcp]". MCPToolset infers streamable HTTP from the URL; older releases called the class MCPServerStreamableHTTP and took mcp_servers= instead of toolsets=.',
      },
      {
        key: 'google-adk',
        label: 'Google ADK',
        docs: 'https://google.github.io/adk-docs/tools/mcp-tools/',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'agent.py', language: 'python',
            render: (ctx) => {
              const tools = ctx.servers.map((s) => `        McpToolset(\n            connection_params=StreamableHTTPConnectionParams(\n                url=${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `                headers={"Authorization": ${pyBearer(ctx)}},\n`}            ),\n        ),`).join('\n')
              return `${pyImportOs(ctx)}from google.adk.agents import LlmAgent\nfrom google.adk.tools.mcp_tool.mcp_toolset import McpToolset\nfrom google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams\n\nroot_agent = LlmAgent(\n    model="gemini-2.5-pro",\n    name="clio_agent",\n    instruction="Answer from the document tools and cite the source.",\n    tools=[\n${tools}\n    ],\n)\n`
            },
          }),
        ],
        hint: 'pip install google-adk. The toolset connects when the agent first needs it; the import path of StreamableHTTPConnectionParams has moved between releases, so check yours if it fails to import.',
      },
      {
        key: 'openai-api',
        label: 'OpenAI API',
        docs: 'https://platform.openai.com/docs/guides/tools-remote-mcp',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'responses.py', language: 'python',
            render: (ctx) => {
              const tools = ctx.servers.map((s) => `        {\n            "type": "mcp",\n            "server_label": ${JSON.stringify(s.id)},\n            "server_url": ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `            "headers": {"Authorization": ${pyBearer(ctx)}},\n`}            "require_approval": "never",\n        },`).join('\n')
              return `${pyImportOs(ctx)}from openai import OpenAI\n\nclient = OpenAI()\nresponse = client.responses.create(\n    model="gpt-5",\n    tools=[\n${tools}\n    ],\n    input="Using the document tools, what is the backup window? Cite the source.",\n)\nprint(response.output_text)\n`
            },
          }),
        ],
        hint: 'The Responses API calls the server from OpenAI\'s side, so the URL must be reachable from the internet. Headers are not stored between requests; send them every time. Pick whichever model you use; the tool block is the same.',
      },
      {
        key: 'anthropic-api',
        label: 'Anthropic API',
        docs: 'https://docs.anthropic.com/en/docs/agents-and-tools/mcp-connector',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'messages.py', language: 'python',
            render: (ctx) => {
              const servers = ctx.servers.map((s) => `        {\n            "type": "url",\n            "url": ${JSON.stringify(s.url)},\n            "name": ${JSON.stringify(s.id)},\n${noCred(ctx) ? '' : `            "authorization_token": ${pyToken(ctx)},\n`}        },`).join('\n')
              const toolsets = ctx.servers.map((s) => `{"type": "mcp_toolset", "mcp_server_name": ${JSON.stringify(s.id)}}`).join(', ')
              return `${pyImportOs(ctx)}import anthropic\n\nclient = anthropic.Anthropic()\nresponse = client.beta.messages.create(\n    model="claude-opus-5-5",\n    max_tokens=16000,\n    betas=["mcp-client-2025-11-20"],\n    mcp_servers=[\n${servers}\n    ],\n    tools=[${toolsets}],\n    messages=[{"role": "user", "content": "Using the document tools, what is the backup window? Cite the source."}],\n)\nprint(next(b.text for b in response.content if b.type == "text"))\n`
            },
          }),
        ],
        hint: 'The MCP connector calls the server from Anthropic\'s side (public URL required). Both halves are needed: mcp_servers names the server, the mcp_toolset entry in tools exposes it.',
      },
      {
        key: 'xai-api',
        label: 'xAI API',
        docs: 'https://docs.x.ai/docs/guides/tools/remote-mcp-tools',
        formats: [
          text({
            key: 'py', label: 'Python', filename: 'grok_responses.py', language: 'python',
            render: (ctx) => {
              const tools = ctx.servers.map((s) => `        {\n            "type": "mcp",\n            "server_label": ${JSON.stringify(s.id)},\n            "server_url": ${JSON.stringify(s.url)},\n${noCred(ctx) ? '' : `            "headers": {"Authorization": ${pyBearer(ctx)}},\n`}        },`).join('\n')
              return `${pyImportOs(ctx)}from openai import OpenAI\n\nclient = OpenAI(base_url="https://api.x.ai/v1", api_key=XAI_API_KEY)\nresponse = client.responses.create(\n    model="grok-4",\n    tools=[\n${tools}\n    ],\n    input="Using the document tools, what is the backup window? Cite the source.",\n)\nprint(response.output_text)\n`
            },
          }),
        ],
        hint: 'xAI\'s remote MCP tool uses the same Responses-API shape through its OpenAI-compatible endpoint (or the xAI SDK). The server is called from xAI\'s side, so it must be reachable from the internet.',
      },
      {
        key: 'generic',
        label: 'Any MCP client',
        docs: 'https://modelcontextprotocol.io/docs/concepts/transports',
        formats: [
          text({
            key: 'facts', label: 'Connection facts', filename: 'clio-mcp.txt', transports: ['http', 'sse'],
            render: (ctx) => {
              const bearer = bearerFor(ctx, dollarBrace)
              const lines = ctx.servers.map((s) => `Endpoint:    ${s.url}\nLegacy SSE:  ${s.sseUrl}`).join('\n')
              return `Transport:   MCP streamable HTTP (stateless, JSON responses) — the current standard\n${lines}\n${bearer.value ? `Header:      Authorization: ${bearer.value}\n` : 'Header:      none (open server or OAuth sign-in)\n'}\nThe legacy SSE endpoint is for clients that only speak the older HTTP+SSE transport;\nsame header; the server announces its message endpoint on connect.\n\nFirst calls: health_check, then list_collections. Hosted connectors (claude.ai,\nChatGPT, Grok) need a URL reachable from the vendor's servers and sign in with\nOAuth when the deployment has an identity provider.\n`
            },
          }),
          jsonFile({
            key: 'bridge', label: 'mcp-remote bridge (stdio-only clients)', filename: 'mcpServers entry', fileKey: 'mcpServers', transports: ['http', 'sse'],
            entry: (ctx, s) => mcpRemoteEntry(ctx, s),
          }),
        ],
        hint: 'Everything that speaks MCP attaches with these facts: Dify, Flowise, Semantic Kernel, Spring AI, Agno, CrewAI, smolagents, Open Interpreter and others. Prefer streamable HTTP; fall back to the SSE URL only when the client offers nothing else. A client that can only launch local commands gets the bridge entry (npx mcp-remote, needs Node.js).',
      },
    ],
  },
]

export const MCP_CLIENTS = MCP_CLIENT_GROUPS.flatMap((g) => g.clients)

export function findClient(key) {
  return MCP_CLIENTS.find((c) => c.key === key) || MCP_CLIENTS[0]
}

function toBase64(s) {
  const bytes = new TextEncoder().encode(s)
  let bin = ''
  for (const b of bytes) bin += String.fromCharCode(b)
  return btoa(bin)
}

/** Formats a client offers for the chosen transport (every client offers streamable HTTP). */
export function formatsFor(client, transport = 'http') {
  const list = client.formats.filter((f) => f.transports.includes(transport))
  return list.length ? list : client.formats
}

/**
 * Render one client's configuration.
 *
 * `ctx` is described at the top of this file. `options.format` picks one of
 * the client's documented formats (its first when omitted); `options.entryOnly`
 * renders just the server entry for formats that support it. Returns the
 * content plus the surrounding facts the panel shows: filename, hint, docs
 * link, the list of formats, and whether the client supports what was asked
 * (`sseSupported`, `entryOnlySupported`).
 */
export function buildClientConfig(key, ctx, options = {}) {
  const client = findClient(key)
  const transport = ctx.transport === 'sse' ? 'sse' : 'http'
  const sseSupported = client.formats.some((f) => f.transports.includes('sse'))
  const effective = { ...ctx, transport: sseSupported ? transport : 'http' }
  const formats = formatsFor(client, effective.transport)
  const format = formats.find((f) => f.key === options.format) || formats[0]
  const entryOnly = Boolean(options.entryOnly) && format.entryOnly
  const { content, note } = format.render(effective, { entryOnly })
  const hint = typeof client.hint === 'function' ? client.hint(effective) : client.hint
  const notes = [note]
  if (transport === 'sse' && !sseSupported) notes.push(`${client.label} documents streamable HTTP only; the standard endpoint is used.`)
  return {
    key: client.key,
    label: client.label,
    docs: client.docs,
    filename: format.filename,
    format: format.key,
    formatLabel: format.label,
    language: format.language,
    formats: formats.map((f) => ({ key: f.key, label: f.label, filename: f.filename, entryOnly: f.entryOnly })),
    entryOnly,
    entryOnlySupported: format.entryOnly,
    sseSupported,
    content,
    note: notes.filter(Boolean).join(' '),
    hint,
  }
}
