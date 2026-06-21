// BR-level damage leaderboard: top damage dealers across all kills in the BR.
import { useEffect, useState } from 'react'
import type { BrDamageLeaderboard } from '../api'
import { api } from '../api'
import { fmtCompact } from '../format'

interface Props {
  brId: string
}

export function DamageLeaderboard({ brId }: Props) {
  const [data, setData] = useState<BrDamageLeaderboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.damageLeaderboard(brId).then(
      (d) => { if (!cancelled) { setData(d); setLoading(false) } },
      (e: unknown) => { if (!cancelled) { setError(String((e as Error)?.message ?? e)); setLoading(false) } },
    )
    return () => { cancelled = true }
  }, [brId])

  if (loading) return <p className="dim">Loading…</p>
  if (error) return <p className="error-text">{error}</p>
  if (!data || data.rows.length === 0) return <p className="dim">No damage data.</p>

  return (
    <div data-testid="damage-leaderboard">
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
        <thead>
          <tr>
            <th style={{ textAlign: 'left', padding: '0.2rem 0.3rem', color: 'var(--text-dim)', fontWeight: 500 }}>#</th>
            <th style={{ textAlign: 'left', padding: '0.2rem 0.3rem', color: 'var(--text-dim)', fontWeight: 500 }}>Pilot</th>
            <th style={{ textAlign: 'right', padding: '0.2rem 0.3rem', color: 'var(--text-dim)', fontWeight: 500 }}>Damage</th>
            <th style={{ textAlign: 'right', padding: '0.2rem 0.3rem', color: 'var(--text-dim)', fontWeight: 500 }}>Share</th>
          </tr>
        </thead>
        <tbody>
          {data.rows.map((row, idx) => (
            <tr key={row.character_id ?? `anon-${idx}`} data-testid="dmg-lb-row">
              <td style={{ padding: '0.2rem 0.3rem', color: 'var(--text-dim)' }}>{idx + 1}</td>
              <td style={{ padding: '0.2rem 0.3rem' }} title={row.character_name ?? undefined}>
                {row.character_name ?? '(unknown)'}
              </td>
              <td style={{ padding: '0.2rem 0.3rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {fmtCompact(row.damage_done)}
              </td>
              <td style={{ padding: '0.2rem 0.3rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                {(row.share * 100).toFixed(1)}%
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="dim" style={{ fontSize: '0.75rem', marginTop: '0.4rem', textAlign: 'right' }}>
        Total: {fmtCompact(data.total_attributed)} attributed damage
      </div>
    </div>
  )
}
