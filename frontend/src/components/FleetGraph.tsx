// Fleet graph (redesign slice 2a).
// Three stacked, x-synced panels (Damage & Reps / Cap / EWAR). Outgoing is drawn
// above a zero baseline, incoming mirrored below. Per-series toggles, smoothing
// control, and kill markers spanning each panel. The uPlot charts are not unit-
// tested (canvas); tests cover loading/empty/error, toggles, smoothing, kills.
//
// The clicked-moment time is owned by the parent (selectedTs / onSelectTs); the
// source→target breakdown lives in MomentDetailPanel.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { FleetTimeline, KillEvent, TimelineFightInfo } from '../api'
import { api } from '../api'
import { fmtCompact, fmtIsk } from '../format'
import type { FleetPanel, FleetView, PanelSeries } from '../fleet'
import { toFleetView } from '../fleet'

const AXIS = '#8893a7'
const GRID = 'rgba(138,147,167,0.15)'
const BASELINE = 'rgba(214,221,232,0.35)'
const FIGHT_EDGE = 'rgba(255,213,79,0.4)'
// Kill marker colours by victim side. Side A = friendly ("us").
const KILL_FRIENDLY_LOSS = '#ef5350' // red — Side A lost a ship
const KILL_HOSTILE_LOSS = '#4caf50' // green — Side A made a kill (enemy lost)
const KILL_NEUTRAL = '#9aa4b2' // grey — neutral / unknown victim side
const SYNC_KEY = 'fleet-x'
const NO_KILLS: KillEvent[] = [] // stable empty ref when markers are toggled off

function killColor(side: string | null): string {
  if (side === 'friendly') return KILL_FRIENDLY_LOSS
  if (side === 'hostile') return KILL_HOSTILE_LOSS
  return KILL_NEUTRAL
}

function hexToRgba(hex: string, a: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}

// --- plugins ---------------------------------------------------------------

function zeroBaselinePlugin(): uPlot.Plugin {
  return {
    hooks: {
      draw(u) {
        const y = u.valToPos(0, 'y', true)
        const ctx = u.ctx
        ctx.save()
        ctx.strokeStyle = BASELINE
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(u.bbox.left, y)
        ctx.lineTo(u.bbox.left + u.bbox.width, y)
        ctx.stroke()
        ctx.restore()
      },
    },
  }
}

// DOM-overlay kill markers (inside uPlot's plot-area `over` element): each is a
// thin coloured line with a top flag and a native hover tooltip, repositioned
// on scale/size changes. DOM (not canvas) so hover + clean toggling work.
function killMarkersPlugin(kills: KillEvent[]): uPlot.Plugin {
  let layer: HTMLDivElement | null = null
  let tip: HTMLDivElement | null = null

  const showTip = (k: KillEvent, ev: MouseEvent) => {
    if (!tip) return
    const icon =
      k.victim_ship_type_id != null
        ? `<img src="https://images.evetech.net/types/${k.victim_ship_type_id}/icon?size=32" width="32" height="32" alt="" />`
        : ''
    const t = new Date(k.ts * 1000).toISOString().slice(11, 19)
    const isk = k.isk != null ? ` · ${fmtIsk(k.isk)}` : ''
    const pilot = k.victim_character_name
      ? `<div class="kill-tip-pilot">${k.victim_character_name}</div>`
      : ''
    tip.innerHTML =
      `${icon}<div class="kill-tip-text"><div class="kill-tip-ship">${k.victim_ship_name}</div>` +
      pilot +
      `<div class="kill-tip-meta">${t} UTC${isk}</div></div>`
    tip.style.display = 'flex'
    moveTip(ev)
  }
  const moveTip = (ev: MouseEvent) => {
    if (!tip) return
    tip.style.left = `${ev.clientX + 12}px`
    tip.style.top = `${ev.clientY + 12}px`
  }
  const hideTip = () => {
    if (tip) tip.style.display = 'none'
  }

  const build = (u: uPlot) => {
    layer = document.createElement('div')
    layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
    tip = document.createElement('div')
    tip.className = 'kill-tip'
    document.body.appendChild(tip)
    for (const k of kills) {
      const color = killColor(k.side_kind)
      const el = document.createElement('div')
      el.className = 'fleet-kill-marker'
      el.dataset.ts = String(k.ts)
      // Faint 1px line centred in a 9px hover target — present but not dominant
      // over the series. The solid flag at the top is the primary locator.
      const faint = hexToRgba(color, 0.3)
      el.style.background =
        `linear-gradient(to right, transparent 4px, ${faint} 4px, ${faint} 5px, transparent 5px)`
      const flag = document.createElement('div')
      flag.className = 'fleet-kill-flag'
      flag.style.borderTopColor = color
      el.appendChild(flag)
      el.addEventListener('mouseenter', (ev) => showTip(k, ev))
      el.addEventListener('mousemove', moveTip)
      el.addEventListener('mouseleave', hideTip)
      el.addEventListener('click', (ev) => {
        ev.stopPropagation() // don't also trigger a time-pick
        window.open(`https://zkillboard.com/kill/${k.killmail_id}/`, '_blank', 'noopener,noreferrer')
      })
      layer.appendChild(el)
    }
    u.over.appendChild(layer)
    position(u)
  }

  const position = (u: uPlot) => {
    if (!layer) return
    const w = u.over.clientWidth
    for (const node of Array.from(layer.children)) {
      const el = node as HTMLElement
      const x = u.valToPos(Number(el.dataset.ts), 'x') // CSS pixels
      if (x < 0 || x > w) {
        el.style.display = 'none'
      } else {
        el.style.display = ''
        el.style.left = `${x}px`
      }
    }
  }

  return {
    hooks: {
      ready: (u) => build(u),
      setScale: (u) => position(u),
      setSize: (u) => position(u),
      destroy: () => {
        tip?.remove()
        tip = null
      },
    },
  }
}

