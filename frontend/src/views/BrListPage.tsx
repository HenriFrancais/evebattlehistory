import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type BrListResponse, type MeResponse, api } from '../api'
import { BrCard } from '../components/BrCard'
import { WinRateSummary } from '../components/WinRateSummary'

export function BrListPage() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [data, setData] = useState<BrListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.me(), api.listBrs()]).then(
      ([m, d]) => { if (!cancelled) { setMe(m); setData(d) } },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [])

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!data || !me) return <div className="page"><p className="dim">Loading…</p></div>

  // Sort newest battle first
  const sorted = [...data.brs].sort((a, b) => {
    const ta = a.battle_at ?? a.created_at
    const tb = b.battle_at ?? b.created_at
    return tb.localeCompare(ta)
  })

  return (
    <div className="page">
      <div className="page-header">
        <h1 style={{ margin: 0 }}>Battle Report Timeline</h1>
        {me.can_create_br && (
          <Link to="/brs/new" className="btn btn-primary" data-testid="new-br-btn">
            + New Battle Report
          </Link>
        )}
      </div>
      <WinRateSummary summary={data.summary} />
      {sorted.length === 0 && <p className="dim">No battle reports yet.</p>}
      {sorted.map((br) => <BrCard key={br.br_id} br={br} />)}
    </div>
  )
}
