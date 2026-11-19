import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

vi.mock('../../utils/answerEvidence', async (importOriginal) => {
  const real = await importOriginal()
  return { ...real, renderEvidenceMarkdown: vi.fn(real.renderEvidenceMarkdown) }
})
import { renderEvidenceMarkdown } from '../../utils/answerEvidence'
import AnswerEvidence from '../AnswerEvidence.vue'

// C1: a streamed answer arrives in many small pieces; the whole answer is
// rendered a few times a second, not once per piece.
describe('rendering a streaming answer', () => {
  afterEach(() => vi.useRealTimers())

  it('renders at most every 150 ms while text arrives, and at once when it ends', async () => {
    vi.useFakeTimers()
    const w = mount(AnswerEvidence, { props: { content: 'Seven', streaming: true, answer: true } })
    renderEvidenceMarkdown.mockClear()

    for (let i = 0; i < 20; i++) await w.setProps({ content: `Seven${' years'.repeat(i + 1)}` })
    expect(renderEvidenceMarkdown).toHaveBeenCalledTimes(0)
    vi.advanceTimersByTime(150)
    expect(renderEvidenceMarkdown).toHaveBeenCalledTimes(1)

    await w.setProps({ content: 'Seven years, final.', streaming: false })
    expect(renderEvidenceMarkdown).toHaveBeenCalledTimes(2)
    expect(w.text()).toContain('Seven years, final.')
  })
})
