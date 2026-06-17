// Fleet graph section component (E3).
// Fetches fleet timeline, transforms via toFleetUplotData, renders chart + kill markers.
// uPlot chart is NOT unit-tested (requires canvas). Tests cover toggle logic and kill list.

import { useCallback, useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { FleetTimeline, KillEvent, TimelineFightInfo } from '../api'
import { api } from '../api'
import { fmtIsk } from '../format'
import type { FleetSeriesConfig, FleetUplotData } from '../fleet'
import { toFleetUplotData } from '../fleet'

// Colours for kill marker vertical lines
const KILL_FRIENDLY_LOSS = 'rgba(229,57,53,0.85)'   // red — friendly loss
const KILL_HOSTILE_LOSS = 'rgba(67,160,71,0.85)'     // green — hostile loss (enemy died)

// ---------------------------------------------------------------------------
// Internal: uPlot chart for fleet data
// ---------------------------------------------------------------------------

const AXIS = '#8893a7'
const GRID = 'rgba(138,147,167,0.15)'
const FIGHT_BAND = 'rgba(255,213,79,0.08)'
const FIGHT_EDGE = 'rgba(255,213,79,0.4)'

/** Draw vertical kill-event markers on the fleet chart canvas. */
function killMarkersPlugin(kills: KillEvent[]): uPlot.Plugin {
  return {
    hooks: {
      draw(u) {
        const ctx = u.ctx
        ctx.save()
        for (const k of kills) {
          const x = u.valToPos(k.ts, 'x', true)
          const top = u.bbox.top
          const h = u.bbox.height
          ctx.strokeStyle = k.side_kind === 'friendly' ? KILL_FRIENDLY_LOSS : KILL_HOSTILE_LOSS
          ctx.lineWidth = 1.5
          ctx.beginPath()
          ctx.moveTo(x, top)
          ctx.lineTo(x, top + h)
          ctx.stroke()
        }
        ctx.restore()
      },
    },
  }
}

function fightMarkersPlugin(fights: TimelineFightInfo[]): uPlot.Plugin {
  return {
    hooks: {
      draw(u) {
        const ctx = u.ctx
        ctx.save()
        for (const f of fights) {
          const x0 = f.started_at != null ? u.valToPos(Date.parse(f.started_at) / 1000, 'x', true) : null
          const x1 = f.ended_at != null ? u.valToPos(Date.parse(f.ended_at) / 1000, 'x', true) : null
          const top = u.bbox.top
          const h = u.bbox.height

          if (x0 != null && x1 != null && x1 > x0) {
            ctx.fillStyle = FIGHT_BAND
            ctx.fillRect(x0, top, x1 - x0, h)
          }
          if (x0 != null) {
            ctx.strokeStyle = FIGHT_EDGE
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x0, top)
            ctx.lineTo(x0, top + h)
            ctx.stroke()
          }
          if (x1 != null && x1 !== x0) {
            ctx.strokeStyle = FIGHT_EDGE
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x1, top)
            ctx.lineTo(x1, top + h)
            ctx.stroke()
          }
        }
        ctx.restore()
      },
    },
  }
}

interface FleetChartProps {
  fleetData: FleetUplotData
  fights: TimelineFightInfo[]
  hiddenSeries: Set<string>
  height?: number
}

function FleetChart({ fleetData, fights, hiddenSeries, height = 260 }: FleetChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  // Hold uPlot instance so the toggle effect can call setSeries without rebuild.
  const uRef = useRef<uPlot | null>(null)

  // Create/destroy effect — keyed on data structure only, NOT hiddenSeries.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const seriesDefs: uPlot.Series[] = [
      {
        label: 'Time',
        value: (_u, v) => (v == null ? '' : new Date(v * 1000).toISOString().slice(11, 19)),
      },
      ...fleetData.seriesConfig.map((sc) => ({
        label: sc.label,
        stroke: sc.stroke,
        width: 1.5,
        points: { show: false },
        spanGaps: false,
        // Initial visibility from current hiddenSeries snapshot at creation time.
        show: !hiddenSeries.has(sc.key),
      })),
    ]

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      legend: { show: true },
      scales: {
        x: { time: true },
        y: { range: (_u, _min, max) => [0, Math.max(1, max)] },
      },
      axes: [
        { stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID } },
        { stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID }, size: 60 },
      ],
      series: seriesDefs,
      plugins: [fightMarkersPlugin(fights), killMarkersPlugin(fleetData.kills)],
    }

    const u = new uPlot(opts, fleetData.data as uPlot.AlignedData, el)
    uRef.current = u

    const onResize = () => {
      u.setSize({ width: el.clientWidth || 800, height })
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      u.destroy()
      uRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fleetData, fights, height])
  // hiddenSeries intentionally omitted — toggling is handled by the setSeries effect below.

  // Separate toggle effect — calls setSeries without rebuilding the chart.
  useEffect(() => {
    const u = uRef.current
    if (!u) return
    fleetData.seriesConfig.forEach((sc, i) => {
      // series[0] is the x-axis; data series start at index 1.
      u.setSeries(i + 1, { show: !hiddenSeries.has(sc.key) })
    })
  }, [hiddenSeries, fleetData.seriesConfig])

  return <div className="timeline-chart" ref={containerRef} />
}

