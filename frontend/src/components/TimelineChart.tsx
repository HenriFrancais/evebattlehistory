// The timeline chart: ONE large uPlot chart showing a single stat type (damage,
// reps, cap or EWAR). Outgoing is drawn above a zero baseline, incoming mirrored
// below. Series chips, smoothing, kill markers and broadcast ticks. Dragging on the
// chart zooms it, and the zoomed window IS the selected range: the parent owns it
// and the stats table follows. The canvas itself is not unit-tested; tests cover
// the controls and the empty states.
//
// What is drawn is owned by the parent: `fleet.series` for the whole fleet,
// `isolatedSeries` when one pilot is isolated, or one line per pilot (`compare`)
// when several are, so they can be read against each other.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { BroadcastRawItem, FleetSeriesItem, FleetTimeline, KillEvent } from '../api'
import { toBroadcastMarkers } from '../broadcasts'
import type { BroadcastMarker } from '../broadcasts'
import { useEntities } from '../entities'
import type { EntityIndex } from '../entities'
import type { FleetPanel, PanelSeries } from '../fleet'
import { clipToWindow, toFleetView } from '../fleet'
import { fmtCompact, isoToEpoch } from '../format'
import type { StatDirection, StatFamily } from '../pilotStats'
import {
  AXIS,
  BROADCAST_KIND_COLOR,
  BROADCAST_KIND_LABEL,
  GRID,
  KILL_FRIENDLY_LOSS,
  KILL_HOSTILE_LOSS,
  KILL_NEUTRAL,
  broadcastMarkersPlugin,
  compareHoverPlugin,
  fightEdgesPlugin,
  hexToRgba,
  hoverSummaryPlugin,
  killMarkersPlugin,
  zeroBaselinePlugin,
} from './chartPlugins'

/** One pilot drawn as their own line when several pilots are compared. */
export interface ComparePilot {
  characterId: number
  name: string
  color: string
  dash?: number[]
  /** This pilot's series alone, dense on the fleet timeline's x axis. */
  series: FleetSeriesItem[]
}

export interface TimeRange {
  from: number
  to: number
}

const NO_KILLS: KillEvent[] = [] // stable empty ref when markers are toggled off
const NO_RAW: BroadcastRawItem[] = []
const NO_FLAGS = new Map<number, string>()
const BROADCAST_KINDS = ['target', 'needs_shield', 'needs_armor', 'needs_capacitor', 'repair']

/** The fight window the x axis is pinned to: first→last loss, with a small buffer. */
export function fightWindow(fleet: FleetTimeline): TimeRange {
  const times: number[] = fleet.kills.map((k) => k.ts)
  for (const f of fleet.fights ?? []) {
    if (f.started_at) times.push(isoToEpoch(f.started_at))
    if (f.ended_at) times.push(isoToEpoch(f.ended_at))
  }
  if (times.length) {
    const lo = Math.min(...times)
    const hi = Math.max(...times)
    const buf = Math.min(Math.max((hi - lo) * 0.05, 15), 120)
    return { from: lo - buf, to: hi + buf }
  }
  const x = fleet.x
  return { from: x.length ? x[0] : 0, to: x.length ? x[x.length - 1] + (fleet.bucket_seconds || 5) : 1 }
}

// --- the canvas -------------------------------------------------------------

interface CanvasProps {
  panel: FleetPanel
  series: PanelSeries[]
  x: number[]
  window: TimeRange
  kills: KillEvent[]
  broadcastMarkers: BroadcastMarker[]
  flaggedDeaths: Map<number, string>
  dimUnless: Set<number> | null
  fleet: FleetTimeline
  height: number
  /** The zoom window (null = whole fight), read when the chart is (re)built. */
  rangeRef: { current: TimeRange | null }
  /** The user zoomed (drag) or reset (double-click): report the visible window. */
  onZoom: (r: TimeRange | null) => void
  /** Lets the parent move the zoom of the live chart without rebuilding it. */
  registerZoomer: (fn: (r: TimeRange | null) => void) => () => void
  showHoverSummary: boolean
  /** One line per pilot: hovering lists every pilot's value at that time. */
  compareMode: boolean
  entities: EntityIndex
}

