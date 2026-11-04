// WebMCP: offering Clio's MCP tools to an agent that lives in the browser.
//
// Browsers are starting to carry their own AI agents (Gemini in Chrome,
// agent extensions). WebMCP lets a page hand such an agent a list of tools
// with JSON schemas, the way an MCP server does for a desktop client, and
// the agent calls them in the page, with the page's own session. For Clio
// that means: no server URL to paste, no token to mint, and the agent sees
// exactly the collections the person has open.
//
// This module is the pure part: finding the API, saying why it is missing,
// and shaping an MCP tool as a WebMCP tool. The registration lifecycle is
// in composables/useWebMcp.js, the JSON-RPC calls in utils/mcpPageClient.js.
//
// Support is decided by FEATURE DETECTION, never by the user agent. Chrome,
// Edge, Brave and every other Chromium build that ships the API are found
// the same way; Firefox and Safari simply have no `modelContext` and get
// nothing. The brand and version are read only to word the help text (is
// there a flag to turn on, or is this not a Chromium browser at all).
//
// Status (Chrome docs, 2026-10): origin trial from Chrome 149 through 162,
// local flag `enable-webmcp-testing`. `document.modelContext` since 150;
// `navigator.modelContext` is the deprecated 149 location, kept as a fallback.
// The API needs a secure context, so a Clio served over plain HTTP from
// another machine will not have it even in a current Chrome.

export const WEBMCP_FIRST_CHROME = 149
export const WEBMCP_TRIAL_LAST_CHROME = 162
export const WEBMCP_FLAG = 'flags/#enable-webmcp-testing'

/** The ModelContext object, or null when this runtime has no WebMCP. */
export function getModelContext(win = globalThis) {
  const doc = win?.document
  if (doc && doc.modelContext) return doc.modelContext
  const nav = win?.navigator
  if (nav && nav.modelContext) return nav.modelContext
  return null
}

/**
 * What this browser is, for the help text only. Prefers the User-Agent
 * Client Hints brands (truthful about Chromium descent) and falls back to
 * the user agent string, which Chromium browsers all stamp `Chrome/NNN`.
 *
 * @returns {{chromium: boolean, name: string, version: number|null, flagsScheme: string}}
 */
