// Timeline tab: one switchable chart and the per-pilot stats table that follows it.
//
// Stat type, direction, the chart's zoom window and the isolated pilots live here;
// the chart and the table are both derived from them in the browser (pilotStats.ts),
// so zooming the chart or ticking a pilot never waits on the server. The table
// always covers exactly what the chart is zoomed to. Stat type,
// direction and isolated pilots are mirrored in the URL so a view can be linked.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import type { BroadcastRawItem, FleetTimeline, PilotTimeline } from '../../api'
import { api } from '../../api'
import { loadFleetTimeline, loadPilotTimeline } from '../../cache'
import { StatsTable } from '../../components/StatsTable'
import type { TimeRange } from '../../components/TimelineChart'
import { TimelineChart, fightWindow } from '../../components/TimelineChart'
import { panelSeriesKeys, seriesEffects } from '../../fleet'
import { fmtTime } from '../../format'
import type { StatDirection, StatFamily } from '../../pilotStats'
import { STAT_FAMILIES, computeStats, snapRange, sumSeries } from '../../pilotStats'

const DIRECTIONS: { id: StatDirection; label: string }[] = [
  { id: 'both', label: 'Both' },
  { id: 'out', label: 'Outgoing' },
  { id: 'in', label: 'Incoming' },
]
const NO_BROADCASTS: BroadcastRawItem[] = []

function fmtSpan(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  const m = Math.floor(s / 60)
  return m > 0 ? `${m}m ${String(s % 60).padStart(2, '0')}s` : `${s}s`
}

/**
 * A UTC clock time (HH:MM:SS) inside the fight window. Typed as text rather than a
 * native date-time input, which would render in the browser's locale and 12-hour clock.
 */
function TimeField({ label, value, window: win, onCommit, testId }: {
  label: string
  value: number
  window: TimeRange
  onCommit: (epoch: number) => void
  testId: string
}) {
  const shown = fmtTime(value, true)
  const [draft, setDraft] = useState(shown)
  const [invalid, setInvalid] = useState(false)
  useEffect(() => { setDraft(shown); setInvalid(false) }, [shown])

  const commit = () => {
    const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(draft.trim())
    const [h, min, sec] = m ? [Number(m[1]), Number(m[2]), Number(m[3] ?? 0)] : [99, 0, 0]
    if (h > 23 || min > 59 || sec > 59) {
      setInvalid(true)
      return
    }
    // The date is the fight's: the first day whose clock time falls inside the window.
    const dayStart = Math.floor(win.from / 86400) * 86400
    let epoch = dayStart + h * 3600 + min * 60 + sec
    if (epoch < Math.floor(win.from)) epoch += 86400
    if (epoch > Math.ceil(win.to)) {
      setInvalid(true)
      return
    }
    setInvalid(false)
    onCommit(epoch)
  }

  return (
    <label className="tl-time">
      <span className="sr-only">{label}</span>
      <input
        type="text" inputMode="numeric" size={8} maxLength={8} placeholder="HH:MM:SS"
        data-testid={testId} value={draft} aria-invalid={invalid}
        title={invalid ? 'Enter a UTC time inside the fight, as HH:MM:SS' : `${label}, HH:MM:SS`}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === 'Enter') commit() }}
      />
    </label>
  )
}

function parsePilots(raw: string | null): number[] {
  if (!raw) return []
  return raw.split(',').map((p) => Number(p)).filter((n) => Number.isInteger(n) && n > 0)
}

interface Props {
  brId: string
  /** Bump to force a re-fetch (side overrides changed, ingest finished). */
  reloadKey?: number
  /** Bump to re-fetch the broadcast markers (after an upload / delete). */
  broadcastKey?: number
  /** character_id → flagged death classification, styles matching kill markers. */
  flaggedDeaths?: Map<number, string>
}

