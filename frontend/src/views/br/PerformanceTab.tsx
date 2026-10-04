// Performance tab: fleet broadcasts and the per-character performance derived from them.
import type { BroadcastMetrics as BroadcastMetricsData } from '../../api'
import { BroadcastMetrics } from '../../components/BroadcastMetrics'
import { PerformancePanel } from '../../components/PerformancePanel'

interface Props {
  brId: string
  canManage: boolean
  reloadKey: number
  broadcastKey: number
  /** A broadcast log was uploaded or deleted. */
  onBroadcastChange: () => void
  onBroadcastLoaded: (m: BroadcastMetricsData) => void
}

export function PerformanceTab({ brId, canManage, reloadKey, broadcastKey, onBroadcastChange, onBroadcastLoaded }: Props) {
  return (
    <div className="tab-stack" data-testid="performance-tab">
      <section data-testid="broadcast-section" className="panel">
        <h2>Fleet broadcasts</h2>
        <BroadcastMetrics
          brId={brId}
          canManage={canManage}
          reloadKey={reloadKey}
          onChange={onBroadcastChange}
          onLoaded={onBroadcastLoaded}
        />
      </section>
      <section data-testid="performance-section" className="panel">
        <h2>Per-character performance</h2>
        <PerformancePanel brId={brId} reloadKey={reloadKey + broadcastKey} />
      </section>
    </div>
  )
}
