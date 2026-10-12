import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

import http from '../../utils/http'
import ReviewBell from '../ReviewBell.vue'
import { useReviewStore } from '../../stores/reviewStore'
import { useUserStore } from '../../stores/userStore'

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

const mountBell = (variant = 'header') => mount(ReviewBell, { props: { variant } })

describe('review notifications', () => {
  it('renders nothing for people who cannot act on the queue', () => {
    const wrapper = mountBell()
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('shows a badge with held, injection and access-request counts combined', async () => {
    const user = useUserStore()
    user.adminConsole = true
    user.pendingRegistrations = 1
    const review = useReviewStore()
    review.setSummary({ pending: 3, held: 1, injection: 2 })

    const wrapper = mountBell()
    expect(wrapper.get('[data-testid="review-badge"]').text()).toBe('4')
    expect(wrapper.get('button').attributes('aria-label')).toContain('1 held · 2 injection · 1 access request')
    // Something is held from search, so the badge is the urgent colour
    expect(wrapper.get('[data-testid="review-badge"]').classes()).toContain('bg-error')
  })

  it('asks the dropdown row to open the right part of Admin', async () => {
    const user = useUserStore()
    user.adminConsole = true
    user.pendingRegistrations = 2
    useReviewStore().setSummary({ pending: 1, injection: 1 })

    const wrapper = mountBell()
    const rows = wrapper.findAll('ul button')
    expect(rows.map((r) => r.text())).toEqual(['Prompt-injection warnings1', 'Access requests2'])
    await rows[1].trigger('click')
    expect(wrapper.emitted('open')[0]).toEqual(['access'])
  })

  it('says so when the queue is clear, in both placements', () => {
    useUserStore().adminConsole = true
    const header = mountBell()
    expect(header.find('[data-testid="review-badge"]').exists()).toBe(false)
    expect(header.text()).toContain('Nothing is waiting for you')
    expect(mountBell('footer').text()).toContain('Review clear')
  })

  it('footer indicator opens the queue', async () => {
    useUserStore().adminConsole = true
    useReviewStore().setSummary({ pending: 5, flagged: 5 })
    const wrapper = mountBell('footer')
    expect(wrapper.text()).toContain('5 to review')
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('open')[0]).toEqual(['review'])
  })
})

describe('review store', () => {
  it('only polls for admins and keeps the last counts when the server errors', async () => {
    const review = useReviewStore()
    await review.refresh()
    expect(http.get).not.toHaveBeenCalled()

    useUserStore().adminConsole = true
    http.get.mockResolvedValueOnce({ data: { pending: 2, held: 1 } })
    await review.refresh()
    expect(review.summary.pending).toBe(2)

    http.get.mockRejectedValueOnce(new Error('boom'))
    await review.refresh()
    expect(review.summary.pending).toBe(2)
    expect(review.failed).toBe(true)
  })
})