// ---------------------------------------------------------------------------
// Kill markers list
// ---------------------------------------------------------------------------

function KillMarkersList({ kills }: { kills: KillEvent[] }) {
  if (kills.length === 0) return null

  return (
    <div data-testid="fleet-kills-list" style={{ marginTop: '0.75rem' }}>
      <h4 style={{ margin: '0 0 0.4rem', fontSize: '0.9rem', color: '#8893a7' }}>Kill Markers</h4>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.25rem' }}>
        {kills.map((k) => (
          <div key={k.killmail_id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.85rem' }}>
            <span style={{ color: '#8893a7', fontVariantNumeric: 'tabular-nums' }}>
              {new Date(k.ts * 1000).toISOString().slice(11, 19)}
            </span>
            <span style={{ fontWeight: 500 }}>{k.victim_ship_name}</span>
            {k.side_kind && (
              <span
                className={`badge badge-${k.side_kind}`}
                style={{ fontSize: '0.75rem' }}
              >
                {k.side_kind}
              </span>
            )}
            {k.isk != null && (
              <span style={{ color: '#8893a7' }}>{fmtIsk(k.isk)}</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Toggle controls for toggleable series
// ---------------------------------------------------------------------------

interface ToggleBarProps {
  seriesConfig: FleetSeriesConfig[]
  hiddenSeries: Set<string>
  onToggle: (key: string) => void
}

function ToggleBar({ seriesConfig, hiddenSeries, onToggle }: ToggleBarProps) {
  const toggleable = seriesConfig.filter((s) => s.toggleable)
  if (toggleable.length === 0) return null

  return (
    <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
      {toggleable.map((s) => {
        const isShown = !hiddenSeries.has(s.key)
        return (
          <button
            key={s.key}
            role="button"
            aria-pressed={isShown}
            onClick={() => onToggle(s.key)}
            style={{
              padding: '0.2rem 0.6rem',
              fontSize: '0.8rem',
              border: `1px solid ${s.stroke}`,
              borderRadius: 4,
              background: isShown ? s.stroke : 'transparent',
              color: isShown ? '#1a1e28' : s.stroke,
              cursor: 'pointer',
              fontWeight: 600,
            }}
          >
            {s.label}
          </button>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
// FleetSection: top-level exported component
// ---------------------------------------------------------------------------

interface Props {
  brId: string
}

export function FleetSection({ brId }: Props) {
  const [fleet, setFleet] = useState<FleetTimeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.fleetTimeline(brId).then(
      (data) => {
        if (!cancelled) {
          setFleet(data)
          setLoading(false)
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(String((e as Error)?.message ?? e))
          setLoading(false)
        }
      }
    )
    return () => { cancelled = true }
  }, [brId])

  const handleToggle = useCallback((key: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev)
      if (next.has(key)) {
        next.delete(key)
      } else {
        next.add(key)
      }
      return next
    })
  }, [])

  if (loading) {
    return <p className="dim">Loading fleet data…</p>
  }

  if (error) {
    return (
      <p className="error-text" data-testid="fleet-error">
        {error}
      </p>
    )
  }

  if (!fleet || fleet.x.length === 0) {
    return (
      <p className="dim" data-testid="fleet-empty">
        No fleet data available for this BR.
      </p>
    )
  }

  const fleetData: FleetUplotData = toFleetUplotData(fleet)

  return (
    <div data-testid="fleet-chart-area">
      <ToggleBar
        seriesConfig={fleetData.seriesConfig}
        hiddenSeries={hiddenSeries}
        onToggle={handleToggle}
      />
      <FleetChart
        fleetData={fleetData}
        fights={fleet.fights}
        hiddenSeries={hiddenSeries}
      />
      <KillMarkersList kills={fleetData.kills} />
    </div>
  )
}
