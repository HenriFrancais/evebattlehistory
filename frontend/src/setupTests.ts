import '@testing-library/jest-dom'
import { beforeEach } from 'vitest'

// The module-level fetch cache (cache.ts) persists for a page's lifetime in the
// app, but must not leak across tests — each test mocks its own API responses.
// Import lazily inside the hook (not at top level): a static import here would
// pull in the real ./api before each test file's vi.mock('../api') is registered,
// pinning the unmocked api into the cache and causing real network calls.
beforeEach(async () => {
  const { resetCache } = await import('./cache')
  resetCache()
})
