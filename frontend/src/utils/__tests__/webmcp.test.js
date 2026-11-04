import { describe, expect, it, vi } from 'vitest'
import {
  getModelContext,
  describeBrowser,
  supportState,
  toWebMcpTool,
  toToolReturn,
  describeWebMcpState,
  WEBMCP_FIRST_CHROME,
} from '../webmcp'

const CHROME_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36'
const EDGE_UA = `${CHROME_UA} Edg/157.0.0.0`
const FIREFOX_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0'
const SAFARI_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15'

const fakeWindow = ({ document = {}, navigator = {}, isSecureContext = true } = {}) => ({ document, navigator, isSecureContext })

describe('getModelContext', () => {
  it('prefers document.modelContext over the deprecated navigator location', () => {
    const onDocument = { registerTool() {} }
    const onNavigator = { registerTool() {} }
    expect(getModelContext(fakeWindow({ document: { modelContext: onDocument }, navigator: { modelContext: onNavigator } }))).toBe(onDocument)
    expect(getModelContext(fakeWindow({ navigator: { modelContext: onNavigator } }))).toBe(onNavigator)
  })

  it('is null when the browser has no WebMCP', () => {
    expect(getModelContext(fakeWindow({ navigator: { userAgent: CHROME_UA } }))).toBeNull()
  })
})

describe('describeBrowser', () => {
  it('reads Chromium descent and the product name from client hints', () => {
    const navigator = {
      userAgent: EDGE_UA,
      userAgentData: { brands: [{ brand: 'Not;A=Brand', version: '99' }, { brand: 'Microsoft Edge', version: '157' }, { brand: 'Chromium', version: '157' }] },
    }
    expect(describeBrowser(fakeWindow({ navigator }))).toEqual({ chromium: true, name: 'Microsoft Edge', version: 157, flagsScheme: 'edge' })
  })

  it('falls back to the user agent string', () => {
    expect(describeBrowser(fakeWindow({ navigator: { userAgent: CHROME_UA } }))).toMatchObject({ chromium: true, name: 'Chrome', version: 157 })
    expect(describeBrowser(fakeWindow({ navigator: { userAgent: FIREFOX_UA } }))).toMatchObject({ chromium: false, name: 'Firefox', version: null })
    expect(describeBrowser(fakeWindow({ navigator: { userAgent: SAFARI_UA } }))).toMatchObject({ chromium: false, name: 'Safari' })
  })

  it('names an unknown runtime neutrally', () => {
    expect(describeBrowser(fakeWindow()).name).toBe('This browser')
  })
})

describe('supportState', () => {
  it('is decided by the feature, not the brand', () => {
    const chromeWithApi = fakeWindow({ document: { modelContext: {} }, navigator: { userAgent: CHROME_UA } })
    const chromeWithout = fakeWindow({ navigator: { userAgent: CHROME_UA } })
    const firefox = fakeWindow({ navigator: { userAgent: FIREFOX_UA } })
    expect(supportState(chromeWithApi)).toBe('available')
    expect(supportState(chromeWithout)).toBe('chromium-off')
    expect(supportState(firefox)).toBe('unsupported')
  })

  it('blames the missing secure context before the browser', () => {
    expect(supportState(fakeWindow({ navigator: { userAgent: CHROME_UA }, isSecureContext: false }))).toBe('insecure')
  })
})

