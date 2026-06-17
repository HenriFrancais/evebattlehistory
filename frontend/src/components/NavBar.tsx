import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { type MeResponse, api } from '../api'
import { ImpersonationPicker } from './ImpersonationPicker'

export function NavBar() {
  const [me, setMe] = useState<MeResponse | null>(null)

  const fetchMe = useCallback(() => {
    api.me().then(setMe).catch(() => setMe(null))
  }, [])

  useEffect(() => {
    fetchMe()
  }, [fetchMe])

  return (
    <nav>
      <span className="nav-title">NV Battle Reports</span>
      <Link to="/">Timeline</Link>
      <Link to="/logs">Logs</Link>
      {me?.impersonation_available && (
        <ImpersonationPicker onChanged={fetchMe} />
      )}
    </nav>
  )
}
