// Fleet section: composes the fleet graph with the moment-detail breakdown.
// Owns the clicked-moment time so a click on the graph reveals the side panel;
// Task 8 lifts this state into the page to place the two in separate columns.
import { useState } from 'react'
import { FleetGraph } from './FleetGraph'
import { MomentDetailPanel } from './MomentDetailPanel'

interface Props {
  brId: string
  /** Bump to force a re-fetch (e.g. after side overrides change). */
  reloadKey?: number
}

export function FleetSection({ brId, reloadKey }: Props) {
  const [selectedTs, setSelectedTs] = useState<number | null>(null)
  return (
    <div className="fleet-layout">
      <div className="fleet-main">
        <FleetGraph brId={brId} reloadKey={reloadKey} selectedTs={selectedTs} onSelectTs={setSelectedTs} />
      </div>
      {selectedTs != null && (
        <div className="fleet-side">
          <MomentDetailPanel brId={brId} at={selectedTs} />
        </div>
      )}
    </div>
  )
}