describe('toWebMcpTool', () => {
  const mcpTool = {
    name: 'search_documents',
    title: 'Search documents',
    description: 'Find passages.',
    inputSchema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
    annotations: { readOnlyHint: true },
  }

  it('keeps the MCP name, schema and read-only hint, and marks the content untrusted', async () => {
    const invoke = vi.fn(async () => ({ content: [{ type: 'text', text: 'one passage' }] }))
    const tool = toWebMcpTool(mcpTool, invoke)
    expect(tool.name).toBe('search_documents')
    expect(tool.inputSchema).toBe(mcpTool.inputSchema)
    expect(tool.annotations).toEqual({ readOnlyHint: true, untrustedContentHint: true, consequentialHint: false })
    const signal = new AbortController().signal
    const result = await tool.execute({ query: 'late fee' }, { signal })
    expect(invoke).toHaveBeenCalledWith('search_documents', { query: 'late fee' }, signal)
    expect(result).toEqual({ content: [{ type: 'text', text: 'one passage' }] })
  })

  it('flags a writing tool as consequential', () => {
    const tool = toWebMcpTool({ name: 'write_document', description: '', annotations: { destructiveHint: false } }, vi.fn())
    expect(tool.annotations.readOnlyHint).toBe(false)
    expect(tool.annotations.consequentialHint).toBe(true)
    expect(tool.inputSchema).toEqual({ type: 'object', properties: {} })
  })

  it('passes an empty object when the agent sends no input', async () => {
    const invoke = vi.fn(async () => ({ content: [] }))
    await toWebMcpTool(mcpTool, invoke).execute(undefined)
    expect(invoke.mock.calls[0][1]).toEqual({})
  })
})

describe('toToolReturn', () => {
  it('turns an MCP error result into a thrown error', () => {
    expect(() => toToolReturn({ isError: true, content: [{ type: 'text', text: "Collection 'x' not found" }] })).toThrow("Collection 'x' not found")
  })

  it('keeps structured content beside the text blocks', () => {
    expect(toToolReturn({ content: [{ type: 'text', text: '{}' }], structuredContent: { total: 2 } }))
      .toEqual({ content: [{ type: 'text', text: '{}' }], structuredContent: { total: 2 } })
  })
})

describe('describeWebMcpState', () => {
  const browser = { chromium: true, name: 'Chrome', version: 157, flagsScheme: 'chrome' }

  it('tells a non-Chromium browser it is not supported, without blaming the person', () => {
    const s = describeWebMcpState({ support: 'unsupported', browser: { ...browser, chromium: false, name: 'Firefox' } })
    expect(s.text).toBe('Firefox does not offer WebMCP.')
    expect(s.hint).toContain(`Chrome ${WEBMCP_FIRST_CHROME}`)
  })

  it('points a Chromium browser at the flag, or at an update when it is too old', () => {
    expect(describeWebMcpState({ support: 'chromium-off', browser }).hint).toContain('chrome://flags/#enable-webmcp-testing')
    const old = describeWebMcpState({ support: 'chromium-off', browser: { ...browser, version: 140 } })
    expect(old.hint).toContain('Update the browser')
    const edge = describeWebMcpState({ support: 'chromium-off', browser: { ...browser, name: 'Microsoft Edge', flagsScheme: 'edge' } })
    expect(edge.text).toContain('Microsoft Edge 157')
    expect(edge.hint).toContain('edge://flags')
  })

  it('explains the secure-context requirement', () => {
    expect(describeWebMcpState({ support: 'insecure', browser }).text).toBe('WebMCP needs a secure page.')
  })

  it('reports the offered tools and any that failed', () => {
    const ready = describeWebMcpState({ support: 'available', browser, enabled: true, status: 'ready', tools: [{ name: 'a' }, { name: 'b' }], failed: [] })
    expect(ready.tone).toBe('ok')
    expect(ready.text).toBe('2 read-only tools offered to agents in this browser.')
    const partial = describeWebMcpState({ support: 'available', browser, enabled: true, status: 'ready', tools: [{ name: 'a' }], failed: [{ name: 'b' }] })
    expect(partial.text).toBe('1 read-only tool offered to agents in this browser.')
    expect(partial.hint).toContain('b')
  })

  it('says when the switch or the server is off', () => {
    expect(describeWebMcpState({ support: 'available', browser, enabled: false, status: 'off' }).text).toMatch(/^Turned off/)
    expect(describeWebMcpState({ support: 'available', browser, enabled: true, status: 'ready', mcpEnabled: false }).text).toContain('MCP server is turned off')
  })
})