function ChartCanvas({
  panel, series, x, window: win, kills, broadcastMarkers, flaggedDeaths, dimUnless, fleet,
  height, rangeRef, onZoom, registerZoomer, showHoverSummary, compareMode, entities,
}: CanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const fullMin = win.from
    const fullMax = win.to
    let settled = false

    // The x domain is bounded to the LOSS window: combat logs can start well before
    // and run long after the actual fight, so buckets outside it are clipped rather
    // than left to stretch the axis.
    const { xs, cols, sourceIdx } = clipToWindow(x, series.map((s) => s.values), fullMin, fullMax)
    const data: (number | null)[][] = [xs, ...cols]

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      legend: { show: false },
      scales: { x: { time: true }, y: {} },
      axes: [
        {
          stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID },
          // UTC HH:MM:SS ticks. uPlot x values are epoch seconds.
          values: (_u, splits) => splits.map((v) => new Date(v * 1000).toISOString().slice(11, 19)),
        },
        {
          stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID },
          size: 60,
          label: panel.unit, // HP / GJ / # as the y-axis title
          labelSize: 18,
          values: (_u, splits) => splits.map((v) => fmtCompact(Math.abs(v))),
        },
      ],
      series: [
        { label: 'Time' },
        ...series.map((s) => ({
          label: s.label,
          stroke: s.stroke,
          fill: s.noFill ? undefined : hexToRgba(s.stroke, 0.14),
          dash: s.dash,
          width: s.noFill ? 2 : 1.75,
          points: { show: false },
          spanGaps: false,
        })),
      ],
      hooks: {
        setScale: [
          (u, key) => {
            // uPlot reports its own initial auto-range (and the zoom re-applied to a
            // rebuilt chart) through this hook too; those are not user gestures.
            if (key !== 'x' || !settled) return
            const min = u.scales.x.min
            const max = u.scales.x.max
            if (min == null || max == null) return
            const full = Math.abs(min - fullMin) <= 1 && Math.abs(max - fullMax) <= 1
            const next = full ? null : { from: min, to: max }
            const cur = rangeRef.current
            const same = next == null || cur == null
              ? next === cur
              : Math.abs(next.from - cur.from) < 0.5 && Math.abs(next.to - cur.to) < 0.5
            // Only a change the user made is reported; the echo of a zoom the parent
            // applied (or of this chart being rebuilt) is not.
            if (!same) onZoom(next)
          },
        ],
      },
      plugins: [
        fightEdgesPlugin(fleet.fights ?? []),
        zeroBaselinePlugin(),
        ...(broadcastMarkers.length ? [broadcastMarkersPlugin(broadcastMarkers)] : []),
        killMarkersPlugin(kills, { flaggedDeaths, dimUnless, entities }),
        ...(showHoverSummary ? [hoverSummaryPlugin(panel.id, fleet.leaders ?? [], entities, sourceIdx)] : []),
        ...(compareMode ? [compareHoverPlugin(series, panel.unit)] : []),
      ],
    }

    // Drag zooms and double-click resets (uPlot's native gestures). The chart is
    // rebuilt on toggles, smoothing and stat type, so it re-applies the current zoom.
    const applyZoom = (r: TimeRange | null) => {
      const target = r ?? { from: fullMin, to: fullMax }
      const { min, max } = u.scales?.x ?? {}
      if (min != null && max != null
        && Math.abs(min - target.from) < 0.5 && Math.abs(max - target.to) < 0.5) return
      u.setScale?.('x', { min: target.from, max: target.to })
    }
    const u = new uPlot(opts, data as uPlot.AlignedData, el)
    if (rangeRef.current) applyZoom(rangeRef.current)
    const unregisterZoomer = registerZoomer(applyZoom)
    // uPlot commits scale changes in a microtask; listen for gestures only after the
    // build's own commits have gone through.
    const settle = setTimeout(() => { settled = true }, 0)
    const resize = () => u.setSize({ width: el.clientWidth || 800, height })
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null
    observer?.observe(el)
    window.addEventListener('resize', resize)
    return () => {
      window.removeEventListener('resize', resize)
      observer?.disconnect()
      clearTimeout(settle)
      unregisterZoomer()
      u.destroy()
    }
  }, [panel, series, x, win, kills, broadcastMarkers, flaggedDeaths, dimUnless, fleet, height,
    rangeRef, onZoom, registerZoomer, showHoverSummary, compareMode, entities])

  return <div className="timeline-chart" data-testid="timeline-canvas" ref={containerRef} />
}

