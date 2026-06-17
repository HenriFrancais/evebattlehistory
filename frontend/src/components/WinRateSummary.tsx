import type { BrListSummary } from '../api'
import { fmtIsk } from '../format'

interface Props {
  summary: BrListSummary
}

export function WinRateSummary({ summary }: Props) {
  const rate = summary.total > 0 ? (summary.win_rate * 100).toFixed(1) : '0.0'
  return (
    <div className="win-rate-summary" data-testid="win-rate-summary">
      <div className="stat-group">
        <div className="stat-label">Win Rate</div>
        <div className="win-rate-big">{rate}%</div>
      </div>
      <div className="stat-group">
        <div className="stat-label">Record</div>
        <div className="wl-row">
          <span className="badge badge-win">{summary.wins}W</span>
          <span className="badge badge-tie">{summary.ties}T</span>
          <span className="badge badge-loss">{summary.losses}L</span>
        </div>
      </div>
      <div className="stat-group">
        <div className="stat-label">Total ISK Killed</div>
        <div className="stat-value">{fmtIsk(summary.total_isk_destroyed)}</div>
      </div>
      <div className="stat-group">
        <div className="stat-label">Total ISK Lost</div>
        <div className="stat-value">{fmtIsk(summary.total_isk_lost)}</div>
      </div>
      <div className="stat-group">
        <div className="stat-label">BRs</div>
        <div className="stat-value">{summary.total}</div>
      </div>
    </div>
  )
}
