import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../../utils/http', () => ({ default: { get: vi.fn() } }))

import http from '../../utils/http'
import EnvironmentSettings from '../EnvironmentSettings.vue'

beforeEach(() => vi.clearAllMocks())

describe('Set by environment', () => {
  it('lists the environment and says where Settings wins', async () => {
    http.get.mockResolvedValue({
      data: {
        environment: [
          { name: 'AI_API_KEY', value: '(set)', overridden_in_settings: false },
          { name: 'CHUNK_SIZE', value: 1200, overridden_in_settings: true },
        ],
        overridden: [{ name: 'CHUNK_SIZE', field: 'chunk_size', environment: 900, saved: 1200 }],
      },
    })
    const w = mount(EnvironmentSettings)
    await flushPromises()

    expect(http.get).toHaveBeenCalledWith('/api/system/settings-sources')
    expect(w.get('[data-testid="environment-overridden"]').text())
      .toContain('CHUNK_SIZE: the environment says 900, Settings has 1200, and Settings wins.')
    expect(w.text()).toContain('2 settings from the environment')
    expect(w.text()).toContain('AI_API_KEY')
    expect(w.text()).toContain('(set)')
  })

  it('shows nothing to someone who is not an admin', async () => {
    http.get.mockRejectedValue(Object.assign(new Error('Forbidden'), { status: 403 }))
    const w = mount(EnvironmentSettings)
    await flushPromises()

    expect(w.find('[data-testid="environment-settings"]').exists()).toBe(false)
  })
})