export function describeBrowser(win = globalThis) {
  const nav = win?.navigator || {}
  const brands = (nav.userAgentData?.brands || []).filter((b) => b && b.brand)
  const ua = nav.userAgent || ''

  let chromium = false
  let name = ''
  let version = null

  if (brands.length) {
    const chromiumBrand = brands.find((b) => /chromium/i.test(b.brand))
    chromium = !!chromiumBrand
    // "Google Chrome", "Microsoft Edge", "Brave" ride beside "Chromium" and
    // a GREASE entry such as "Not;A=Brand"; the product name is the one that
    // is neither.
    const product = brands.find((b) => !/chromium|not.a.brand|not\W*a\W*brand/i.test(b.brand))
    name = product?.brand || chromiumBrand?.brand || ''
    const v = parseInt((product || chromiumBrand)?.version, 10)
    version = Number.isFinite(v) ? v : null
  } else if (ua) {
    const chromeMatch = ua.match(/\bChrom(?:e|ium)\/(\d+)/)
    chromium = !!chromeMatch && !/\bFirefox\//.test(ua)
    if (chromium) version = parseInt(chromeMatch[1], 10)
    if (/\bEdg\//.test(ua)) name = 'Microsoft Edge'
    else if (/\bOPR\//.test(ua)) name = 'Opera'
    else if (/\bBrave\b/.test(ua)) name = 'Brave'
    else if (chromium) name = 'Chrome'
    else if (/\bFirefox\//.test(ua)) name = 'Firefox'
    else if (/\bSafari\//.test(ua) && /\bVersion\//.test(ua)) name = 'Safari'
  }

  return {
    chromium,
    name: name || 'This browser',
    version,
    flagsScheme: /edge/i.test(name) ? 'edge' : 'chrome',
  }
}

/**
 * Why the tools can or cannot be offered here.
 *   available    `modelContext` exists
 *   insecure     no secure context (plain HTTP from another machine)
 *   chromium-off a Chromium browser without the API: flag off, or too old
 *   unsupported  not a Chromium browser
 */
export function supportState(win = globalThis) {
  if (getModelContext(win)) return 'available'
  if (win?.isSecureContext === false) return 'insecure'
  return describeBrowser(win).chromium ? 'chromium-off' : 'unsupported'
}

/**
 * Shape an MCP tool (as `tools/list` returns it) as a WebMCP tool.
 *
 * `invoke(name, args, signal)` performs the call and returns the MCP result.
 * Every tool is marked as returning untrusted content: it quotes documents
 * people uploaded, and a browser agent should treat those passages as data,
 * exactly as Clio tells desktop clients. A tool that writes is flagged as
 * consequential so the agent asks before using it.
 */
export function toWebMcpTool(tool, invoke) {
  const readOnly = isReadOnly(tool)
  return {
    name: tool.name,
    title: tool.title || tool.annotations?.title || tool.name,
    description: tool.description || '',
    inputSchema: tool.inputSchema || { type: 'object', properties: {} },
    annotations: {
      readOnlyHint: readOnly,
      untrustedContentHint: true,
      consequentialHint: !readOnly,
    },
    async execute(input, context) {
      const result = await invoke(tool.name, input && typeof input === 'object' ? input : {}, context?.signal)
      return toToolReturn(result)
    },
  }
}

export function isReadOnly(tool) {
  return !!tool?.annotations?.readOnlyHint
}

/**
 * The value a WebMCP `execute` hands back. The MCP result's content blocks
 * are what the agent needs; an MCP error result becomes a thrown error so
 * the agent sees a failure rather than a message that looks like data.
 */
export function toToolReturn(result) {
  if (!result || typeof result !== 'object') return { content: [{ type: 'text', text: String(result ?? '') }] }
  const content = Array.isArray(result.content) ? result.content : []
  if (result.isError) {
    const text = content.filter((c) => c?.type === 'text').map((c) => c.text).join('\n') || 'The tool reported an error.'
    throw new Error(text)
  }
  const out = { content }
  if (result.structuredContent !== undefined) out.structuredContent = result.structuredContent
  return out
}

/**
 * The sentence (and the hint under it) the Connect tab shows for the
 * current state. Pure, so the copy is tested once and wording stays in one
 * place.
 *
 * @param {object} s  { support, browser, enabled, status, tools, failed, error, mcpEnabled }
 */
export function describeWebMcpState(s) {
  const { browser = {} } = s
  const who = browser.name || 'This browser'
  if (s.support === 'unsupported') {
    return {
      tone: 'neutral',
      text: `${who} does not offer WebMCP.`,
      hint: `WebMCP is a Chromium origin trial (Chrome ${WEBMCP_FIRST_CHROME} to ${WEBMCP_TRIAL_LAST_CHROME}). Open Clio in Chrome or another Chromium browser to try it; everything else on this page works as before.`,
    }
  }
  if (s.support === 'insecure') {
    return {
      tone: 'neutral',
      text: 'WebMCP needs a secure page.',
      hint: 'This page is served over plain HTTP from another machine. Put a TLS proxy in front of Clio, or open it as http://localhost on the machine that runs it.',
    }
  }
  if (s.support === 'chromium-off') {
    const v = browser.version
    const old = v != null && v < WEBMCP_FIRST_CHROME
    return {
      tone: 'neutral',
      text: `${who}${v ? ` ${v}` : ''} is Chromium-based, but WebMCP is not turned on.`,
      hint: old
        ? `WebMCP arrived in Chrome ${WEBMCP_FIRST_CHROME}. Update the browser, then turn on ${browser.flagsScheme || 'chrome'}://${WEBMCP_FLAG} and relaunch.`
        : `Turn on ${browser.flagsScheme || 'chrome'}://${WEBMCP_FLAG} and relaunch, or register this site for the origin trial.`,
    }
  }
  if (s.mcpEnabled === false) {
    return { tone: 'neutral', text: 'The MCP server is turned off, so there are no tools to offer.', hint: '' }
  }
  if (!s.enabled || s.status === 'off') {
    return { tone: 'neutral', text: 'Turned off. Agents in this browser cannot see Clio’s tools.', hint: '' }
  }
  if (s.status === 'error') {
    return { tone: 'error', text: `Could not offer the tools: ${s.error || 'unknown error'}.`, hint: '' }
  }
  if (s.status === 'ready') {
    const n = s.tools?.length || 0
    const failed = s.failed?.length || 0
    return {
      tone: 'ok',
      text: `${n} read-only ${n === 1 ? 'tool' : 'tools'} offered to agents in this browser.`,
      hint: failed ? `${failed} could not be registered: ${s.failed.map((f) => f.name).join(', ')}.` : 'Each call lands in Try it above, like a call from any other client.',
    }
  }
  return { tone: 'neutral', text: 'Registering tools…', hint: '' }
}