/** " (smoothed over ±N s)" for a compared pilot, from the widest of their lines. */
function smoothNote(lines: PanelSeries[] | null, characterId: number): string {
  const spans = (lines ?? [])
    .filter((l) => l.key.startsWith(`${characterId}:`))
    .map((l) => l.smoothSeconds ?? 0)
  const widest = Math.max(0, ...spans)
  return widest ? ` (smoothed over ±${widest} s)` : ''
}

// --- chart + its controls ----------------------------------------------------

interface Props {
  /** Fleet timeline: x axis, kills, fights, leaders, and the fleet-wide series. */
  fleet: FleetTimeline
  family: StatFamily
  direction: StatDirection
  /** Sum of the isolated pilots' series; null draws the whole fleet. */
  isolatedSeries: FleetSeriesItem[] | null
  /** Two or more isolated pilots, each drawn as their own line; null otherwise. */
  compare?: ComparePilot[] | null
  /** Untick a pilot from the compare legend. */
  onTogglePilot?: (characterId: number) => void
  /** The isolated pilots; other pilots' loss markers are dimmed. */
  isolatedIds: Set<number>
  /** The zoom window; null = the whole fight. Owned by the parent. */
  selectedRange: TimeRange | null
  /** The user zoomed the chart (drag) or reset it (double-click). */
  onSelectRange: (r: TimeRange | null) => void
  /** Series chips switched off (family ids from fleet.ts), owned by the parent. */
  hiddenSeries: Set<string>
  onToggleSeries: (key: string) => void
  broadcasts?: BroadcastRawItem[]
  flaggedDeaths?: Map<number, string>
  height?: number
}

