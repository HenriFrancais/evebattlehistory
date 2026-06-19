import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { type CharacterTimeline, type TimelineEvent, type TimelineEventList, ApiError, api } from '../api'
import { TimelineChart } from '../components/TimelineChart'
import { fmtTime } from '../format'
import { toUplotData } from '../timeline'

function formatTs(ts: string): string {
  return fmtTime(ts, true)
}

function EventsPanel({
  events,
  truncated,
}: {
  events: TimelineEvent[]
  truncated: boolean
}) {
  return (
    <div className="panel">
      <h3 style={{ margin: '0 0 0.5rem' }}>Events in range</h3>
      {truncated && (
        <p className="truncated-notice">Results truncated to 1000 rows — narrow your range to see all events.</p>
      )}
      {events.length === 0 ? (
        <p className="dim">No events in this range.</p>
      ) : (
        <table className="events-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Dir</th>
              <th>Effect</th>
              <th>Amount</th>
              <th>Other</th>
              <th>Ship</th>
              <th>Module</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={`${e.ts}-${i}`}>
                <td>{formatTs(e.ts)}</td>
                <td>{e.direction ?? '—'}</td>
                <td>{e.effect_type ?? '—'}</td>
                <td>{e.amount != null ? e.amount.toFixed(0) : '—'}</td>
                <td>{e.other_name ?? '—'}</td>
                <td>{e.other_ship_name ?? '—'}</td>
                <td>{e.module_name ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function CharacterTimelinePage() {
  const { id, charId } = useParams<{ id: string; charId: string }>()
  const [timeline, setTimeline] = useState<CharacterTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [forbidden, setForbidden] = useState(false)
  const [eventList, setEventList] = useState<TimelineEventList | null>(null)
  const [eventsLoading, setEventsLoading] = useState(false)

  useEffect(() => {
    if (!id || !charId) return
    let cancelled = false
    setForbidden(false)
    setError(null)
    setTimeline(null)
    api.characterTimeline(id, charId).then(
      (data) => { if (!cancelled) setTimeline(data) },
      (e: unknown) => {
        if (!cancelled) {
          if (e instanceof ApiError && e.status === 403) {
            setForbidden(true)
          } else {
            setError(String((e as Error)?.message ?? e))
          }
        }
      },
    )
    return () => { cancelled = true }
  }, [id, charId])

  const handleSelectRange = useCallback(
    (from: number, to: number) => {
      if (!id || !charId) return
      setEventList(null)
      setEventsLoading(true)
      api.characterEvents(id, charId, from, to).then(
        (data) => {
          setEventList(data)
          setEventsLoading(false)
        },
        (e: unknown) => {
          console.error('Events fetch failed:', e)
          setEventsLoading(false)
        },
      )
    },
    [id, charId],
  )

  if (forbidden) {
    return (
      <div className="page">
        <p className="error-text" data-testid="forbidden-message">
          You can only view your own characters' logs — ask an FC for fleet-wide access.
        </p>
      </div>
    )
  }

  if (error) {
    return (
      <div className="page">
        <p className="error-text">{error}</p>
      </div>
    )
  }

  if (!timeline) {
    return (
      <div className="page">
        <p className="dim">Loading…</p>
      </div>
    )
  }

  const isEmpty = timeline.series.length === 0

  const uplotData = isEmpty ? null : toUplotData(timeline)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to={`/brs/${id}`} className="dim" style={{ fontSize: '0.85rem' }}>
            ← BR Summary
          </Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>Character Timeline</h1>
        </div>
      </div>

      {isEmpty ? (
        <div className="panel">
          <p className="dim">No logs for this character in this BR. Upload combat logs to see timeline data.</p>
        </div>
      ) : (
        <>
          <div className="panel">
            <p className="dim" style={{ margin: '0 0 0.5rem', fontSize: '0.82rem' }}>
              Drag to select a time range and see raw events below. Fight boundaries shown as bands.
            </p>
            <TimelineChart
              data={uplotData!}
              fights={timeline.fights}
              onSelectRange={handleSelectRange}
            />
          </div>

          {eventsLoading && (
            <div className="panel">
              <p className="dim">Loading events…</p>
            </div>
          )}

          {!eventsLoading && eventList && (
            <EventsPanel events={eventList.events} truncated={eventList.truncated} />
          )}

          {!eventsLoading && !eventList && (
            <div className="panel">
              <p className="dim">Drag on the chart to select a time range and drill into events.</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