function fightEdgesPlugin(fights: TimelineFightInfo[]): uPlot.Plugin {
  return {
    hooks: {
      draw(u) {
        const ctx = u.ctx
        ctx.save()
        ctx.strokeStyle = FIGHT_EDGE
        ctx.lineWidth = 1
        for (const f of fights) {
          for (const t of [f.started_at, f.ended_at]) {
            if (t == null) continue
            const x = u.valToPos(Date.parse(t) / 1000, 'x', true)
            ctx.beginPath()
            ctx.moveTo(x, u.bbox.top)
            ctx.lineTo(x, u.bbox.top + u.bbox.height)
            ctx.stroke()
          }
        }
        ctx.restore()
      },
    },
  }
}

// Persistent draggable time slider. Owns a vertical line in u.over; registers a
// reposition fn so all panels' sliders move together; drag updates the shared time.
function sliderPlugin(
  getTime: () => number | null,
  onChange: (t: number) => void,
  register: (fn: () => void) => () => void,
): uPlot.Plugin {
  let line: HTMLDivElement | null = null
  let unregister: (() => void) | null = null

  const position = (u: uPlot) => {
    if (!line) return
    const t = getTime()
    if (t == null) {
      line.style.display = 'none'
      return
    }
    const x = u.valToPos(t, 'x')
    const w = u.over.clientWidth
    if (x < 0 || x > w) {
      line.style.display = 'none'
    } else {
      line.style.display = ''
      line.style.left = `${x}px`
    }
  }

  return {
    hooks: {
      ready: (u) => {
        line = document.createElement('div')
        line.className = 'fleet-slider'
        line.innerHTML = '<span class="fleet-slider-grip"></span>'
        u.over.appendChild(line)
        const onMove = (ev: MouseEvent) => {
          const rect = u.over.getBoundingClientRect()
          let t = u.posToVal(ev.clientX - rect.left, 'x')
          const min = u.scales.x.min ?? t
          const max = u.scales.x.max ?? t
          t = Math.max(min, Math.min(max, t))
          onChange(t)
        }
        const onUp = () => {
          document.removeEventListener('mousemove', onMove)
          document.removeEventListener('mouseup', onUp)
        }
        line.addEventListener('mousedown', (ev) => {
          ev.stopPropagation()
          ev.preventDefault()
          document.addEventListener('mousemove', onMove)
          document.addEventListener('mouseup', onUp)
        })
        unregister = register(() => position(u))
        position(u)
      },
      setScale: (u) => position(u),
      setSize: (u) => position(u),
      destroy: () => {
        unregister?.()
        line?.remove()
        line = null
      },
    },
  }
}

