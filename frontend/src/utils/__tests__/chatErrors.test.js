import { describe, expect, it } from 'vitest'
import { presentChatError } from '../chatErrors'

describe('presentChatError', () => {
  it('summarizes provider failures and keeps the diagnostic available', () => {
    expect(presentChatError('Chat failed: Error code: 400 - invalid tools')).toEqual({
      message: "The AI provider couldn't complete this request. Check the selected model or endpoint, then retry.",
      detail: 'Error code: 400 - invalid tools',
    })
  })

  it('leaves application and network messages unchanged', () => {
    expect(presentChatError('Rate limit reached')).toEqual({
      message: 'Rate limit reached',
      detail: '',
    })
  })

  it('provides a fallback for empty errors', () => {
    expect(presentChatError('')).toEqual({
      message: 'Chat failed. Please try again.',
      detail: '',
    })
  })
})
