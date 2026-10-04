// uPlot plugins for the timeline chart: zero baseline, kill markers, broadcast
// ticks, fight edges and the cursor hover summary.
// The chart canvas is not unit-tested; these are DOM overlays inside uPlot's
// plot-area `over` element.
import uPlot from 'uplot'
import type { KillEvent, Leaders, TimelineFightInfo } from '../api'
import type { BroadcastMarker } from '../broadcasts'
import type { EntityIndex } from '../entities'
import { isoToEpoch } from '../format'
import type { PanelId } from '../fleet'
import { renderHoverSummary, renderKillTip } from '../hoverSummary'

export const AXIS = '#8893a7'
export const GRID = 'rgba(138,147,167,0.15)'
const BASELINE = 'rgba(214,221,232,0.35)'
const FIGHT_EDGE = 'rgba(255,213,79,0.4)'
// Kill marker colours by victim side. Side A = friendly ("us").
export const KILL_FRIENDLY_LOSS = '#ef5350' // red — Side A lost a ship
export const KILL_HOSTILE_LOSS = '#4caf50' // green — Side A made a kill (enemy lost)
export const KILL_NEUTRAL = '#9aa4b2' // grey — neutral / unknown victim side
export const DEATH_FLAG_COLOR = '#ffb300' // amber — a death flagged by broadcast classification

// Broadcast marker colours by kind (bottom-anchored ticks on the timeline).
export const BROADCAST_KIND_COLOR: Record<string, string> = {
  target: '#42a5f5',
  needs_shield: '#66bb6a',
  needs_armor: '#ffa726',
  needs_capacitor: '#ab47bc',
  repair: '#26c6da',
}
export const BROADCAST_KIND_LABEL: Record<string, string> = {
  target: 'Targets',
  needs_shield: 'Shield',
  needs_armor: 'Armor',
  needs_capacitor: 'Cap',
  repair: 'Repair',
}
const DEATH_FLAG_REASON: Record<string, string> = {
  no_broadcast: 'died with no rep broadcast',
  late_broadcast: 'broadcast too late to save',
  unanswered: 'broadcast went unanswered by logi',
}

function killColor(side: string | null): string {
  if (side === 'friendly') return KILL_FRIENDLY_LOSS
  if (side === 'hostile') return KILL_HOSTILE_LOSS
  return KILL_NEUTRAL
}

export function hexToRgba(hex: string, a: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${a})`
}

// --- plugins ---------------------------------------------------------------

export function zeroBaselinePlugin(): uPlot.Plugin {
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
export interface KillMarkerOpts {
  /** character_id → flagged death classification (amber marker + reason in the tip). */
  flaggedDeaths?: Map<number, string>
  /** When set, losses of anyone NOT in it are dimmed (pilot isolation). */
  dimUnless?: Set<number> | null
  /** Adds corp / alliance tickers to the victim's name in the tooltip. */
  entities?: EntityIndex
}

export function killMarkersPlugin(kills: KillEvent[], opts: KillMarkerOpts = {}): uPlot.Plugin {
  const flaggedDeaths = opts.flaggedDeaths ?? new Map<number, string>()
  const dimUnless = opts.dimUnless ?? null
  let layer: HTMLDivElement | null = null
  let tip: HTMLDivElement | null = null
  let detachDrag: (() => void) | null = null

  const flagOf = (k: KillEvent): string | undefined =>
    k.victim_character_id != null ? flaggedDeaths.get(k.victim_character_id) : undefined

  // Anchored above the chart, horizontally centred on the marker's triangle —
  // NOT following the cursor. This keeps the kill tip clear of the cursor-tracking
  // damage hover-tip (.hover-tip), which would otherwise render on top of it.
  const showTip = (k: KillEvent, el: HTMLElement) => {
    if (!tip) return
    const flag = flagOf(k)
    tip.innerHTML = renderKillTip(
      k, flag ? (DEATH_FLAG_REASON[flag] ?? flag) : null, DEATH_FLAG_COLOR, opts.entities,
    )
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
      const flagged = flagOf(k)
      const color = flagged ? DEATH_FLAG_COLOR : killColor(k.side_kind)
      const el = document.createElement('div')
      el.className = flagged ? 'fleet-kill-marker fleet-kill-marker--flagged' : 'fleet-kill-marker'
      const dimmed =
        dimUnless != null &&
        !(k.victim_character_id != null && dimUnless.has(k.victim_character_id))
      if (dimmed) el.classList.add('fleet-kill-marker--dim')
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
      })
      // uPlot starts a zoom drag only when the press lands on its own overlay, and
      // on a big fight these hover columns cover much of the chart. Hand a plain
      // press on a marker to the overlay so a drag can start anywhere.
      el.addEventListener('mousedown', (ev) => {
        if (ev.button !== 0 || ev.ctrlKey || ev.metaKey) return
        ev.stopPropagation()
        u.over.dispatchEvent(new MouseEvent('mousedown', {
          clientX: ev.clientX, clientY: ev.clientY, button: 0, buttons: 1, bubbles: true,
        }))
      })
      layer.appendChild(el)
    }
    u.over.appendChild(layer)

    // Drag priority: once a drag actually MOVES, make the markers inert so the
    // zoom drag flows smoothly across them instead of snagging on a
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

// DOM-overlay broadcast markers: short bottom-anchored ticks coloured by kind.
// Purely visual (pointer-events:none) so they never snag the zoom/snapshot drag;
// the toggle legend explains the colours.  `markers[].ts` is epoch MILLISECONDS.
export function broadcastMarkersPlugin(markers: BroadcastMarker[]): uPlot.Plugin {
  let layer: HTMLDivElement | null = null

  const build = (u: uPlot) => {
    layer = document.createElement('div')
    layer.style.cssText = 'position:absolute;inset:0;pointer-events:none;'
    for (const m of markers) {
      const el = document.createElement('div')
      el.className = 'fleet-broadcast-marker'
      el.dataset.ts = String(m.ts / 1000) // chart x is epoch seconds
      const color = BROADCAST_KIND_COLOR[m.kind] ?? '#8893a7'
      // A short tick rising from the bottom axis; title gives a native hover label.
      el.style.cssText =
        'position:absolute;bottom:0;width:0;height:26%;border-left:1px solid ' +
        `${hexToRgba(color, 0.75)};`
      el.title = m.label
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
      const x = u.valToPos(Number(el.dataset.ts), 'x')
      if (x < 0 || x > w) el.style.display = 'none'
      else {
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
    },
  }
}

export function fightEdgesPlugin(fights: TimelineFightInfo[]): uPlot.Plugin {
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
// `sourceIdx` maps a chart point to its bucket in the fleet timeline's x axis: the
// chart is clipped to the fight window and padded, so the two are not the same index.
export function hoverSummaryPlugin(
  panelId: PanelId, leaders: Leaders[], entities?: EntityIndex,
  sourceIdx?: (number | null)[],
): uPlot.Plugin {
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
        const bucket = sourceIdx ? sourceIdx[idx] : idx
        tip.innerHTML = renderHoverSummary(panelId, leaders, bucket ?? -1, entities)
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
