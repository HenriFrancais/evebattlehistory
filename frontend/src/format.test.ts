import { describe, expect, it } from 'vitest'
import { fmtDate, fmtTime, fmtDateTime, isoToEpoch } from './format'

describe('isoToEpoch', () => {
  it('reads a naive (no-tz) timestamp as UTC, not local time', () => {
    // 2026-06-14T20:18:58 UTC = 1781468338. Must not depend on the host tz.
    expect(isoToEpoch('2026-06-14T20:18:58')).toBe(1781468338)
  })

  it('respects an explicit Z or offset when present', () => {
    expect(isoToEpoch('2026-06-14T20:18:58Z')).toBe(1781468338)
    expect(isoToEpoch('2026-06-14T21:18:58+01:00')).toBe(1781468338)
  })
})

// 2026-06-16T19:21:14Z
const EPOCH_S = 1781897514 / 1 // overwritten below to a known instant
const ISO = '2026-06-16T19:21:14Z'
const EPOCH = Date.parse(ISO) / 1000

describe('date/time formatting (UTC)', () => {
  it('fmtDate → YYYY-MM-DD from ISO string', () => {
    expect(fmtDate(ISO)).toBe('2026-06-16')
  })
  it('fmtDate from epoch seconds', () => {
    expect(fmtDate(EPOCH)).toBe('2026-06-16')
  })
  it('fmtTime → HH:MM 24h UTC (no seconds by default)', () => {
    expect(fmtTime(ISO)).toBe('19:21')
  })
  it('fmtTime with seconds', () => {
    expect(fmtTime(EPOCH, true)).toBe('19:21:14')
  })
  it('fmtDateTime → YYYY-MM-DD HH:MM UTC', () => {
    expect(fmtDateTime(new Date(ISO))).toBe('2026-06-16 19:21')
  })
  void EPOCH_S
})

import { fmtCompact } from './format'

describe('fmtCompact', () => {
  it('formats millions, thousands, and small values', () => {
    expect(fmtCompact(1_500_000)).toBe('1.5M')
    expect(fmtCompact(9200)).toBe('9.2k')
    expect(fmtCompact(42)).toBe('42')
  })
})
