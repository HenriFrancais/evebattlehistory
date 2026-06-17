// Pure transform: CharacterTimeline API response → uPlot data + series config.
// No uPlot import — this module is unit-tested in jsdom without canvas.

import type { CharacterTimeline, TimelineSeriesItem } from './api'

export interface SeriesConfig {
  key: string
  label: string
  stroke: string
  direction: 'in' | 'out' | null
}

export interface UplotData {
  data: (number | null)[][]
  seriesConfig: SeriesConfig[]
}

// Colour palette: warm = outgoing (damage out, regen out), cool = incoming
// Uses a small rotating palette per direction so multiple series of the same
// direction get distinguishable colours.
const WARM = ['#ff7043', '#ffa726', '#ffca28', '#ef5350']
const COOL = ['#42a5f5', '#26c6da', '#66bb6a', '#ab47bc']
const NEUTRAL = ['#8893a7', '#b0bec5', '#cfd8dc']

function pickColour(item: TimelineSeriesItem, idxInDirection: number): string {
  if (item.direction === 'out') return WARM[idxInDirection % WARM.length]
  if (item.direction === 'in') return COOL[idxInDirection % COOL.length]
  return NEUTRAL[idxInDirection % NEUTRAL.length]
}

function makeLabel(item: TimelineSeriesItem): string {
  const type = item.effect_type ?? 'unknown'
  const dir = item.direction
  if (dir === 'out') return `${type} out`
  if (dir === 'in') return `${type} in`
  return type
}

export function toUplotData(timeline: CharacterTimeline): UplotData {
  const xs = timeline.x

  const outCount: Record<string, number> = { out: 0, in: 0, null: 0 }

  const data: (number | null)[][] = [xs]
  const seriesConfig: SeriesConfig[] = []

  for (const s of timeline.series) {
    const dirKey = s.direction ?? 'null'
    const idxInDir = outCount[dirKey] ?? 0
    outCount[dirKey] = idxInDir + 1

    data.push(s.values)
    seriesConfig.push({
      key: s.key,
      label: makeLabel(s),
      stroke: pickColour(s, idxInDir),
      direction: s.direction === 'in' ? 'in' : s.direction === 'out' ? 'out' : null,
    })
  }

  return { data, seriesConfig }
}
