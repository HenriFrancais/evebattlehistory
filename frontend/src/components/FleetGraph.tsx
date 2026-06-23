// Fleet graph (redesign slice 2a).
// Three stacked, x-synced panels (Damage & Reps / Cap / EWAR). Outgoing is drawn
// above a zero baseline, incoming mirrored below. Per-series toggles, smoothing
// control, and kill markers spanning each panel. The uPlot charts are not unit-
// tested (canvas); tests cover loading/empty/error, toggles, smoothing, kills.
//
// The selected time range is owned by the parent (selectedRange / onSelectRange);
// the source→target breakdown lives in SnapshotPanel.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { FleetTimeline, KillEvent, Leaders, TimelineFightInfo } from '../api'
import { loadFleetTimeline } from '../cache'
import { fmtCompact, fmtIsk, isoToEpoch } from '../format'
import type { FleetPanel, FleetView, PanelSeries } from '../fleet'
import { toFleetView } from '../fleet'
import { renderHoverSummary } from '../hoverSummary'

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

function epochToLocalInput(epoch: number): string {
  return new Date(epoch * 1000).toISOString().slice(0, 19)
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
  let detachDrag: (() => void) | null = null

  // Anchored above the chart, horizontally centred on the marker's triangle —
  // NOT following the cursor. This keeps the kill tip clear of the cursor-tracking
  // damage hover-tip (.hover-tip), which would otherwise render on top of it.
  const showTip = (k: KillEvent, el: HTMLElement) => {
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
      `<div class="kill-tip-meta">${t} UTC${isk}</div>` +
      `<div class="kill-tip-meta">⌃-click → zKill</div></div>`
    tip.style.display = 'flex'
    // The marker spans the full plot height (top:0, height:100%), so its rect's
    // top edge is the chart top and its mid-x is the triangle's position.
    const m = el.getBoundingClientRect()
    const centerX = (m.left + m.right) / 2
    const gap = 10 // sits just above the chart, pointing down at the triangle
    let left = centerX - tip.offsetWidth / 2
    left = Math.max(4, Math.min(left, window.innerWidth - tip.offsetWidth - 4))
    tip.style.left = `${left}px`
    tip.style.top = `${Math.max(4, m.top - tip.offsetHeight - gap)}px`
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
      el.addEventListener('mouseenter', () => showTip(k, el))
      el.addEventListener('mouseleave', hideTip)
      el.addEventListener('click', (ev) => {
        if (ev.ctrlKey || ev.metaKey) {
          ev.stopPropagation()
          window.open(`https://zkillboard.com/kill/${k.killmail_id}/`, '_blank', 'noopener,noreferrer')
        }
        // plain click falls through to the graph's range handler
      })
      layer.appendChild(el)
    }
    u.over.appendChild(layer)

    // Drag priority: once a drag actually MOVES, make the markers inert so the
    // zoom/snapshot drag flows smoothly across them instead of snagging on a
    // marker's hover target. Restored on mouseup. A plain (non-moving) click is
    // untouched, so ⌃-click → zKill still works.
    const setInert = (on: boolean) => {
      if (!layer) return
      for (const node of Array.from(layer.children)) {
        ;(node as HTMLElement).style.pointerEvents = on ? 'none' : ''
      }
    }
    const onDown = () => {
      let dragging = false
      const onMove = () => {
        if (!dragging) {
          dragging = true
          setInert(true)
          hideTip()
        }
      }
      const onUp = () => {
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
        if (dragging) setInert(false)
      }
      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    }
    u.over.addEventListener('mousedown', onDown)
    detachDrag = () => u.over.removeEventListener('mousedown', onDown)

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
        detachDrag?.()
        detachDrag = null
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
            const x = u.valToPos(isoToEpoch(t), 'x', true)
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

// DOM-overlay hover-summary tooltip: side totals + top-receiver leaders at the
// hovered bucket. Modelled on killMarkersPlugin (body-appended fixed tip).
function hoverSummaryPlugin(view: FleetView, leaders: Leaders[]): uPlot.Plugin {
  let tip: HTMLDivElement | null = null

  return {
    hooks: {
      ready: (u) => {
        tip = document.createElement('div')
        tip.className = 'hover-tip'
        tip.style.display = 'none'
        document.body.appendChild(tip)

        u.over.addEventListener('mouseleave', () => {
          if (tip) tip.style.display = 'none'
        })
      },
      setCursor: (u) => {
        if (!tip) return
        const idx = u.cursor.idx
        if (idx == null) {
          tip.style.display = 'none'
          return
        }
        tip.innerHTML = renderHoverSummary(view, leaders, idx)
        tip.style.display = 'block'
        // Position near the cursor using uPlot's cursor left/top
        const left = u.cursor.left ?? 0
        const top = u.cursor.top ?? 0
        const rect = u.over.getBoundingClientRect()
        tip.style.left = `${rect.left + left + 14}px`
        tip.style.top = `${rect.top + top + 14}px`
      },
      destroy: () => {
        tip?.remove()
        tip = null
      },
    },
  }
}

// Persistent draggable range band. Two handles (from/to) + a shaded span in u.over.
// A registered reposition fn keeps all panels' bands in sync. Dragging a handle edits
// the shared range; the band/handles never trigger native drag-zoom (stopPropagation).
function rangePlugin(
  getRange: () => { from: number; to: number } | null,
  onChange: (r: { from: number; to: number }) => void,
  register: (fn: () => void) => () => void,
): uPlot.Plugin {
  let band: HTMLDivElement | null = null
  let h0: HTMLDivElement | null = null
  let h1: HTMLDivElement | null = null
  let unregister: (() => void) | null = null

  const position = (u: uPlot) => {
    const r = getRange()
    if (!band || !h0 || !h1) return
    if (r == null) {
      band.style.display = h0.style.display = h1.style.display = 'none'
      return
    }
    const x0 = u.valToPos(r.from, 'x')
    const x1 = u.valToPos(r.to, 'x')
    const lo = Math.min(x0, x1)
    const hi = Math.max(x0, x1)
    band.style.display = ''
    band.style.left = `${lo}px`
    band.style.width = `${Math.max(0, hi - lo)}px`
    h0.style.display = h1.style.display = ''
    h0.style.left = `${x0}px`
    h1.style.left = `${x1}px`
  }

  const dragHandle = (u: uPlot, which: 'from' | 'to') => (ev: MouseEvent) => {
    ev.stopPropagation()
    ev.preventDefault()
    const move = (e: MouseEvent) => {
      const rect = u.over.getBoundingClientRect()
      let t = u.posToVal(e.clientX - rect.left, 'x')
      const min = u.scales.x.min ?? t
      const max = u.scales.x.max ?? t
      t = Math.max(min, Math.min(max, t))
      const cur = getRange()
      if (!cur) return
      onChange(which === 'from' ? { from: t, to: cur.to } : { from: cur.from, to: t })
    }
    const up = () => {
      document.removeEventListener('mousemove', move)
      document.removeEventListener('mouseup', up)
    }
    document.addEventListener('mousemove', move)
    document.addEventListener('mouseup', up)
  }

  return {
    hooks: {
      ready: (u) => {
        band = document.createElement('div')
        band.className = 'fleet-range-band'
        h0 = document.createElement('div')
        h1 = document.createElement('div')
        h0.className = h1.className = 'fleet-range-handle'
        u.over.appendChild(band)
        u.over.appendChild(h0)
        u.over.appendChild(h1)
        h0.addEventListener('mousedown', dragHandle(u, 'from'))
        h1.addEventListener('mousedown', dragHandle(u, 'to'))
        unregister = register(() => position(u))
        position(u)
      },
      setScale: (u) => position(u),
      setSize: (u) => position(u),
      destroy: () => {
        unregister?.()
        band?.remove(); h0?.remove(); h1?.remove()
        band = h0 = h1 = null
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
  rangeRef: { current: { from: number; to: number } | null }
  /** Range drag: replaces the whole range. Used by Shift-drag on the plot and the handle drags. */
  onRangeDrag: (r: { from: number; to: number }) => void
  registerPositioner: (fn: () => void) => () => void
  registerReset: (fn: () => void) => () => void
  /** FleetView (all panels) and leaders for the hover-summary plugin. */
  view: FleetView
  leaders: Leaders[]
}

function PanelChart({
  panel,
  x,
  hiddenSeries,
  kills,
  fights,
  height,
  zoomRef,
  rangeRef,
  onRangeDrag,
  registerPositioner,
  registerReset,
  view,
  leaders,
}: PanelChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)

  // Hoist full-extent computation so both the build effect and the reset closure
  // read the same memoised values without duplicating the logic.
  const { fullMin, fullMax } = useMemo(() => {
    const lossTimes: number[] = []
    for (const k of kills) lossTimes.push(k.ts)
    for (const f of fights) {
      if (f.started_at) lossTimes.push(isoToEpoch(f.started_at))
      if (f.ended_at) lossTimes.push(isoToEpoch(f.ended_at))
    }
    if (lossTimes.length) {
      const lo0 = Math.min(...lossTimes)
      const hi0 = Math.max(...lossTimes)
      const buf = Math.min(Math.max((hi0 - lo0) * 0.05, 15), 120)
      return { fullMin: lo0 - buf, fullMax: hi0 + buf }
    }
    return { fullMin: x.length ? x[0] : 0, fullMax: x.length ? x[x.length - 1] : 1 }
  }, [kills, fights, x])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const visible = panel.series.filter((s) => !hiddenSeries.has(s.key))

    // X domain is bounded to the LOSS window — earliest→latest loss (kill markers
    // and, for the per-pilot view where kills aren't passed, the kill-derived
    // fight bounds) — with a buffer on each side. Combat logs can start well
    // before and run long after the actual fight, so we clip that pre/post noise
    // rather than letting it stretch the axis. Falls back to the log extent only
    // when there are no losses at all.
    const lossTimes: number[] = []
    for (const k of kills) lossTimes.push(k.ts)
    for (const f of fights) {
      if (f.started_at) lossTimes.push(isoToEpoch(f.started_at))
      if (f.ended_at) lossTimes.push(isoToEpoch(f.ended_at))
    }
    let xs = x.slice()
    let cols = visible.map((s) => s.values.slice())
    const lo = fullMin
    const hi = fullMax
    if (lossTimes.length) {
      // Clip log buckets outside the window.
      const keep: number[] = []
      for (let i = 0; i < xs.length; i++) if (xs[i] >= lo && xs[i] <= hi) keep.push(i)
      xs = keep.map((i) => xs[i])
      cols = cols.map((c) => keep.map((i) => c[i]))
      // Pin the axis to the full window even where data doesn't reach the edges.
      if (!xs.length || xs[0] > lo) {
        xs = [lo, ...xs]
        cols = cols.map((c) => [null, ...c])
      }
      if (xs[xs.length - 1] < hi) {
        xs = [...xs, hi]
        cols = cols.map((c) => [...c, null])
      }
    }
    const data: (number | null)[][] = [xs, ...cols]

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
        {
          stroke: AXIS, grid: { stroke: GRID }, ticks: { stroke: GRID },
          // UTC ticks: date on the day boundary, else HH:MM:SS. uPlot x values are epoch seconds.
          values: (_u, splits) => splits.map((v) => {
            const iso = new Date(v * 1000).toISOString()
            return iso.slice(11, 19) // HH:MM:SS UTC
          }),
        },
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
        rangePlugin(() => rangeRef.current, onRangeDrag, registerPositioner),
        hoverSummaryPlugin(view, leaders),
      ],
    }

    // Capture any prior zoom BEFORE creating the chart — uPlot's initial
    // auto-range fires setScale and would otherwise clear it.
    const savedZoom = zoomRef.current
    const u = new uPlot(opts, data as uPlot.AlignedData, el)
    if (savedZoom) u.setScale?.('x', { min: savedZoom[0], max: savedZoom[1] })

    // Register the per-panel reset callback (after chart creation + saved-zoom restore).
    const unregisterReset = registerReset(() => {
      u.setScale?.('x', { min: fullMin, max: fullMax })
    })

    // Shift-drag paints the snapshot range; plain drag = zoom, double-click = zoom-out (native).
    // Capture phase + stopImmediatePropagation blocks uPlot's drag-zoom only while Shift is held;
    // without Shift this no-ops and uPlot's native gestures run untouched.
    // Track the active drag listeners so a mid-drag unmount/rebuild removes them in
    // cleanup — otherwise the stale `move` closure calls into a destroyed uPlot.
    let dragMove: ((e: MouseEvent) => void) | null = null
    let dragUp: (() => void) | null = null
    const clearDrag = () => {
      if (dragMove) document.removeEventListener('mousemove', dragMove)
      if (dragUp) document.removeEventListener('mouseup', dragUp)
      dragMove = dragUp = null
    }
    const onShiftDown = (ev: MouseEvent) => {
      if (!ev.shiftKey) return
      ev.stopImmediatePropagation() // block uPlot's drag-zoom
      ev.preventDefault()
      const rect = u.over.getBoundingClientRect()
      const from = u.posToVal(ev.clientX - rect.left, 'x')
      clearDrag() // defensively drop any prior drag listeners
      dragMove = (e: MouseEvent) => {
        const to = u.posToVal(e.clientX - rect.left, 'x')
        onRangeDrag({ from: Math.min(from, to), to: Math.max(from, to) })
      }
      dragUp = () => clearDrag()
      document.addEventListener('mousemove', dragMove)
      document.addEventListener('mouseup', dragUp)
    }
    u.over?.addEventListener('mousedown', onShiftDown, true) // capture phase
    const onResize = () => u.setSize({ width: el.clientWidth || 800, height })
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      clearDrag()
      unregisterReset()
      u.destroy()
    }
  }, [panel, x, hiddenSeries, kills, fights, height, zoomRef, rangeRef, onRangeDrag, registerPositioner, registerReset, fullMin, fullMax, view, leaders])

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