// --- one panel chart -------------------------------------------------------

interface PanelChartProps {
  panel: FleetPanel
  x: number[]
  hiddenSeries: Set<string>
  kills: KillEvent[]
  fights: TimelineFightInfo[]
  height: number
  /** Shared x-zoom [min,max] across panels; null = full extent. Preserved across rebuilds. */
  zoomRef: { current: [number, number] | null }
  sliderTimeRef: { current: number | null }
  onSliderChange: (ts: number) => void
  registerPositioner: (fn: () => void) => () => void
}

function PanelChart({
  panel,
  x,
  hiddenSeries,
  kills,
  fights,
  height,
  zoomRef,
  sliderTimeRef,
  onSliderChange,
  registerPositioner,
}: PanelChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const visible = panel.series.filter((s) => !hiddenSeries.has(s.key))

    // X domain spans the WHOLE engagement, not just logged buckets: kills and
    // fights often extend beyond the log window. We AUGMENT the x data with the
    // domain endpoints (null y) rather than forcing scales.x.range — a hard
    // range would freeze the view and disable native drag-zoom / pan / reset.
    const times: number[] = []
    if (x.length) times.push(x[0], x[x.length - 1])
    for (const k of kills) times.push(k.ts)
    for (const f of fights) {
      if (f.started_at) times.push(Date.parse(f.started_at) / 1000)
      if (f.ended_at) times.push(Date.parse(f.ended_at) / 1000)
    }
    let xs = x.slice()
    let cols = visible.map((s) => s.values.slice())
    if (times.length) {
      const t0 = Math.min(...times)
      const t1 = Math.max(...times)
      if (!xs.length || t0 < xs[0]) {
        xs = [t0, ...xs]
        cols = cols.map((c) => [null, ...c])
      }
      if (t1 > xs[xs.length - 1]) {
        xs = [...xs, t1]
        cols = cols.map((c) => [...c, null])
      }
    }
    const data: (number | null)[][] = [xs, ...cols]
    const fullMin = xs.length ? xs[0] : 0
    const fullMax = xs.length ? xs[xs.length - 1] : 1

    const seriesDefs: uPlot.Series[] = [
      {
        label: 'Time',
        value: (_u, v) => (v == null ? '' : new Date(v * 1000).toISOString().slice(11, 19)),
      },
      ...visible.map((s) => ({
        label: s.label,
        stroke: s.stroke,
        fill: hexToRgba(s.stroke, 0.12),
        width: 1.5,
        points: { show: false },
        spanGaps: false,
        // Tooltip shows magnitude (abs) so mirrored 'in' reads positive.
        value: (_u: uPlot, v: number | null) =>
          v == null ? '' : fmtCompact(Math.abs(v)),
      })),
    ]

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      legend: { show: false },
      cursor: {
        // Sync the cursor AND the x-scale across panels, so zoom/pan on one
        // zooms all three together.
        sync: { key: SYNC_KEY, setSeries: false, scales: ['x', null] },
      },
      scales: { x: { time: true }, y: {} },
      axes: [
        { stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID } },
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          size: 56,
          label: panel.unit, // HP / GJ / # as the y-axis title
          labelSize: 18,
          values: (_u, splits) => splits.map((v) => fmtCompact(Math.abs(v))),
        },
      ],
      series: seriesDefs,
      hooks: {
        setScale: [
          (u, key) => {
            if (key !== 'x') return
            const min = u.scales.x.min
            const max = u.scales.x.max
            if (min == null || max == null) return
            const full = Math.abs(min - fullMin) <= 1 && Math.abs(max - fullMax) <= 1
            zoomRef.current = full ? null : [min, max]
          },
        ],
      },
      plugins: [
        fightEdgesPlugin(fights),
        zeroBaselinePlugin(),
        killMarkersPlugin(kills),
        sliderPlugin(() => sliderTimeRef.current, onSliderChange, registerPositioner),
      ],
    }

    // Capture any prior zoom BEFORE creating the chart — uPlot's initial
    // auto-range fires setScale and would otherwise clear it.
    const savedZoom = zoomRef.current
    const u = new uPlot(opts, data as uPlot.AlignedData, el)
    if (savedZoom) u.setScale?.('x', { min: savedZoom[0], max: savedZoom[1] })

    // Click a point in time → place/move the slider there.
    const onClick = () => {
      const idx = u.cursor.idx
      if (idx == null) return
      const ts = u.data[0][idx] as number | null
      if (ts != null) onSliderChange(ts)
    }
    u.over?.addEventListener('click', onClick)
    const onResize = () => u.setSize({ width: el.clientWidth || 800, height })
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      u.destroy()
    }
  }, [panel, x, hiddenSeries, kills, fights, height, zoomRef, sliderTimeRef, onSliderChange, registerPositioner])

  return (
    <div className="fleet-panel" data-testid={`fleet-panel-${panel.id}`}>
      <div className="fleet-panel-head">
        <span className="fleet-panel-title">{panel.title}</span>
      </div>
      <div className="timeline-chart" ref={containerRef} />
    </div>
  )
}

