import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { ApiError, BrDetail, BrStatus, FightOut, FightWithBrId, FilterGroup, MeResponse, UserCoverage } from '../api'
import { api } from '../api'
import { CoverageMatrix } from '../components/CoverageMatrix'
import { FilterBuilder } from '../components/FilterBuilder'
import { FightList } from '../components/FightList'
import { IngestProgress } from '../components/IngestProgress'
import { fmtIsk } from '../format'

function MyCoverageSection({ id }: { id: string }) {
  const [myCoverage, setMyCoverage] = useState<UserCoverage | null>(null)
  const [noParticipation, setNoParticipation] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.myBrCoverage(id).then(
      (data) => { if (!cancelled) setMyCoverage(data) },
      (e: unknown) => {
        if (!cancelled) {
          const err = e as ApiError
          if (err?.status === 404) {
            setNoParticipation(true)
          } else {
            setError(String(err?.message ?? e))
          }
        }
      },
    )
    return () => { cancelled = true }
  }, [id])

  if (noParticipation) {
    return <p className="dim">None of your characters participated in this BR.</p>
  }
  if (error) {
    return <p className="error-text">{error}</p>
  }
  if (!myCoverage) {
    return <p className="dim">Loading coverage…</p>
  }

  return (
    <div>
      <h3 style={{ margin: '0 0 0.5rem' }}>My Characters</h3>
      {myCoverage.characters.length === 0 ? (
        <p className="dim">No characters found.</p>
      ) : (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
          {myCoverage.characters.map((char) => (
            <div key={char.character_id} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <Link to={`/brs/${id}/characters/${char.character_id}`} style={{ fontWeight: 500 }}>
                {char.character_name}
              </Link>
              {char.covered ? (
                <span className="cov-covered">✓ covered ({char.fights_covered.length} fights)</span>
              ) : (
                <span className="cov-missing">
                  ✗ missing {char.fights_missing.length} fight{char.fights_missing.length !== 1 ? 's' : ''}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
      <p style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}>
        <Link to="/logs">Upload logs →</Link>
      </p>
    </div>
  )
}

export function BrDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [br, setBr] = useState<BrDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [me, setMe] = useState<MeResponse | null>(null)
  const [fullCoverage, setFullCoverage] = useState<UserCoverage[] | null>(null)
  const [filteredFights, setFilteredFights] = useState<FightWithBrId[] | null>(null)
  const [fightFilterActive, setFightFilterActive] = useState(false)
  const [fightFilterError, setFightFilterError] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!id) return
    let cancelled = false
    api.getBr(id).then(
      (d) => { if (!cancelled) setBr(d) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id])

  useEffect(() => { return load() }, [load])

  useEffect(() => {
    let cancelled = false
    api.me().then(
      (d) => { if (!cancelled) setMe(d) },
      () => { /* ignore errors; coverage just won't show */ },
    )
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!br || !id || !me?.can_create_br) return
    let cancelled = false
    api.brCoverage(id).then(
      (data) => { if (!cancelled) setFullCoverage(data) },
      () => { /* non-critical; ignore */ },
    )
    return () => { cancelled = true }
  }, [br, id, me])

  async function handleFightFilterApply(tree: FilterGroup) {
    if (!id) return
    setFightFilterError(null)
    try {
      const result = await api.filterFights(tree, id)
      setFilteredFights(result)
      setFightFilterActive(true)
    } catch (e: unknown) {
      setFightFilterError(String((e as Error)?.message ?? e))
    }
  }

  function handleFightFilterClear() {
    setFilteredFights(null)
    setFightFilterActive(false)
    setFightFilterError(null)
  }

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!br) return <div className="page"><p className="dim">Loading…</p></div>

  const brStatus: BrStatus = {
    br_id: br.br_id,
    status: br.status,
    progress_pct: br.progress_pct,
    error_text: null,
  }

  const displayFights: FightOut[] = filteredFights ?? br.fights

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to="/" className="dim" style={{ fontSize: '0.85rem' }}>← Timeline</Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>{br.title ?? `BR ${br.br_id}`}</h1>
        </div>
      </div>
      <div className="panel">
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1.5rem' }}>
          {br.result && (
            <div>
              <div className="stat-label">Result</div>
              <span className={`badge badge-${br.result}`}>{br.result}</span>
            </div>
          )}
          {br.isk_efficiency != null && (
            <div>
              <div className="stat-label">ISK Efficiency</div>
              <div className="stat-value">{(br.isk_efficiency * 100).toFixed(1)}%</div>
            </div>
          )}
          <div>
            <div className="stat-label">ISK Killed</div>
            <div className="stat-value">{fmtIsk(br.our_isk_destroyed)}</div>
          </div>
          <div>
            <div className="stat-label">ISK Lost</div>
            <div className="stat-value">{fmtIsk(br.our_isk_lost)}</div>
          </div>
          <div>
            <div className="stat-label">Engagements</div>
            <div className="stat-value">{br.fight_count}</div>
          </div>
          <div>
            <div className="stat-label">Source</div>
            <div>
              <span className="badge badge-source">{br.source}</span>
              {br.source_url && (
                <a href={br.source_url} target="_top" rel="noopener noreferrer" style={{ marginLeft: '0.5rem' }}>View source ↗</a>
              )}
            </div>
          </div>
        </div>
      </div>
      {br.status !== 'ready' && (
        <IngestProgress brId={br.br_id} initialStatus={brStatus} onReady={load} />
      )}

      <h2 style={{ margin: 0 }}>Engagements</h2>

      <details>
        <summary style={{ cursor: 'pointer', fontWeight: 600, marginBottom: '0.5rem' }}>
          Filter sub-engagements
        </summary>
        <FilterBuilder scope="fight" onApply={handleFightFilterApply} onClear={handleFightFilterClear} />
        {fightFilterError && <p className="error-text">{fightFilterError}</p>}
      </details>

      {fightFilterActive && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <span className="dim" data-testid="fight-filter-count">
            Showing {filteredFights?.length ?? 0} of {br.fights.length} sub-engagements
          </span>
          <button className="btn" onClick={handleFightFilterClear} data-testid="fight-filter-clear">
            Clear filter
          </button>
        </div>
      )}

      <FightList fights={displayFights} brId={br.br_id} />

      <section data-testid="log-coverage-section" className="panel">
        <h2 style={{ margin: '0 0 0.75rem' }}>Log Coverage</h2>
        {id && <MyCoverageSection id={id} />}
        {me?.can_create_br && fullCoverage && (
          <div style={{ marginTop: '1rem' }}>
            <h3 style={{ margin: '0 0 0.5rem' }}>All Members</h3>
            <CoverageMatrix coverage={fullCoverage} brId={id} />
          </div>
        )}
      </section>
    </div>
  )
}
