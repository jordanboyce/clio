// Connection recipes for every MCP client the Connect tab knows about.
//
// One entry per client: how to say "this server, this bearer token" in that
// client's own configuration language. The server side is identical for all
// of them (standard MCP streamable HTTP at /mcp, plus the legacy SSE shape at
// /mcp/sse for clients that predate streamable HTTP), so this file is only
// about spelling. Entries are generated locally in the browser; the server
// never sees the snippet or the token after it was minted.
//
// Keep each `content` a faithful copy of the client's documented shape; a
// snippet that looks plausible but is wrong costs the person an afternoon.
// `hint` says where the snippet goes and what to watch for.

const bearer = (token) => `Bearer ${token}`

const json = (value) => JSON.stringify(value, null, 2)

// `ctx`: { id, url, sseUrl, token, oauthEnabled }
//   id      — server name used as the key inside the client's config
//   url     — the streamable HTTP endpoint (…/mcp), with ?collection_id when chosen
//   sseUrl  — the legacy SSE endpoint (…/mcp/sse)
//   token   — the personal token (or a placeholder when none is selected)

export const MCP_CLIENT_GROUPS = [
  {
    title: 'Coding agents and IDEs',
    clients: [
      {
        key: 'claude',
        label: 'Claude Code',
        filename: '.mcp.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'http', url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Save as .mcp.json in the project root (or run `claude mcp add --transport http ' +
          '<name> <url> --header "Authorization: Bearer <token>"`). Keep the file out of version control.',
      },
      {
        key: 'codex',
        label: 'Codex',
        filename: '~/.codex/config.toml',
        render: (c) => `[mcp_servers.${c.id}]\nurl = "${c.url}"\nhttp_headers = { "Authorization" = "${bearer(c.token)}" }\n`,
        hint: 'Merge this table into ~/.codex/config.toml. Keep the credential private.',
      },
      {
        key: 'pi',
        label: 'Pi',
        filename: '~/.pi/agent/mcp.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'streamable-http', url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Merge under mcpServers in ~/.pi/agent/mcp.json (or .pi/mcp.json for one project, read once the project is trusted). ' +
          'Equivalent one-liner: `pi mcp add <name> --url <url> --bearer-token-env-var CLIO_TOKEN`, which keeps the token in an environment variable (headers accept ${VAR}). ' +
          'Pi speaks streamable HTTP only, which is this URL; it does not use the legacy SSE endpoint.',
      },
      {
        key: 'gemini',
        label: 'Gemini CLI',
        filename: '~/.gemini/settings.json',
        render: (c) => json({ mcpServers: { [c.id]: { httpUrl: c.url, headers: { Authorization: bearer(c.token) }, timeout: 60000 } } }),
        hint: 'Merge under mcpServers in ~/.gemini/settings.json (user-wide) or .gemini/settings.json (project). ' +
          'httpUrl is the streamable HTTP transport; `url` would select the legacy SSE transport. ' +
          'Gemini CLI expands ${ENV_VAR} inside values if you would rather not store the token in the file.',
      },
      {
        key: 'cursor',
        label: 'Cursor',
        filename: '.cursor/mcp.json',
        render: (c) => json({ mcpServers: { [c.id]: { url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Save as .cursor/mcp.json in the project (or ~/.cursor/mcp.json for every project), then enable the server under Cursor Settings → MCP.',
      },
      {
        key: 'copilot',
        label: 'VS Code',
        filename: '.vscode/mcp.json',
        render: (c) => json({ servers: { [c.id]: { type: 'http', url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Place at .vscode/mcp.json in the project, or merge into your VS Code user settings. GitHub Copilot in VS Code reads it.',
      },
      {
        key: 'copilot-cli',
        label: 'Copilot CLI',
        filename: '~/.copilot/mcp-config.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'http', url: c.url, headers: { Authorization: bearer(c.token) }, tools: ['*'] } } }),
        hint: 'Merge under mcpServers in ~/.copilot/mcp-config.json (the CLI\'s own file, not VS Code\'s mcp.json), or run `/mcp add` inside Copilot CLI and paste the URL and header there.',
      },
      {
        key: 'windsurf',
        label: 'Windsurf',
        filename: '~/.codeium/windsurf/mcp_config.json',
        render: (c) => json({ mcpServers: { [c.id]: { serverUrl: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Merge this entry into ~/.codeium/windsurf/mcp_config.json and refresh the MCP list in Windsurf.',
      },
      {
        key: 'zed',
        label: 'Zed',
        filename: '~/.config/zed/settings.json',
        render: (c) => json({ context_servers: { [c.id]: { url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Merge under context_servers (Zed\'s name for MCP servers) in your Zed settings.json. Leave headers out to let Zed sign in with OAuth instead, when the deployment offers it.',
      },
      {
        key: 'cline',
        label: 'Cline',
        filename: 'cline_mcp_settings.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'streamableHttp', url: c.url, headers: { Authorization: bearer(c.token) }, disabled: false } } }),
        hint: 'Cline → MCP Servers → Remote Servers → Edit Configuration opens cline_mcp_settings.json; merge this under mcpServers. The type is streamableHttp ("sse" is the legacy transport, see the Any MCP client entry).',
      },
      {
        key: 'roo',
        label: 'Roo Code',
        filename: '.roo/mcp.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'streamable-http', url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Save as .roo/mcp.json in the project, or merge into the global mcp_settings.json from Roo Code\'s MCP panel. Note the hyphenated type.',
      },
      {
        key: 'kiro',
        label: 'Kiro',
        filename: '.kiro/settings/mcp.json',
        render: (c) => json({ mcpServers: { [c.id]: { url: c.url, headers: { Authorization: bearer(c.token) }, disabled: false } } }),
        hint: 'Save as .kiro/settings/mcp.json in the workspace, or ~/.kiro/settings/mcp.json for every workspace. A url entry is a remote server; without headers Kiro tries OAuth.',
      },
      {
        key: 'opencode',
        label: 'OpenCode',
        filename: 'opencode.json',
        render: (c) => json({ $schema: 'https://opencode.ai/config.json', mcp: { [c.id]: { type: 'remote', url: c.url, enabled: true, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Merge under mcp in opencode.json (project) or ~/.config/opencode/opencode.json (global).',
      },
    ],
  },
  {
    title: 'Desktop and chat apps',
    clients: [
      {
        key: 'desktop',
        label: 'Claude Desktop',
        filename: 'claude_desktop_config.json',
        render: (c) => json({ mcpServers: { [c.id]: {
          command: 'npx',
          args: ['-y', 'mcp-remote', c.url, '--header', `Authorization: ${bearer(c.token)}`],
        } } }),
        hint: (c) => c.oauthEnabled
          ? 'With sign-in enabled you can skip this: Settings → Connectors → Add custom connector, paste the server URL and sign in. This token form bridges through mcp-remote (needs Node.js).'
          : 'Claude Desktop speaks stdio to local servers, so mcp-remote (needs Node.js) bridges it to this URL. Merge into claude_desktop_config.json (Settings → Developer → Edit Config) and restart Claude Desktop.',
      },
      {
        key: 'claude-ai',
        label: 'claude.ai',
        filename: 'connector',
        render: (c) => `Settings → Connectors → Add custom connector\n\nName: ${c.id}\nRemote MCP server URL: ${c.url}\n`,
        hint: (c) => c.oauthEnabled
          ? 'claude.ai connects from Anthropic\'s servers, so the URL must be reachable from the internet. It signs in through this deployment\'s identity provider; no token to paste.'
          : 'claude.ai custom connectors sign in with OAuth and cannot send a static bearer token. This deployment has no identity provider configured, so set one up (docs/IDENTITY.md) or use Claude Desktop / Claude Code with a token instead.',
      },
      {
        key: 'chatgpt',
        label: 'ChatGPT',
        filename: 'connector',
        render: (c) => `Settings → Connectors → Create (developer mode)\n\nName: ${c.id}\nMCP server URL: ${c.url}\nAuthentication: OAuth\n`,
        hint: (c) => c.oauthEnabled
          ? 'ChatGPT connects from OpenAI\'s servers (public URL required) and signs in through your identity provider. The server exposes the search and fetch tools ChatGPT connectors and deep research expect, scoped like every other tool.'
          : 'ChatGPT custom connectors need OAuth or an unauthenticated endpoint; a pasted bearer token is not an option. Configure an identity provider (docs/IDENTITY.md) first.',
      },
      {
        key: 'grok',
        label: 'Grok',
        filename: 'connector',
        render: (c) => `grok.com → Connectors → New connector → Custom\n\nName: ${c.id}\nMCP server URL: ${c.url}\n`,
        hint: 'Grok connects from xAI\'s servers: the URL must be reachable from the public internet (localhost and private addresses are rejected) and authentication happens in the connector dialog, so the deployment needs an identity provider for OAuth. For the xAI API, see the Automation group.',
      },
      {
        key: 'hermes',
        label: 'Hermes Agent',
        filename: '~/.hermes/config.yaml',
        render: (c) => `mcp_servers:\n  ${c.id}:\n    url: "${c.url}"\n    headers:\n      Authorization: "${bearer(c.token)}"\n`,
        hint: 'Merge under mcp_servers in ~/.hermes/config.yaml. Hermes connects over streamable HTTP (its mcp package needs HTTP client support); the URL must be reachable from where Hermes runs.',
      },
      {
        key: 'goose',
        label: 'Goose',
        filename: '~/.config/goose/config.yaml',
        render: (c) => `extensions:\n  ${c.id}:\n    enabled: true\n    type: streamable_http\n    name: ${c.id}\n    uri: "${c.url}"\n    headers:\n      Authorization: "${bearer(c.token)}"\n    timeout: 300\n`,
        hint: 'Merge under extensions in ~/.config/goose/config.yaml, or add it from Goose\'s Extensions screen as a Remote Extension (Streamable HTTP) with the same URL and header.',
      },
      {
        key: 'anythingllm',
        label: 'AnythingLLM',
        filename: 'anythingllm_mcp_servers.json',
        render: (c) => json({ mcpServers: { [c.id]: { type: 'streamable', url: c.url, headers: { Authorization: bearer(c.token) } } } }),
        hint: 'Merge into anythingllm_mcp_servers.json in AnythingLLM\'s storage/plugins directory, then reload MCP servers. The URL must be reachable from AnythingLLM; localhost inside a container refers to that container.',
      },
      {
        key: 'librechat',
        label: 'LibreChat',
        filename: 'librechat.yaml',
        render: (c) => `mcpServers:\n  ${c.id}:\n    type: streamable-http\n    url: "${c.url}"\n    headers:\n      Authorization: "${bearer(c.token)}"\n    timeout: 60000\n`,
        hint: 'Merge under mcpServers in librechat.yaml. LibreChat reads ${ENV_VAR} inside header values, so the token can live in the container\'s environment instead.',
      },
      {
        key: 'openwebui',
        label: 'Open WebUI',
        filename: 'admin settings',
        render: (c) => `Admin Settings → External Tools → + Add connection\n\nType: MCP (Streamable HTTP)\nURL: ${c.url}\nAuth: Bearer\nKey: ${c.token}\n`,
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
        filename: 'MCP Client Tool node',
        render: (c) => `Node: MCP Client Tool\nServer Transport: HTTP Streamable\nEndpoint: ${c.url}\nAuthentication: Header Auth\n  Name: Authorization\n  Value: ${bearer(c.token)}\n`,
        hint: 'Store the header as an n8n credential rather than typing it into the node. A 401 means the token does not match; a connection error usually means n8n cannot reach the URL from its container.',
      },
      {
        key: 'langchain',
        label: 'LangChain',
        filename: 'agent.py',
        render: (c) => `from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "${c.id}": {
        "transport": "streamable_http",
        "url": "${c.url}",
        "headers": {"Authorization": "${bearer(c.token)}"},
    }
})
tools = await client.get_tools()   # hand these to create_agent / any LangGraph agent
`,
        hint: 'pip install langchain-mcp-adapters. Every tool keeps its server-side scoping; the agent sees only the collections this token may read.',
      },
      {
        key: 'python-sdk',
        label: 'Python SDK',
        filename: 'clio_client.py',
        render: (c) => `from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client("${c.url}", headers={"Authorization": "${bearer(c.token)}"}) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("search_collection", {"query": "what is the backup window", "top_k": 5})
        print(result.structuredContent or result.content[0].text)
`,
        hint: 'pip install mcp. Call health_check first on a new server, then list_collections; research_documents for multi-part questions, ask_documents (when present) for a finished cited answer.',
      },
      {
        key: 'typescript-sdk',
        label: 'TypeScript SDK',
        filename: 'clio-client.ts',
        render: (c) => `import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

const transport = new StreamableHTTPClientTransport(new URL("${c.url}"), {
  requestInit: { headers: { Authorization: "${bearer(c.token)}" } },
});
const client = new Client({ name: "${c.id}-client", version: "1.0.0" });
await client.connect(transport);
const result = await client.callTool({ name: "search_collection", arguments: { query: "what is the backup window", top_k: 5 } });
console.log(result.structuredContent ?? result.content);
`,
        hint: 'npm install @modelcontextprotocol/sdk. The same transport class is what Claude Desktop, Cursor and most TypeScript agents use under the hood.',
      },
      {
        key: 'openai-api',
        label: 'OpenAI API',
        filename: 'responses.py',
        render: (c) => `from openai import OpenAI

client = OpenAI()
response = client.responses.create(
    model="gpt-5",
    tools=[{
        "type": "mcp",
        "server_label": "${c.id}",
        "server_url": "${c.url}",
        "headers": {"Authorization": "${bearer(c.token)}"},
        "require_approval": "never",
    }],
    input="Using the ${c.id} tools, what is the backup window? Cite the source.",
)
print(response.output_text)
`,
        hint: 'The Responses API calls the server from OpenAI\'s side, so the URL must be reachable from the internet. Headers are not stored between requests; send them every time. Pick whichever model you use; the tool block is the same.',
      },
      {
        key: 'anthropic-api',
        label: 'Anthropic API',
        filename: 'messages.py',
        render: (c) => `import anthropic

client = anthropic.Anthropic()
response = client.beta.messages.create(
    model="claude-opus-5-5",
    max_tokens=16000,
    betas=["mcp-client-2025-11-20"],
    mcp_servers=[{
        "type": "url",
        "url": "${c.url}",
        "name": "${c.id}",
        "authorization_token": "${c.token}",
    }],
    tools=[{"type": "mcp_toolset", "mcp_server_name": "${c.id}"}],
    messages=[{"role": "user", "content": "Using the ${c.id} tools, what is the backup window? Cite the source."}],
)
print(next(b.text for b in response.content if b.type == "text"))
`,
        hint: 'The MCP connector calls the server from Anthropic\'s side (public URL required). Both halves are needed: mcp_servers names the server, the mcp_toolset entry in tools exposes it.',
      },
      {
        key: 'xai-api',
        label: 'xAI API',
        filename: 'grok_responses.py',
        render: (c) => `from openai import OpenAI

client = OpenAI(base_url="https://api.x.ai/v1", api_key=XAI_API_KEY)
response = client.responses.create(
    model="grok-4",
    tools=[{
        "type": "mcp",
        "server_label": "${c.id}",
        "server_url": "${c.url}",
        "headers": {"Authorization": "${bearer(c.token)}"},
    }],
    input="Using the ${c.id} tools, what is the backup window? Cite the source.",
)
print(response.output_text)
`,
        hint: 'xAI\'s remote MCP tool uses the same Responses-API shape through its OpenAI-compatible endpoint (or the xAI SDK). The server is called from xAI\'s side, so it must be reachable from the internet.',
      },
      {
        key: 'generic',
        label: 'Any MCP client',
        filename: 'clio-mcp.txt',
        render: (c) => `Transport:   MCP streamable HTTP (stateless, JSON responses) — the current standard
Endpoint:    ${c.url}
Header:      Authorization: ${bearer(c.token)}

Legacy SSE:  ${c.sseUrl}   (for clients that only speak the older HTTP+SSE transport;
             same header; the server announces its message endpoint on connect)

First calls: health_check, then list_collections. Hosted connectors (claude.ai,
ChatGPT, Grok) need a URL reachable from the vendor's servers and sign in with
OAuth when the deployment has an identity provider.
`,
        hint: 'Everything that speaks MCP attaches with these facts: Dify, Flowise, Vercel AI SDK, Semantic Kernel, Spring AI, Agno, CrewAI, smolagents, Open Interpreter and others. Prefer streamable HTTP; fall back to the SSE URL only when the client offers nothing else.',
      },
    ],
  },
]

export const MCP_CLIENTS = MCP_CLIENT_GROUPS.flatMap((g) => g.clients)

export function findClient(key) {
  return MCP_CLIENTS.find((c) => c.key === key) || MCP_CLIENTS[0]
}

export function buildClientConfig(key, ctx) {
  const client = findClient(key)
  return {
    key: client.key,
    label: client.label,
    filename: client.filename,
    content: client.render(ctx),
    hint: typeof client.hint === 'function' ? client.hint(ctx) : client.hint,
  }
}
