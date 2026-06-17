// Pure transform: FleetTimeline API response → uPlot data + series config + kill markers.
// No uPlot import — unit-tested in jsdom without canvas.

import type { FleetTimeline, KillEvent } from './api'

export interface FleetSeriesConfig {
  key: string
  label: string
  stroke: string
  fill?: string
  toggleable: boolean  // EWAR and cap_warfare series are toggleable
}

export interface FleetUplotData {
  data: (number | null)[][]
  seriesConfig: FleetSeriesConfig[]
  kills: KillEvent[]  // for rendering kill markers
}

// Fixed order: dps_out, remote_rep, ewar, cap_warfare
const SERIES_ORDER = ['dps_out', 'remote_rep', 'ewar', 'cap_warfare']
const SERIES_LABELS: Record<string, string> = {
  dps_out: 'Fleet DPS',
  remote_rep: 'Remote Rep',
  ewar: 'EWAR (count)',
  cap_warfare: 'Cap Warfare',
}
const SERIES_STROKES: Record<string, string> = {
  dps_out: '#ff7043',     // warm orange/red for damage out
  remote_rep: '#42a5f5',  // cool blue for logi
  ewar: '#ab47bc',        // purple for EWAR
  cap_warfare: '#ffca28', // yellow for cap
}
const TOGGLEABLE = new Set(['ewar', 'cap_warfare'])

export function toFleetUplotData(fleet: FleetTimeline): FleetUplotData {
  const xs = fleet.x
  const data: (number | null)[][] = [xs]
  const seriesConfig: FleetSeriesConfig[] = []

  // Build a map of series key → values for fast lookup
  const seriesMap = new Map<string, (number | null)[]>()
  for (const s of fleet.series) {
    seriesMap.set(s.key, s.values)
  }

  for (const key of SERIES_ORDER) {
    const values = seriesMap.get(key) ?? new Array(xs.length).fill(null)
    data.push(values)
    seriesConfig.push({
      key,
      label: SERIES_LABELS[key] ?? key,
      stroke: SERIES_STROKES[key] ?? '#8893a7',
      toggleable: TOGGLEABLE.has(key),
    })
  }

  return { data, seriesConfig, kills: fleet.kills }
}