// --- core (operates on already-fetched data) -------------------------------

interface CoreProps {
  /** Already-fetched timeline (fleet-wide or adapted from a single character). */
  fleet: FleetTimeline
  /** Selected time range (epoch seconds), owned by the parent; null = none. */
  selectedRange: { from: number; to: number } | null
  onSelectRange: (r: { from: number; to: number } | null) => void
  /** Panel height in px. */
  height?: number
}

/**
 * Stacked family panels with smoothing, per-series toggles, kill markers, and a
 * draggable snapshot range. Shared by the fleet view and the per-character view.
 */
export function FleetGraphCore({
  fleet,
  selectedRange,
  onSelectRange,
  height = 165,
}: CoreProps) {
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set())
  const [smooth, setSmooth] = useState(true)
  const [smoothScale, setSmoothScale] = useState(1)
  const [showKills, setShowKills] = useState(true)
  const initialised = useRef(false)
  const winMin = fleet.t_start
  const winMax = fleet.t_end
  // Shared x-zoom across the panels, preserved across rebuilds (toggles,
  // smoothing) but reset when the underlying data reloads.
  const zoomRef = useRef<[number, number] | null>(null)
  // Range mirrored in a ref so the chart plugins read it live; positioners move
  // every panel's band/handles together on drag without rebuilding charts.
  const rangeRef = useRef<{ from: number; to: number } | null>(null)
  const positionersRef = useRef<Set<() => void>>(new Set())
  const resettersRef = useRef<Set<() => void>>(new Set())

  const registerPositioner = useCallback((fn: () => void) => {
    positionersRef.current.add(fn)
    return () => {
      positionersRef.current.delete(fn)
    }
  }, [])

  const registerReset = useCallback((fn: () => void) => {
    resettersRef.current.add(fn)
    return () => {
      resettersRef.current.delete(fn)
    }
  }, [])

  const handleResetZoom = useCallback(() => {
    resettersRef.current.forEach((fn) => fn())
    zoomRef.current = null
  }, [])

  const handleRangeDrag = useCallback((r: { from: number; to: number }) => {
    rangeRef.current = r
    positionersRef.current.forEach((fn) => fn())
    onSelectRange(r)
  }, [onSelectRange])

  const handleTimeInput = useCallback(
    (which: 'from' | 'to', value: string) => {
      if (!value) return
      const epoch = isoToEpoch(value)
      if (!Number.isFinite(epoch)) return
      const cur = selectedRange ?? { from: winMin ?? epoch, to: winMax ?? epoch }
      const next = which === 'from' ? { from: epoch, to: cur.to } : { from: cur.from, to: epoch }
      if (next.from >= next.to) return
      if (winMin != null && (next.from < winMin || next.to < winMin)) return
      if (winMax != null && (next.from > winMax || next.to > winMax)) return
      onSelectRange(next)
    },
    [selectedRange, winMin, winMax, onSelectRange],
  )

  // Mirror the parent-owned selection into the ref + reposition every panel's
  // band/handles, so external clears/changes are reflected on the canvas.
  useEffect(() => {
    rangeRef.current = selectedRange
    positionersRef.current.forEach((fn) => fn())
  }, [selectedRange])

  // Reset zoom + re-seed defaults when the underlying data changes.
  useEffect(() => {
    zoomRef.current = null
    initialised.current = false
  }, [fleet])

  const view: FleetView = useMemo(
    () => toFleetView(fleet, { smooth, smoothScale, bucketSeconds: fleet.bucket_seconds }),
    [fleet, smooth, smoothScale],
  )

  // Seed hidden set from defaults once per data load.
  useEffect(() => {
    if (initialised.current) return
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

  if (view.x.length === 0)
    return (
      <p className="dim" data-testid="fleet-empty">
        No timeline data available.
      </p>
    )

  const hasKills = view.kills.length > 0

  return (
    <div data-testid="fleet-chart-area">
      <div className="fleet-time-inputs" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem', flexWrap: 'wrap' }}>
        <label className="dim" style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.75rem' }}>
          Snapshot from (UTC)
          <input type="datetime-local" data-testid="snap-from-input" step={1}
            min={winMin != null ? epochToLocalInput(winMin) : undefined}
            max={winMax != null ? epochToLocalInput(winMax) : undefined}
            value={selectedRange ? epochToLocalInput(selectedRange.from) : ''}
            onChange={(e) => handleTimeInput('from', e.target.value)} />
        </label>
        <label className="dim" style={{ display: 'flex', flexDirection: 'column', gap: '0.2rem', fontSize: '0.75rem' }}>
          to (UTC)
          <input type="datetime-local" data-testid="snap-to-input" step={1}
            min={winMin != null ? epochToLocalInput(winMin) : undefined}
            max={winMax != null ? epochToLocalInput(winMax) : undefined}
            value={selectedRange ? epochToLocalInput(selectedRange.to) : ''}
            onChange={(e) => handleTimeInput('to', e.target.value)} />
        </label>
      </div>
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
        {hasKills && (
          <button
            role="button"
            aria-pressed={showKills}
            className="fleet-legend-btn"
            onClick={() => setShowKills((s) => !s)}
            style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
          >
            {showKills ? 'Kill markers: on' : 'Kill markers: off'}
          </button>
        )}
        <button
          type="button"
          className="fleet-legend-btn"
          data-testid="reset-zoom-btn"
          onClick={handleResetZoom}
          style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
        >
          Reset zoom
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
            fights={fleet.fights ?? []}
            height={height}
            zoomRef={zoomRef}
            rangeRef={rangeRef}
            onRangeDrag={handleRangeDrag}
            registerPositioner={registerPositioner}
            registerReset={registerReset}
            view={view}
            leaders={fleet.leaders ?? []}
          />
        </div>
      ))}

      <KillLegend kills={view.kills} />
      <p className="dim" style={{ fontSize: '0.75rem', margin: '0.2rem 0 0' }}>
        Tip: Shift-drag a range on any graph to snapshot it (drag the handles to adjust). Plain drag zooms; double-click resets.{hasKills ? ' ⌃-click a kill marker for zKill.' : ''}
      </p>
    </div>
  )
}

