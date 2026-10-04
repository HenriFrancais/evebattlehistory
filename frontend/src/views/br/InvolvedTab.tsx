// Involved tab: the high-level view of a fight — who was there, on which side,
// in what — one row per pilot, from the largest hull down.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import type { ApiError, CompositionResponse, UserCoverage } from '../../api'
import { api } from '../../api'
import { loadComposition } from '../../cache'
import type { InvolvedMode } from '../../components/InvolvedSide'
import { InvolvedSide } from '../../components/InvolvedSide'

/** One line telling a member how many of their own characters have a log for this battle. */
function MyCoverage({ brId }: { brId: string }) {
  const [cov, setCov] = useState<UserCoverage | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.myBrCoverage(brId).then(
      (d) => { if (!cancelled) setCov(d) },
      (e: unknown) => {
        // 404 = none of the viewer's characters took part: nothing to say.
        if (!cancelled && (e as ApiError)?.status !== 404) setError(String((e as Error)?.message ?? e))
      },
    )
    return () => { cancelled = true }
  }, [brId])

  if (error) return <p className="error-text" role="alert">{error}</p>
  if (!cov || cov.characters.length === 0) return null
  const missing = cov.characters.filter((c) => !c.covered)
  const total = cov.characters.length
  return (
    <div className={`inv-coverage${missing.length ? ' missing' : ''}`} data-testid="my-coverage">
      <span>
        {missing.length === 0
          ? `All ${total} of your characters in this battle have logs.`
          : `${total - missing.length} of ${total} of your characters in this battle have logs.`}
        {missing.length > 0 && (
          <span className="dim"> Missing: {missing.map((c) => c.character_name).join(', ')}.</span>
        )}
      </span>
      {missing.length > 0 && <Link className="btn btn-primary" to="/logs">Upload logs</Link>}
    </div>
  )
}

interface Props {
  brId: string
  /** Bump to force a re-fetch (side overrides changed, ingest finished). */
  reloadKey?: number
  /** A pilot's side or ship was changed here: every tab's data is now stale. */
  onDataChanged?: () => void
}

export function InvolvedTab({ brId, reloadKey, onDataChanged }: Props) {
  const [data, setData] = useState<CompositionResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [mode, setMode] = useState<InvolvedMode>('pilot')
  const [showModules, setShowModules] = useState(true)
  const [localReload, setLocalReload] = useState(0)
  // Same brId re-run ⇒ only a reload signal changed ⇒ force a fresh fetch; a new
  // brId (or first mount) reads the prefetch cache.
  const fetchedBrId = useRef<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    const force = fetchedBrId.current === brId
    fetchedBrId.current = brId
    loadComposition(brId, force).then(
      (d) => { if (!cancelled) setData(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [brId, reloadKey, localReload])

  // If By-user becomes unavailable while selected, fall back.
  useEffect(() => {
    if (mode === 'main' && data && !data.by_user_available) setMode('pilot')
  }, [mode, data])

  const modes: { id: InvolvedMode; label: string }[] = [
    { id: 'pilot', label: 'Pilots' },
    // Grouping characters under their main needs the roster: FC / High Command only.
    ...(data?.by_user_available ? [{ id: 'main' as const, label: 'By main' }] : []),
    { id: 'ship', label: 'Ships' },
  ]

  return (
    <div className="involved-tab" data-testid="involved-tab">
      <MyCoverage brId={brId} />
      <section className="panel">
        {error ? (
          <p className="error-text" data-testid="involved-error" role="alert">{error}</p>
        ) : !data ? (
          <p className="dim">Loading who was involved…</p>
        ) : data.sides.length === 0 ? (
          <p className="dim" data-testid="involved-empty">
            No pilots yet. They appear once the battle report has finished ingesting its killmails.
          </p>
        ) : (
          <>
            <div className="inv-controls">
              <div className="seg" role="group" aria-label="Show">
                {modes.map((m) => (
                  <button key={m.id} type="button" className={mode === m.id ? 'on' : ''}
                    aria-pressed={mode === m.id} onClick={() => setMode(m.id)}>
                    {m.label}
                  </button>
                ))}
              </div>
              {mode !== 'ship' && (
                <label className="chart-check" data-testid="toggle-modules">
                  <input type="checkbox" checked={showModules} onChange={(e) => setShowModules(e.target.checked)} />
                  Modules
                </label>
              )}
            </div>
            <div className="inv-sides" style={{ ['--inv-cols' as string]: data.sides.length }}>
              {data.sides.map((side) => (
                <InvolvedSide
                  key={side.side_kind}
                  side={side}
                  brId={brId}
                  mode={mode}
                  showModules={showModules}
                  canEdit={data.by_user_available}
                  onChanged={() => (onDataChanged ? onDataChanged() : setLocalReload((v) => v + 1))}
                />
              ))}
            </div>
          </>
        )}
      </section>
    </div>
  )
}
