// Battle report page: a header strip that is always visible and a tab bar; the
// active tab fills the width. Tabs are routes (/brs/:id/:tab) so a view can be
// linked and the back button works. Only the active tab is mounted.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, Navigate, useNavigate, useParams } from 'react-router-dom'
import type {
  BrDetail, BrEntities, BrStatus, BroadcastMetrics as BroadcastMetricsData, CompositionResponse,
  MeResponse,
} from '../api'
import { api } from '../api'
import { flaggedDeathsByChar } from '../broadcasts'
import { invalidateBr, loadBr, loadComposition, loadEntities, loadMe } from '../cache'
import { BrHeader } from '../components/BrHeader'
import { IngestProgress } from '../components/IngestProgress'
import { EntityContext, buildEntityIndex } from '../entities'
import { AarTab } from './br/AarTab'
import { PerformanceTab } from './br/PerformanceTab'
import { InvolvedTab } from './br/InvolvedTab'
import { ManageTab } from './br/ManageTab'
import { TimelineTab } from './br/TimelineTab'

type TabId = 'involved' | 'timeline' | 'performance' | 'aar' | 'manage'

const TABS: { id: TabId; label: string; elevated?: boolean }[] = [
  { id: 'involved', label: 'Involved' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'performance', label: 'Performance' },
  { id: 'aar', label: 'AAR' },
  { id: 'manage', label: 'Manage', elevated: true },
]

/** Old per-character page → the timeline tab with that pilot isolated. */
export function CharacterRedirect() {
  const { id, charId } = useParams<{ id: string; charId: string }>()
  return <Navigate to={`/brs/${id}/timeline?pilots=${charId}`} replace />
}

