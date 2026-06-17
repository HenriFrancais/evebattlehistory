// uPlot wrapper for the character timeline chart.
// Lifecycle: create on mount, destroy on unmount, rebuild when data changes.
// Fight boundaries are drawn as vertical bands using uPlot's addBand / hooks.
// Brush-select a horizontal region → onSelectRange(from, to).
// This component is NOT unit-tested because uPlot requires a real canvas.
// The unit-tested seam is toUplotData() in timeline.ts.

import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { TimelineFightInfo } from '../api'
import type { UplotData } from '../timeline'

const AXIS = '#8893a7'
const GRID = 'rgba(138,147,167,0.15)'
const FIGHT_BAND = 'rgba(255,213,79,0.08)'
const FIGHT_EDGE = 'rgba(255,213,79,0.4)'

interface Props {
  data: UplotData
  fights: TimelineFightInfo[]
  /**
   * Stable reference required: captured in the brush plugin closure at chart
   * mount and intentionally omitted from the effect deps. A new identity only
   * takes effect on a full rebuild (when `data`/`fights`/`height` change), so
   * callers must memoise this (e.g. useCallback) to avoid a stale closure.
   */
  onSelectRange: (from: number, to: number) => void
  height?: number
}

/** Draw vertical fight-boundary markers as hooks. */
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

/** Brush-select plugin: on mouseup after drag, fire onSelectRange. */
function brushSelectPlugin(onSelectRange: (from: number, to: number) => void): uPlot.Plugin {
  let dragStart: number | null = null

  return {
    hooks: {
      ready(u) {
        const over = u.over

        const onMouseDown = (e: MouseEvent) => {
          if (e.button !== 0) return
          const { left } = over.getBoundingClientRect()
          dragStart = u.posToVal(e.clientX - left, 'x')
        }

        const onMouseUp = (e: MouseEvent) => {
          if (dragStart == null) return
          const { left } = over.getBoundingClientRect()
          const dragEnd = u.posToVal(e.clientX - left, 'x')
          const from = Math.min(dragStart, dragEnd)
          const to = Math.max(dragStart, dragEnd)
          dragStart = null
          // Only fire if the drag covered at least 1 second (not a plain click)
          if (to - from >= 1) {
            onSelectRange(Math.floor(from), Math.ceil(to))
          }
        }

        over.addEventListener('mousedown', onMouseDown)
        over.addEventListener('mouseup', onMouseUp)

        ;(u as unknown as { _brushCleanup?: () => void })._brushCleanup = () => {
          over.removeEventListener('mousedown', onMouseDown)
          over.removeEventListener('mouseup', onMouseUp)
        }
      },
      destroy(u) {
        ;(u as unknown as { _brushCleanup?: () => void })._brushCleanup?.()
      },
    },
  }
}

export function TimelineChart({ data, fights, onSelectRange, height = 260 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const seriesDefs: uPlot.Series[] = [
      // x axis series (timestamp)
      {
        label: 'Time',
        value: (_u, v) => (v == null ? '' : new Date(v * 1000).toISOString().slice(11, 19)),
      },
      ...data.seriesConfig.map((sc) => ({
        label: sc.label,
        stroke: sc.stroke,
        width: 1.5,
        points: { show: false },
        spanGaps: false, // null gaps render as gaps
      })),
    ]

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      cursor: {
        drag: { x: true, y: false, dist: 5 },
      },
      legend: { show: true },
      scales: {
        x: { time: true },
        y: { range: (_u, _min, max) => [0, Math.max(1, max)] },
      },
      axes: [
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
        },
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          size: 60,
        },
      ],
      series: seriesDefs,
      plugins: [
        fightMarkersPlugin(fights),
        brushSelectPlugin(onSelectRange),
      ],
    }

    // uPlot data: first element is xs (number[]), rest are (number|null)[]
    // We cast because uPlot's TypeScript type says number[] but it handles nulls for spanGaps
    const uplotData = data.data as uPlot.AlignedData

    const u = new uPlot(opts, uplotData, el)

    const onResize = () => {
      u.setSize({ width: el.clientWidth || 800, height })
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      u.destroy()
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, fights, height])
  // onSelectRange intentionally omitted from deps: it's captured in plugin closure at mount.
  // Data + fights changes trigger full rebuild via the effect.

  return <div className="timeline-chart" ref={containerRef} />
}
