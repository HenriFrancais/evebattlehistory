// Fleet section: composes the fleet graph with the snapshot breakdown.
// Owns the selected time range so a two-click selection on the graph reveals the
// side panel; a later task lifts this state into the page for separate columns.
import { useState } from 'react'
import { FleetGraph } from './FleetGraph'
import { SnapshotPanel } from './SnapshotPanel'

interface Props {
  brId: string
  /** Bump to force a re-fetch (e.g. after side overrides change). */
  reloadKey?: number
}

export function FleetSection({ brId, reloadKey }: Props) {
  const [range, setRange] = useState<{ from: number; to: number } | null>(null)
  return (
    <div className="fleet-layout">
      <div className="fleet-main">
        <FleetGraph brId={brId} reloadKey={reloadKey} selectedRange={range} onSelectRange={setRange} />
      </div>
      {range != null && (
        <div className="fleet-side"><SnapshotPanel brId={brId} range={range} /></div>
      )}
    </div>
  )
}
