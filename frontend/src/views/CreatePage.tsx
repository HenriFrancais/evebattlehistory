import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import type { MeResponse } from '../api'
import { api } from '../api'
import { SourceComposer } from '../components/SourceComposer'

export function CreatePage() {
  const [me, setMe] = useState<MeResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    let cancelled = false
    api.me().then(
      (m) => { if (!cancelled) setMe(m) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [])

  if (error) return <div className="page"><p className="error-text">{error}</p></div>
  if (!me) return <div className="page"><p className="dim">Loading…</p></div>

  if (!me.can_create_br) {
    return (
      <div className="not-authorised">
        <h2>Not Authorised</h2>
        <p>You don&apos;t have permission to create Battle Reports.</p>
      </div>
    )
  }

  return (
    <div className="page">
      <SourceComposer onCreated={(id) => { void navigate(`/brs/${id}`) }} />
    </div>
  )
}