// --- fleet-wide wrapper (fetches the BR's fleet timeline) -------------------

interface Props {
  brId: string
  /** Bump to force a re-fetch (e.g. after side overrides change). */
  reloadKey?: number
  /** Selected time range (epoch seconds), owned by the parent; null = none. */
  selectedRange: { from: number; to: number } | null
  onSelectRange: (r: { from: number; to: number } | null) => void
  /** Per-panel height in px (taller in the fullscreen overlay). */
  height?: number
}

export function FleetGraph({ brId, reloadKey, selectedRange, onSelectRange, height = 260 }: Props) {
  const [fleet, setFleet] = useState<FleetTimeline | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Tracks the brId the last fetch ran for. When the effect re-runs for the SAME
  // brId, only `reloadKey` changed (sides edit / refresh) → force a fresh fetch.
  // A new brId (or first mount) reads the prefetch cache so the graph can open
  // already-loaded.
  const fetchedBrId = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    onSelectRange(null)
    const force = fetchedBrId.current === brId
    fetchedBrId.current = brId
    loadFleetTimeline(brId, force).then(
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
    // onSelectRange is a stable parent setter; deliberately not a dep (re-fetch only on brId/reloadKey).
  }, [brId, reloadKey])

  if (loading) return <p className="dim">Loading fleet data…</p>
  if (error)
    return (
      <p className="error-text" data-testid="fleet-error">
        {error}
      </p>
    )
  if (!fleet || fleet.x.length === 0)
    return (
      <p className="dim" data-testid="fleet-empty">
        No fleet data available for this BR.
      </p>
    )

  return (
    <FleetGraphCore fleet={fleet} selectedRange={selectedRange} onSelectRange={onSelectRange} height={height} />
  )
}
