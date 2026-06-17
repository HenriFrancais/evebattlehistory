import { Link } from 'react-router-dom'
import type { BrSummary } from '../api'
import { fmtIsk } from '../format'

interface Props {
  br: BrSummary
}

function ResultBadge({ result }: { result: string | null }) {
  if (!result) return <span className="badge badge-pending">Pending</span>
  const cls = result === 'win' ? 'badge-win' : result === 'loss' ? 'badge-loss' : 'badge-tie'
  return <span className={`badge ${cls}`} data-testid="result-badge">{result}</span>
}

export function BrCard({ br }: Props) {
  const date = br.battle_at
    ? new Date(br.battle_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
    : new Date(br.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })

  return (
    <div className="br-card" data-testid="br-card">
      <div className="br-card-title">
        <Link to={`/brs/${br.br_id}`}>{br.title ?? `BR ${br.br_id}`}</Link>
      </div>
      <div className="br-card-meta">
        <span className="badge badge-source">{br.source}</span>
        {br.source_url && (
          <a href={br.source_url} target="_top" rel="noopener noreferrer">View source ↗</a>
        )}
        <span>{date}</span>
        <ResultBadge result={br.result} />
        {br.isk_efficiency != null && (
          <span title="ISK efficiency">{(br.isk_efficiency * 100).toFixed(1)}% eff</span>
        )}
        <span title="ISK killed">{fmtIsk(br.our_isk_destroyed)} killed</span>
        <span title="ISK lost">{fmtIsk(br.our_isk_lost)} lost</span>
        <span data-testid="fight-count">{br.fight_count} fight{br.fight_count !== 1 ? 's' : ''}</span>
        {br.status !== 'ready' && (
          <span className="dim">({br.status} {br.progress_pct}%)</span>
        )}
      </div>
    </div>
  )
}