export function TimelineChart({
  fleet, family, direction, isolatedSeries, compare = null, onTogglePilot, isolatedIds,
  selectedRange, onSelectRange,
  hiddenSeries, onToggleSeries, broadcasts = NO_RAW, flaggedDeaths = NO_FLAGS, height = 420,
}: Props) {
  const entities = useEntities()
  const [smooth, setSmooth] = useState(true)
  const [smoothScale, setSmoothScale] = useState(1)
  const [showKills, setShowKills] = useState(true)
  const [enabledKinds, setEnabledKinds] = useState<Set<string>>(new Set())
  // The zoom window mirrored in a ref: a rebuilt chart reads it, and the scale hook
  // compares against it to tell a user gesture from the echo of an applied zoom.
  const rangeRef = useRef<TimeRange | null>(selectedRange)
  const zoomersRef = useRef<Set<(r: TimeRange | null) => void>>(new Set())

  const registerZoomer = useCallback((fn: (r: TimeRange | null) => void) => {
    zoomersRef.current.add(fn)
    return () => { zoomersRef.current.delete(fn) }
  }, [])
  const handleZoom = useCallback((r: TimeRange | null) => {
    rangeRef.current = r
    onSelectRange(r)
  }, [onSelectRange])

  // A range set from outside the chart (time fields, reset) moves the live chart.
  useEffect(() => {
    rangeRef.current = selectedRange
    zoomersRef.current.forEach((fn) => fn(selectedRange))
  }, [selectedRange])

  const win = useMemo(() => fightWindow(fleet), [fleet])
  const isolated = isolatedSeries != null

  const panel: FleetPanel | null = useMemo(() => {
    const source = isolatedSeries ? { ...fleet, series: isolatedSeries } : fleet
    const view = toFleetView(source, { smooth, smoothScale, bucketSeconds: fleet.bucket_seconds })
    const found = view.panels.find((p) => p.id === family)
    if (!found) return null
    if (found.id === 'ewar') return found // applications per bucket: a count, not a rate
    // Amounts are drawn per second so the chart reads on the same scale as the
    // table's Peak /s and Avg /s columns.
    const per = Math.max(1, fleet.bucket_seconds)
    return {
      ...found,
      unit: `${found.unit}/s`,
      series: found.series.map((sr) => ({
        ...sr, values: sr.values.map((v) => (v == null ? null : v / per)),
      })),
    }
  }, [fleet, isolatedSeries, family, smooth, smoothScale])

  // Chips offered for this stat type and direction; drawn = offered minus hidden.
  const offered = useMemo(
    () => (panel?.series ?? []).filter((s) => direction === 'both' || s.direction === direction),
    [panel, direction],
  )
  // Comparing pilots: one line per pilot and direction, the sum of the series chips
  // that are switched on, in the pilot's colour.
  const compareLines = useMemo(() => {
    if (!compare) return null
    const per = family === 'ewar' ? 1 : Math.max(1, fleet.bucket_seconds)
    const lines: PanelSeries[] = []
    for (const c of compare) {
      const view = toFleetView(
        { ...fleet, series: c.series }, { smooth, smoothScale, bucketSeconds: fleet.bucket_seconds },
      )
      const own = view.panels.find((p) => p.id === family)?.series ?? []
      for (const dir of ['out', 'in'] as const) {
        if (direction !== 'both' && direction !== dir) continue
        const parts = own.filter((s) => s.direction === dir && !hiddenSeries.has(s.key))
        if (parts.length === 0) continue
        const values = fleet.x.map((_, i) => {
          let acc: number | null = null
          for (const part of parts) {
            const v = part.values[i]
            if (v != null) acc = (acc ?? 0) + v
          }
          return acc == null ? null : acc / per
        })
        const spans = parts.map((p) => p.smoothSeconds ?? 0)
        lines.push({
          key: `${c.characterId}:${dir}`, label: c.name, stroke: c.color, dash: c.dash,
          direction: dir, defaultVisible: true, values, noFill: true,
          smoothSeconds: Math.max(...spans) || undefined,
        })
      }
    }
    return lines
  }, [compare, fleet, family, direction, hiddenSeries, smooth, smoothScale])
  const drawn = useMemo(
    () => compareLines ?? offered.filter((s) => !hiddenSeries.has(s.key)),
    [compareLines, offered, hiddenSeries],
  )

  const availableKinds = useMemo(() => {
    const seen = new Set(broadcasts.map((b) => b.kind))
    return BROADCAST_KINDS.filter((k) => seen.has(k))
  }, [broadcasts])
  const broadcastMarkers = useMemo(
    () => toBroadcastMarkers(broadcasts, enabledKinds),
    [broadcasts, enabledKinds],
  )
  const dimUnless = useMemo(() => (isolated ? isolatedIds : null), [isolated, isolatedIds])

  const kills = fleet.kills
  const friendly = kills.filter((k) => k.side_kind === 'friendly').length
  const hostile = kills.filter((k) => k.side_kind === 'hostile').length

  return (
    <div data-testid="timeline-chart-area">
      <div className="chart-controls">
        <div className="chart-chips" role="group" aria-label="Series shown">
          {offered.map((s) => {
            const shown = !hiddenSeries.has(s.key)
            return (
              <button
                key={s.key}
                type="button"
                aria-pressed={shown}
                title={!compare && s.smoothSeconds ? `Smoothed over ±${s.smoothSeconds} s` : undefined}
                onClick={() => onToggleSeries(s.key)}
                className="fleet-legend-btn"
                style={compare ? {
                  // Colour means "which pilot" while comparing, so the chips go neutral.
                  background: shown ? 'rgba(138,147,167,0.18)' : 'transparent',
                  color: shown ? 'var(--text)' : 'var(--text-dim)',
                } : {
                  borderColor: s.stroke,
                  background: shown ? hexToRgba(s.stroke, 0.18) : 'transparent',
                  color: shown ? s.stroke : 'var(--text-dim)',
                }}
              >
                {!compare && <span aria-hidden className="chip-swatch" style={{ background: s.stroke }} />}
                {s.label}
              </button>
            )
          })}
        </div>
        <div className="chart-options">
          {availableKinds.length > 0 && (
            <div className="chart-chips" data-testid="broadcast-toggles" role="group" aria-label="Broadcast markers">
              <span className="dim chart-options-label">Broadcasts</span>
              {availableKinds.map((kind) => {
                const on = enabledKinds.has(kind)
                const color = BROADCAST_KIND_COLOR[kind] ?? 'var(--accent)'
                return (
                  <button
                    key={kind}
                    type="button"
                    aria-pressed={on}
                    className="fleet-legend-btn"
                    data-testid={`broadcast-toggle-${kind}`}
                    onClick={() => setEnabledKinds((prev) => {
                      const next = new Set(prev)
                      if (next.has(kind)) next.delete(kind)
                      else next.add(kind)
                      return next
                    })}
                    style={{ borderColor: color, color: on ? '#0a0d12' : color, background: on ? color : 'transparent' }}
                  >
                    {BROADCAST_KIND_LABEL[kind] ?? kind}
                  </button>
                )
              })}
            </div>
          )}
          {kills.length > 0 && (
            <label className="chart-check">
              <input type="checkbox" checked={showKills} onChange={(e) => setShowKills(e.target.checked)} />
              Kill markers
            </label>
          )}
          <label className="chart-check">
            <input type="checkbox" checked={smooth} onChange={(e) => setSmooth(e.target.checked)} />
            Smoothing
          </label>
          {smooth && (
            <label className="chart-check dim">
              <input
                type="range" min={0.5} max={3} step={0.5} value={smoothScale}
                onChange={(e) => setSmoothScale(Number(e.target.value))}
                aria-label="Smoothing window"
              />
              ×{smoothScale}
            </label>
          )}
        </div>
      </div>

      {compare && (
        <div className="chart-chips compare-legend" data-testid="compare-legend" role="group" aria-label="Pilots compared">
          {compare.map((c) => (
            <button
              key={c.characterId}
              type="button"
              className="fleet-legend-btn compare-chip"
              title={`Remove ${c.name} from the comparison${smoothNote(compareLines, c.characterId)}`}
              onClick={() => onTogglePilot?.(c.characterId)}
            >
              <span
                aria-hidden
                className={c.dash ? 'compare-swatch dashed' : 'compare-swatch'}
                style={{ color: c.color }}
              />
              {c.name}
              <span aria-hidden className="compare-chip-x">×</span>
            </button>
          ))}
        </div>
      )}

      {!panel || offered.length === 0 || drawn.length === 0 && compare ? (
        <p className="dim chart-empty" data-testid="chart-empty" style={{ minHeight: height }}>
          {isolated
            ? 'The isolated pilots have nothing logged for this stat type and direction.'
            : 'Nothing was logged for this stat type and direction in this battle.'}
        </p>
      ) : (
        <ChartCanvas
          panel={panel}
          series={drawn}
          x={fleet.x}
          window={win}
          kills={showKills ? kills : NO_KILLS}
          broadcastMarkers={broadcastMarkers}
          flaggedDeaths={flaggedDeaths}
          dimUnless={dimUnless}
          fleet={fleet}
          height={height}
          rangeRef={rangeRef}
          onZoom={handleZoom}
          registerZoomer={registerZoomer}
          showHoverSummary={!isolated}
          compareMode={compare != null}
          entities={entities}
        />
      )}

      <div className="chart-foot dim">
        {kills.length > 0 && (
          <span data-testid="fleet-kill-legend" className="chart-kill-legend">
            <span><span style={{ color: KILL_HOSTILE_LOSS }}>▮</span> enemy lost ({hostile})</span>
            <span><span style={{ color: KILL_FRIENDLY_LOSS }}>▮</span> friendly lost ({friendly})</span>
            {kills.length - friendly - hostile > 0 && (
              <span><span style={{ color: KILL_NEUTRAL }}>▮</span> unassigned ({kills.length - friendly - hostile})</span>
            )}
          </span>
        )}
        <span>
          Drag across the chart to zoom in; the table below follows what is shown. Double-click to zoom back out
          {kills.length > 0 ? '. Ctrl-click a kill marker to open it on zKillboard.' : '.'}
        </span>
      </div>
    </div>
  )
}