export function TimelineTab({ brId, reloadKey, broadcastKey, flaggedDeaths }: Props) {
  const [params, setParams] = useSearchParams()
  const [fleet, setFleet] = useState<FleetTimeline | null>(null)
  const [fleetError, setFleetError] = useState<string | null>(null)
  const [pilots, setPilots] = useState<PilotTimeline | null>(null)
  const [pilotsError, setPilotsError] = useState<string | null>(null)
  const [broadcasts, setBroadcasts] = useState<BroadcastRawItem[]>(NO_BROADCASTS)
  const [range, setRange] = useState<TimeRange | null>(null)
  const [hiddenSeries, setHiddenSeries] = useState<Set<string>>(new Set())

  const statParam = params.get('stat')
  const family: StatFamily = STAT_FAMILIES.some((f) => f.id === statParam)
    ? (statParam as StatFamily)
    : 'damage'
  const dirParam = params.get('dir')
  const direction: StatDirection = dirParam === 'out' || dirParam === 'in' ? dirParam : 'both'
  const pilotsParam = params.get('pilots')

  const setParam = useCallback((key: string, value: string | null) => {
    setParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      return next
    }, { replace: true })
  }, [setParams])

  // Same brId re-run ⇒ only a reload signal changed ⇒ force a fresh fetch; a new
  // brId (or first mount) reads the prefetch cache.
  const fetchedBrId = useRef<string | null>(null)
  useEffect(() => {
    let cancelled = false
    const force = fetchedBrId.current === brId
    fetchedBrId.current = brId
    setFleetError(null)
    setPilotsError(null)
    setRange(null)
    loadFleetTimeline(brId, force).then(
      (d) => { if (!cancelled) setFleet(d) },
      (e: unknown) => { if (!cancelled) setFleetError(String((e as Error)?.message ?? e)) },
    )
    loadPilotTimeline(brId, force).then(
      (d) => { if (!cancelled) setPilots(d) },
      (e: unknown) => { if (!cancelled) setPilotsError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey])

  // Raw broadcast markers for the chart toggle. Best-effort: none ⇒ no toggle.
  useEffect(() => {
    let cancelled = false
    api.broadcasts(brId).then(
      (rows) => { if (!cancelled) setBroadcasts(rows) },
      () => { if (!cancelled) setBroadcasts(NO_BROADCASTS) },
    )
    return () => { cancelled = true }
  }, [brId, broadcastKey])

  // Isolated pilots: from the URL, minus ids the data does not contain (stale link,
  // or a pilot this viewer may not see).
  const isolatedIds = useMemo(() => {
    const wanted = parsePilots(pilotsParam)
    if (!pilots) return new Set<number>()
    const known = new Set(pilots.pilots.map((p) => p.character_id))
    return new Set(wanted.filter((id) => known.has(id)))
  }, [pilotsParam, pilots])

  // Drop stale ids from the URL once the data is in.
  useEffect(() => {
    if (!pilots) return
    const wanted = parsePilots(pilotsParam)
    if (wanted.length !== isolatedIds.size) {
      setParam('pilots', isolatedIds.size ? [...isolatedIds].join(',') : null)
    }
  }, [pilots, pilotsParam, isolatedIds, setParam])

  const togglePilot = useCallback((id: number) => {
    const next = new Set(isolatedIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setParam('pilots', next.size ? [...next].join(',') : null)
  }, [isolatedIds, setParam])

  const toggleSeries = useCallback((key: string) => {
    setHiddenSeries((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const isolatedSeries = useMemo(
    () => (pilots && isolatedIds.size > 0 ? sumSeries(pilots, isolatedIds) : null),
    [pilots, isolatedIds],
  )

  const win = useMemo(() => (fleet ? fightWindow(fleet) : { from: 0, to: 0 }), [fleet])
  // What the numbers cover: the dragged range (or the whole fight) widened to whole
  // 5-second buckets, so label, table, averages and breakdown describe the same span.
  const bucketSeconds = fleet?.bucket_seconds ?? 5
  const active = useMemo(
    () => snapRange(range ?? win, bucketSeconds),
    [range, win, bucketSeconds],
  )
  // With "Both" on the chart the table describes outgoing.
  const tableDirection: 'out' | 'in' = direction === 'in' ? 'in' : 'out'

  // The table follows the series switched on for the chart.
  const effects = useMemo(() => {
    const out = new Set<string>()
    for (const key of panelSeriesKeys(family)) {
      if (hiddenSeries.has(key)) continue
      const def = seriesEffects(key)
      if (def && def.direction === tableDirection) def.effects.forEach((e) => out.add(e))
    }
    return [...out]
  }, [family, hiddenSeries, tableDirection])

  const stats = useMemo(
    () =>
      pilots
        ? computeStats(pilots, { family, effects, direction: tableDirection, from: active.from, to: active.to })
        : null,
    [pilots, family, effects, tableDirection, active.from, active.to],
  )

  const handleTimeInput = (which: 'from' | 'to', epoch: number) => {
    const next = which === 'from' ? { from: epoch, to: active.to } : { from: active.from, to: epoch }
    if (next.from >= next.to) return
    setRange({ from: Math.max(next.from, win.from), to: Math.min(next.to, win.to) })
  }

  if (fleetError) return <p className="error-text" data-testid="timeline-error" role="alert">{fleetError}</p>
  if (!fleet) return <p className="dim">Loading timeline…</p>
  if (fleet.x.length === 0) {
    return (
      <div className="panel tab-empty" data-testid="timeline-empty">
        <h2>No combat logs for this battle yet</h2>
        <p className="dim">
          The timeline is built from pilots&apos; game logs. Once someone who was in the
          fight uploads theirs, damage, reps, cap and EWAR appear here.
        </p>
        <Link className="btn btn-primary" to="/logs">Upload logs</Link>
      </div>
    )
  }

  return (
    <div className="timeline-tab" data-testid="timeline-tab">
      <section className="panel">
        <div className="tl-switches">
          <div className="seg" role="group" aria-label="Stat type">
            {STAT_FAMILIES.map((f) => (
              <button
                key={f.id} type="button" className={family === f.id ? 'on' : ''}
                aria-pressed={family === f.id}
                onClick={() => setParam('stat', f.id === 'damage' ? null : f.id)}
              >
                {f.label}
              </button>
            ))}
          </div>
          <div className="seg" role="group" aria-label="Direction">
            {DIRECTIONS.map((d) => (
              <button
                key={d.id} type="button" className={direction === d.id ? 'on' : ''}
                aria-pressed={direction === d.id}
                onClick={() => setParam('dir', d.id === 'both' ? null : d.id)}
              >
                {d.label}
              </button>
            ))}
          </div>
        </div>
        <TimelineChart
          fleet={fleet}
          family={family}
          direction={direction}
          isolatedSeries={isolatedSeries}
          isolatedIds={isolatedIds}
          selectedRange={range}
          onSelectRange={setRange}
          hiddenSeries={hiddenSeries}
          onToggleSeries={toggleSeries}
          broadcasts={broadcasts}
          flaggedDeaths={flaggedDeaths}
        />
      </section>

      <section className="panel">
        <div className="tl-selection" data-testid="tl-selection">
          <div className="tl-selection-range">
            <strong data-testid="range-label">
              {fmtTime(active.from, true)} → {fmtTime(active.to, true)} UTC
            </strong>
            <span className="dim">
              {fmtSpan(active.to - active.from)} · {range ? 'zoomed in' : 'whole fight'}
            </span>
            <TimeField
              label="Shown from (UTC)" testId="range-from-input" value={active.from} window={win}
              onCommit={(t) => handleTimeInput('from', t)}
            />
            <TimeField
              label="Shown to (UTC)" testId="range-to-input" value={active.to} window={win}
              onCommit={(t) => handleTimeInput('to', t)}
            />
            {range && (
              <button type="button" className="btn-mini" data-testid="range-clear" onClick={() => setRange(null)}>
                Show whole fight
              </button>
            )}
          </div>
          {isolatedIds.size > 0 && (
            <div className="tl-isolated" data-testid="isolation-bar">
              <strong>{isolatedIds.size} pilot{isolatedIds.size === 1 ? '' : 's'} isolated</strong>
              <button type="button" className="btn-mini" data-testid="isolation-clear" onClick={() => setParam('pilots', null)}>
                Show whole fleet
              </button>
            </div>
          )}
        </div>
        {direction === 'both' && (
          <p className="dim stats-note" data-testid="table-direction-note">
            The table shows outgoing. Switch the direction to Incoming for what each pilot received.
          </p>
        )}
        {pilotsError ? (
          <p className="error-text" data-testid="stats-error" role="alert">
            Could not load per-pilot statistics: {pilotsError}
          </p>
        ) : !pilots || !stats ? (
          <p className="dim">Loading per-pilot statistics…</p>
        ) : (
          <StatsTable
            brId={brId}
            rows={stats.rows}
            totals={stats.totals}
            pilots={pilots.pilots}
            family={family}
            direction={tableDirection}
            selected={isolatedIds}
            onToggle={togglePilot}
            range={active}
            scope={pilots.scope}
          />
        )}
      </section>
    </div>
  )
}
