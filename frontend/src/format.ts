/** Shared formatting utilities. */

export function fmtIsk(v: number): string {
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  return `${v.toFixed(0)}`
}

/**
 * Coerce Date | epoch-seconds | ISO string to a Date. Backend datetimes are
 * naive UTC (SQLite); a string with a time component but no timezone marker
 * must be read as UTC — not the browser's local zone — so it renders in UTC
 * regardless of where the viewer is. (Same rule as isoToEpoch.)
 */
function toDate(x: Date | number | string): Date {
  if (x instanceof Date) return x
  if (typeof x === 'number') return new Date(x * 1000)
  const hasTz = /[zZ]$|[+-]\d\d:?\d\d$/.test(x)
  const hasTime = x.includes('T')
  return new Date(hasTime && !hasTz ? `${x}Z` : x)
}

/** ISO calendar date in UTC: YYYY-MM-DD. */
export function fmtDate(x: Date | number | string): string {
  return toDate(x).toISOString().slice(0, 10)
}

/**
 * Epoch seconds for a backend ISO timestamp. Backend datetimes are naive UTC
 * (SQLite); a string without a timezone marker must be read as UTC — not the
 * browser's local zone — so it lines up with epoch-second axes.
 */
export function isoToEpoch(iso: string): number {
  const hasTz = /[zZ]$|[+-]\d\d:?\d\d$/.test(iso)
  return Date.parse(hasTz ? iso : `${iso}Z`) / 1000
}

/** 24-hour UTC time: HH:MM, or HH:MM:SS when withSeconds. */
export function fmtTime(x: Date | number | string, withSeconds = false): string {
  return toDate(x).toISOString().slice(11, withSeconds ? 19 : 16)
}

/** YYYY-MM-DD HH:MM in UTC. */
export function fmtDateTime(x: Date | number | string): string {
  const iso = toDate(x).toISOString()
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}`
}

/** Compact magnitude: 1.5M / 44k / integer, dropping a trailing .0 (preserves sign). */
export function fmtCompact(n: number): string {
  const a = Math.abs(n)
  if (a >= 1e6) return `${(n / 1e6).toFixed(1).replace(/\.0$/, '')}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1).replace(/\.0$/, '')}k`
  return `${Math.round(n)}`
}
