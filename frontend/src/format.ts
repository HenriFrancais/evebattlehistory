/** Shared formatting utilities. */

export function fmtIsk(v: number): string {
  if (v >= 1e12) return `${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M`
  return `${v.toFixed(0)}`
}

/** Coerce Date | epoch-seconds | ISO string to a Date. */
function toDate(x: Date | number | string): Date {
  if (x instanceof Date) return x
  if (typeof x === 'number') return new Date(x * 1000)
  return new Date(x)
}

/** ISO calendar date in UTC: YYYY-MM-DD. */
export function fmtDate(x: Date | number | string): string {
  return toDate(x).toISOString().slice(0, 10)
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

/** Compact magnitude: 1.5M / 1.5k / integer (preserves sign). */
export function fmtCompact(n: number): string {
  const a = Math.abs(n)
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`
  return `${Math.round(n)}`
}