export function BrDetailPage() {
  const { id, tab } = useParams<{ id: string; tab?: string }>()
  const navigate = useNavigate()
  const [br, setBr] = useState<BrDetail | null>(null)
  const [displayTitle, setDisplayTitle] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [me, setMe] = useState<MeResponse | null>(null)
  const [entities, setEntities] = useState<BrEntities | null>(null)
  const [composition, setComposition] = useState<CompositionResponse | null>(null)
  const [refreshStatus, setRefreshStatus] = useState<BrStatus | null>(null)
  // Bumped whenever sides change or an ingest finishes: tabs re-fetch their data.
  const [dataVersion, setDataVersion] = useState(0)
  // Bumped on a broadcast upload/delete: the timeline re-fetches its markers.
  const [broadcastVersion, setBroadcastVersion] = useState(0)
  const [flaggedDeaths, setFlaggedDeaths] = useState<Map<number, string>>(new Map())

  // `force` bypasses the prefetch cache and refreshes it (after an ingest/refresh).
  const load = useCallback((force = false) => {
    if (!id) return
    let cancelled = false
    loadBr(id, force).then(
      (d) => {
        if (!cancelled) {
          setBr(d)
          setDisplayTitle(d.title ?? `BR ${d.br_id}`)
        }
      },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id])

  useEffect(() => load(false), [load])

  useEffect(() => {
    let cancelled = false
    loadMe().then(
      (d) => { if (!cancelled) setMe(d) },
      () => { /* without `me` the page renders as a member would see it */ },
    )
    return () => { cancelled = true }
  }, [])

  // Who is who (tickers), and the per-side pilot counts for the header. Both are
  // best-effort: names render without tickers and the header without counts.
  useEffect(() => {
    if (!id) return
    let cancelled = false
    const force = dataVersion > 0
    loadEntities(id, force).then((d) => { if (!cancelled) setEntities(d) }, () => {})
    loadComposition(id, force).then((d) => { if (!cancelled) setComposition(d) }, () => {})
    return () => { cancelled = true }
  }, [id, dataVersion])

  const canManage = me?.can_create_br ?? false
  const requested = TABS.find((t) => t.id === tab)
  // An unknown tab, or Manage for a member, falls back to Involved.
  const active: TabId = requested && (!requested.elevated || canManage) ? requested.id : 'involved'

  // Deaths flagged by broadcast analysis style the timeline's kill markers. The
  // Performance tab lifts them when it loads; the timeline tab fetches them itself.
  useEffect(() => {
    if (!id || active !== 'timeline') return
    let cancelled = false
    api.broadcastMetrics(id).then(
      (m) => { if (!cancelled) setFlaggedDeaths(flaggedDeathsByChar(m)) },
      () => { /* no broadcast log: no flagged deaths */ },
    )
    return () => { cancelled = true }
  }, [id, active, broadcastVersion, dataVersion])

  const handleBroadcastLoaded = useCallback((m: BroadcastMetricsData) => {
    setFlaggedDeaths(flaggedDeathsByChar(m))
  }, [])

  const entityIndex = useMemo(() => buildEntityIndex(entities), [entities])

  if (error && !br) return <div className="page"><p className="error-text" role="alert">{error}</p></div>
  if (!br || !id) return <div className="page"><p className="dim">Loading battle report…</p></div>

  const brStatus: BrStatus = {
    br_id: br.br_id, status: br.status, progress_pct: br.progress_pct, error_text: null,
  }
  const onIngestReady = () => {
    setRefreshStatus(null)
    invalidateBr(br.br_id)
    load(true)
    setDataVersion((v) => v + 1)
  }
  // Sides (or a pilot's side / ship) changed: kill colours, leaders, counts and the
  // composition are all derived from them, so drop every cached resource of this BR
  // — including what the overview prefetched — and have the mounted tab refetch.
  const onDataChanged = () => {
    invalidateBr(br.br_id)
    setDataVersion((v) => v + 1)
  }
  const tabs = TABS.filter((t) => !t.elevated || canManage)

  return (
    <EntityContext.Provider value={entityIndex}>
      <div className="page page-wide br-page">
        <BrHeader
          br={br}
          title={displayTitle}
          canEdit={canManage}
          onTitleUpdated={(t) => { setDisplayTitle(t); invalidateBr(br.br_id) }}
          composition={composition}
        />

        {error && <p className="error-text" role="alert">{error}</p>}

        {br.warning_text && (
          <div className="panel br-warning" role="alert" data-testid="ingest-warning">
            {br.warning_text}
          </div>
        )}

        {br.status !== 'ready' && (
          <IngestProgress brId={br.br_id} initialStatus={brStatus} onReady={onIngestReady} />
        )}
        {refreshStatus && (
          <IngestProgress brId={br.br_id} initialStatus={refreshStatus} onReady={onIngestReady} />
        )}

        <nav className="br-tabs" aria-label="Battle report sections">
          {tabs.map((t) => (
            <Link
              key={t.id}
              to={t.id === 'involved' ? `/brs/${id}` : `/brs/${id}/${t.id}`}
              className={active === t.id ? 'br-tab on' : 'br-tab'}
              aria-current={active === t.id ? 'page' : undefined}
              data-testid={`tab-${t.id}`}
            >
              {t.label}
            </Link>
          ))}
        </nav>

        {active === 'involved' && (
          <InvolvedTab brId={id} reloadKey={dataVersion} onDataChanged={onDataChanged} />
        )}
        {active === 'timeline' && (
          <TimelineTab
            brId={id}
            reloadKey={dataVersion}
            broadcastKey={broadcastVersion}
            flaggedDeaths={flaggedDeaths}
          />
        )}
        {active === 'performance' && (
          <PerformanceTab
            brId={id}
            canManage={canManage}
            reloadKey={dataVersion}
            broadcastKey={broadcastVersion}
            onBroadcastChange={() => setBroadcastVersion((v) => v + 1)}
            onBroadcastLoaded={handleBroadcastLoaded}
          />
        )}
        {active === 'aar' && <AarTab brId={id} canManage={canManage} />}
        {active === 'manage' && (
          <ManageTab
            brId={id}
            title={displayTitle}
            onRefreshTriggered={setRefreshStatus}
            onSidesChanged={onDataChanged}
            onDeleted={() => { invalidateBr(id); navigate('/') }}
            onError={setError}
          />
        )}
      </div>
    </EntityContext.Provider>
  )
}