// --- per-panel toggle legend ----------------------------------------------

function ToggleLegend({
  series,
  hiddenSeries,
  onToggle,
}: {
  series: PanelSeries[]
  hiddenSeries: Set<string>
  onToggle: (key: string) => void
}) {
  return (
    <div className="fleet-legend">
      {series.map((s) => {
        const shown = !hiddenSeries.has(s.key)
        return (
          <button
            key={s.key}
            role="button"
            aria-pressed={shown}
            onClick={() => onToggle(s.key)}
            className="fleet-legend-btn"
            style={{
              borderColor: s.stroke,
              background: shown ? hexToRgba(s.stroke, 0.18) : 'transparent',
              color: shown ? s.stroke : 'var(--text-dim)',
              opacity: shown ? 1 : 0.6,
            }}
          >
            <span
              aria-hidden
              style={{ width: 10, height: 10, borderRadius: 2, background: s.stroke, display: 'inline-block' }}
            />
            {s.label}
          </button>
        )
      })}
    </div>
  )
}

// --- kill marker legend ----------------------------------------------------

function KillLegend({ kills }: { kills: KillEvent[] }) {
  if (kills.length === 0) return null
  const friendly = kills.filter((k) => k.side_kind === 'friendly').length
  const hostile = kills.filter((k) => k.side_kind === 'hostile').length
  const unassigned = kills.length - friendly - hostile
  return (
    <div className="dim" data-testid="fleet-kill-legend" style={{ display: 'flex', gap: '1rem', fontSize: '0.78rem', marginTop: '0.4rem', flexWrap: 'wrap' }}>
      <span><span style={{ color: KILL_HOSTILE_LOSS }}>▮</span> enemy lost ({hostile})</span>
      <span><span style={{ color: KILL_FRIENDLY_LOSS }}>▮</span> friendly lost ({friendly})</span>
      {unassigned > 0 && <span><span style={{ color: KILL_NEUTRAL }}>▮</span> unassigned ({unassigned})</span>}
      <span className="dim">hover for detail</span>
    </div>
  )
}

// --- top-level -------------------------------------------------------------

interface Props {
  brId: string
  /** Bump to force a re-fetch (e.g. after side overrides change). */
  reloadKey?: number
  /** Clicked-moment time (epoch seconds), owned by the parent; null = none. */
  selectedTs: number | null
  onSelectTs: (ts: number | null) => void
}

