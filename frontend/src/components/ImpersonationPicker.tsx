/**
 * Dev-only "View as user" picker.
 *
 * Rendered inside the NavBar when me.impersonation_available is true.
 * Populated from GET /api/roster/users; selecting a user injects
 * X-Impersonate-User on every subsequent API call and refreshes the view.
 */

import { useEffect, useState } from 'react'
import { type RosterUserOut, api, getImpersonateUser, setImpersonateUser } from '../api'

interface ImpersonationPickerProps {
  /** Callback so parent can re-fetch /api/me after selection changes. */
  onChanged: () => void
}

export function ImpersonationPicker({ onChanged }: ImpersonationPickerProps) {
  const [users, setUsers] = useState<RosterUserOut[]>([])
  const [current, setCurrent] = useState<string>(getImpersonateUser() ?? '')

  useEffect(() => {
    api.rosterUsers().then(setUsers).catch(() => setUsers([]))
  }, [])

  function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const val = e.target.value
    setCurrent(val)
    setImpersonateUser(val || null)
    onChanged()
  }

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem' }}>
      <span style={{ color: '#f0a500', fontWeight: 600 }}>DEV</span>
      <label htmlFor="impersonate-picker" style={{ color: '#ccc' }}>
        View as:
      </label>
      <select
        id="impersonate-picker"
        value={current}
        onChange={handleChange}
        style={{ fontSize: '0.82rem', background: '#1e1e2e', color: '#cdd6f4', border: '1px solid #45475a', borderRadius: 4, padding: '2px 6px' }}
        aria-label="View as user"
      >
        <option value="">— yourself —</option>
        {users.map((u) => (
          <option key={u.user_name} value={u.user_name}>
            {u.user_name} ({u.rank})
          </option>
        ))}
      </select>
      {current && (
        <button
          onClick={() => { setCurrent(''); setImpersonateUser(null); onChanged() }}
          style={{ fontSize: '0.75rem', background: 'none', border: '1px solid #45475a', borderRadius: 4, color: '#cdd6f4', cursor: 'pointer', padding: '2px 6px' }}
          aria-label="Stop impersonating"
        >
          ✕ Stop
        </button>
      )}
    </span>
  )
}
