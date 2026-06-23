// Module-level promise cache for navigation-heavy resources.
//
// Two jobs:
//   1. Prefetch-on-hover — warm a BR's heavy panels (detail, fleet timeline,
//      composition) while the cursor is still travelling to the row, so the BR
//      opens already-populated instead of firing everything cold on click.
//   2. Share `me` — fetch the viewer once per session instead of on every page
//      (DevBar + overview + every BR open each used to issue their own /api/me).
//
// Entries live for the page's lifetime and are wiped on a full reload — which is
// the only moment identity changes (impersonation forces window.location.reload),
// so cached `me`/BR data can never bleed across users. Mutations within the app
// (refresh, side edits, title edit, delete) invalidate or force-refetch the
// affected keys, so the cache never serves data that an in-app action has stale-d.

import { api } from './api'

const store = new Map<string, Promise<unknown>>()

/** Return the in-flight/resolved promise for `key`, else start one via `fn`. */
function getOrFetch<T>(key: string, fn: () => Promise<T>): Promise<T> {
  const hit = store.get(key) as Promise<T> | undefined
  if (hit) return hit
  // Evict on rejection so a failed fetch doesn't pin an error forever.
  const p = fn().catch((e) => {
    store.delete(key)
    throw e
  })
  store.set(key, p)
  return p
}

/** Drop any cached entry for `key` and fetch fresh. */
function refetch<T>(key: string, fn: () => Promise<T>): Promise<T> {
  store.delete(key)
  return getOrFetch(key, fn)
}

/** Drop cached entries so the next read refetches (after an edit/delete). */
export function invalidate(...keys: string[]): void {
  for (const k of keys) store.delete(k)
}

/** Clear the entire cache. For tests — each one mocks its own API responses. */
export function resetCache(): void {
  store.clear()
}

const kBr = (id: string) => `br:${id}`
const kFleet = (id: string) => `fleet:${id}`
const kComp = (id: string) => `comp:${id}`

/** The viewer ("me"), fetched once per session. */
export const loadMe = () => getOrFetch('me', () => api.me())

/** BR detail. `force` bypasses the cache and refreshes it (use after a reload). */
export const loadBr = (id: string, force = false) =>
  (force ? refetch : getOrFetch)(kBr(id), () => api.getBr(id))

export const loadFleetTimeline = (id: string, force = false) =>
  (force ? refetch : getOrFetch)(kFleet(id), () => api.fleetTimeline(id))

export const loadComposition = (id: string, force = false) =>
  (force ? refetch : getOrFetch)(kComp(id), () => api.composition(id))

/** Drop every cached resource for one BR (after refresh / title edit / delete). */
export const invalidateBr = (id: string) => invalidate(kBr(id), kFleet(id), kComp(id))

/**
 * Warm a BR's heavy detail resources ahead of a click. Fire-and-forget: errors
 * are swallowed here (the real load on the detail page surfaces them).
 */
export function prefetchBr(id: string): void {
  void loadBr(id).catch(() => {})
  void loadFleetTimeline(id).catch(() => {})
  void loadComposition(id).catch(() => {})
}