export function FleetGraph({ brId, reloadKey, selectedTs, onSelectTs }: Props) {
  const [fleet, setFleet] = useState<FleetTimeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set())
  const [smooth, setSmooth] = useState(true)
  const [smoothScale, setSmoothScale] = useState(1)
  const [showKills, setShowKills] = useState(true)
  const initialised = useRef(false)
  // Shared x-zoom across the panels, preserved across rebuilds (toggles,
  // smoothing) but reset when the BR data reloads.
  const zoomRef = useRef<[number, number] | null>(null)
  // Slider time mirrored in a ref so the chart plugins read it live; positioners
  // move every panel's slider together on drag without rebuilding charts.
  const sliderTimeRef = useRef<number | null>(null)
  const positionersRef = useRef<Set<() => void>>(new Set())

  const registerPositioner = useCallback((fn: () => void) => {
    positionersRef.current.add(fn)
    return () => {
      positionersRef.current.delete(fn)
    }
  }, [])

  const handleSliderChange = useCallback((ts: number) => {
    sliderTimeRef.current = ts
    positionersRef.current.forEach((fn) => fn())
    onSelectTs(ts)
  }, [onSelectTs])

  // Mirror the parent-owned selection into the ref + reposition every panel's
  // slider line, so external clears/changes are reflected on the canvas.
  useEffect(() => {
    sliderTimeRef.current = selectedTs
    positionersRef.current.forEach((fn) => fn())
  }, [selectedTs])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    initialised.current = false
    zoomRef.current = null
    sliderTimeRef.current = null
    onSelectTs(null)
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
      },
    )
    return () => {
      cancelled = true
    }
    // onSelectTs is a stable parent setter; deliberately not a dep (re-fetch only on brId/reloadKey).
  }, [brId, reloadKey])

  const view: FleetView | null = useMemo(
    () =>
      fleet
        ? toFleetView(fleet, { smooth, smoothScale, bucketSeconds: fleet.bucket_seconds })
        : null,
    [fleet, smooth, smoothScale],
  )

  // Seed hidden set from defaults once per BR load.
  useEffect(() => {
    if (!view || initialised.current) return
    const hidden = new Set<string>()
    for (const p of view.panels) for (const s of p.series) if (!s.defaultVisible) hidden.add(s.key)
    setHiddenSeries(hidden)
    initialised.current = true
  }, [view])

  const handleToggle = useCallback((key: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  if (loading) return <p className="dim">Loading fleet data…</p>
  if (error)
    return (
      <p className="error-text" data-testid="fleet-error">
        {error}
      </p>
    )
  if (!view || view.x.length === 0)
    return (
      <p className="dim" data-testid="fleet-empty">
        No fleet data available for this BR.
      </p>
    )

  return (
    <div data-testid="fleet-chart-area">
      <div className="fleet-controls">
        <button
          role="button"
          aria-pressed={smooth}
          className="fleet-legend-btn"
          onClick={() => setSmooth((s) => !s)}
          style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
        >
          {smooth ? 'Smoothing: on' : 'Smoothing: off'}
        </button>
        {smooth && (
          <label className="dim" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.8rem' }}>
            window
            <input
              type="range"
              min={0.5}
              max={3}
              step={0.5}
              value={smoothScale}
              onChange={(e) => setSmoothScale(Number(e.target.value))}
              aria-label="smoothing window"
            />
            ×{smoothScale}
          </label>
        )}
        <button
          role="button"
          aria-pressed={showKills}
          className="fleet-legend-btn"
          onClick={() => setShowKills((s) => !s)}
          style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
        >
          {showKills ? 'Kill markers: on' : 'Kill markers: off'}
        </button>
      </div>

      {view.panels.map((panel) => (
        <div key={panel.id} style={{ marginBottom: '0.75rem' }}>
          <ToggleLegend series={panel.series} hiddenSeries={hiddenSeries} onToggle={handleToggle} />
          <PanelChart
            panel={panel}
            x={view.x}
            hiddenSeries={hiddenSeries}
            kills={showKills ? view.kills : NO_KILLS}
            fights={fleet?.fights ?? []}
            height={165}
            zoomRef={zoomRef}
            sliderTimeRef={sliderTimeRef}
            onSliderChange={handleSliderChange}
            registerPositioner={registerPositioner}
          />
        </div>
      ))}

      <KillLegend kills={view.kills} />
      <p className="dim" style={{ fontSize: '0.75rem', margin: '0.2rem 0 0' }}>
        Tip: click a moment on any graph to drop a slider, then drag it to scrub the breakdown.
      </p>
    </div>
  )
}
