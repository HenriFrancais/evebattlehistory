/**
 * Regression tests for api.ts fetcher URLs.
 *
 * Component tests mock `api.*` directly, so they cannot catch wrong URL strings.
 * These tests spy on the underlying `fetch` global (which jsonFetch calls) and
 * assert that each fetcher builds the EXACT URL the FastAPI backend registers.
 *
 * Real backend route decorators (app/api/brs.py):
 *   GET /api/brs/{br_id}/losses/{killmail_id}/damage
 *   GET /api/brs/{br_id}/losses/{killmail_id}/items
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

// Minimal Response mock — jsonFetch only calls res.ok and res.json().
function mockOkResponse(body: unknown): Response {
  return {
    ok: true,
    json: () => Promise.resolve(body),
  } as unknown as Response
}

describe('api fetcher URLs', () => {
  let fetchSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchSpy = vi.fn().mockResolvedValue(mockOkResponse({}))
    vi.stubGlobal('fetch', fetchSpy)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('lossDamage calls /api/brs/{brId}/losses/{kmId}/damage (not /kills/)', async () => {
    await api.lossDamage('br-abc', 42)
    const url: string = fetchSpy.mock.calls[0][0]
    expect(url).toContain('/brs/br-abc/losses/42/damage')
    expect(url).not.toContain('/kills/')
  })

  it('lossItems calls /api/brs/{brId}/losses/{kmId}/items (not /kills/)', async () => {
    await api.lossItems('br-abc', 42)
    const url: string = fetchSpy.mock.calls[0][0]
    expect(url).toContain('/brs/br-abc/losses/42/items')
    expect(url).not.toContain('/kills/')
  })
})
