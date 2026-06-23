// Dev-only floating control (no app header — the app is embedded in an iframe).
// Shows the "View as user" impersonation picker when the backend reports
// impersonation_available (DEV_MODE). Changing the identity forces a full page
// reload so the whole app behaves exactly as if that user opened the service.
// In production impersonation_available is false, so this renders nothing.

import { useEffect, useState } from 'react'
import type { MeResponse } from '../api'
import { loadMe } from '../cache'
import { ImpersonationPicker } from './ImpersonationPicker'

export function DevBar() {
  const [me, setMe] = useState<MeResponse | null>(null)

  useEffect(() => {
    loadMe().then(setMe).catch(() => setMe(null))
  }, [])

  if (!me?.impersonation_available) return null

  return (
    <div className="dev-bar">
      <ImpersonationPicker onChanged={() => window.location.reload()} />
    </div>
  )
}
