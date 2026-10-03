import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type BrListResponse, type FilteredBrResponse, type FilterGroup, type MeResponse, api } from '../api'
import { loadMe } from '../cache'
import { BrTimelineTable } from '../components/BrTimelineTable'
import { FilterBuilder } from '../components/FilterBuilder'
import { WinRateSummary } from '../components/WinRateSummary'

const PAGE_SIZE = 50

export function BrListPage() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [data, setData] = useState<BrListResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [filteredData, setFilteredData] = useState<FilteredBrResponse | null>(null)
  const [filterActive, setFilterActive] = useState(false)
  const [filterError, setFilterError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([loadMe(), api.listBrs(PAGE_SIZE, 0)]).then(
      ([m, d]) => { if (!cancelled) { setMe(m); setData(d) } },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [])

  // The list is paged server-side (newest battle first); the summary always covers
  // every report. "Show older" appends the next page.
  async function handleShowOlder() {
    if (!data) return
    setLoadingMore(true)
    try {
      const next = await api.listBrs(PAGE_SIZE, data.brs.length)
      setData({ ...next, brs: [...data.brs, ...next.brs] })
    } catch (e: unknown) {
      setError(String((e as Error)?.message ?? e))
    } finally {
      setLoadingMore(false)
    }
  }

  async function handleFilterApply(tree: FilterGroup) {
    setFilterError(null)
    try {
      const result = await api.filterBrs(tree)
      setFilteredData(result)
      setFilterActive(true)
    } catch (e: unknown) {
      setFilterError(String((e as Error)?.message ?? e))
    }
  }

  function handleFilterClear() {
    setFilteredData(null)
    setFilterActive(false)
    setFilterError(null)
  }

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!data || !me) return <div className="page"><p className="dim">Loading…</p></div>

  const displayData = filteredData ?? data
  const total = data.total ?? data.brs.length
  const remaining = total - data.brs.length

  return (
    <div className="page">
      <div className="page-header">
        <h1 style={{ margin: 0 }}>Overview</h1>
        <div style={{ display: 'flex', gap: '0.5rem' }}>
          <Link to="/logs" className="btn" data-testid="logs-btn">
            Logs
          </Link>
          {me.can_create_br && (
            <Link to="/brs/new" className="btn btn-primary" data-testid="new-br-btn">
              + New Battle Report
            </Link>
          )}
        </div>
      </div>

      <details>
        <summary style={{ cursor: 'pointer', fontWeight: 600, marginBottom: '0.5rem' }}>
          Search battle reports
        </summary>
        <FilterBuilder scope="br" onApply={handleFilterApply} onClear={handleFilterClear} />
        {filterError && <p className="error-text">{filterError}</p>}
      </details>

      {filterActive && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
          <span className="dim" data-testid="filter-count">
            Showing {filteredData?.brs.length ?? 0} of {total} filtered results
          </span>
          <button className="btn" onClick={handleFilterClear} data-testid="filter-clear-results">
            Clear filter
          </button>
        </div>
      )}

      <WinRateSummary summary={displayData.summary} />
      <BrTimelineTable brs={displayData.brs} />
      {!filterActive && remaining > 0 && (
        <div style={{ textAlign: 'center', marginTop: '0.75rem' }}>
          <button
            className="btn"
            data-testid="show-older"
            disabled={loadingMore}
            onClick={() => { void handleShowOlder() }}
          >
            {loadingMore ? 'Loading…' : `Show older (${remaining} more)`}
          </button>
        </div>
      )}
    </div>
  )
}
