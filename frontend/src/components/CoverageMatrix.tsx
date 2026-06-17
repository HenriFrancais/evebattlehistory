// Props: coverage: UserCoverage[]; brId?: string (when provided, character names link to timeline)

import { Link } from 'react-router-dom'
import type { UserCoverage } from '../api'

interface Props {
  coverage: UserCoverage[]
  brId?: string
}

export function CoverageMatrix({ coverage, brId }: Props) {
  return (
    <table className="cov-matrix" data-testid="coverage-matrix">
      <thead>
        <tr>
          <th>User</th>
          <th>Character</th>
          <th>Coverage</th>
        </tr>
      </thead>
      <tbody>
        {coverage.map((user) =>
          user.characters.map((char, charIdx) => (
            <tr key={`${user.user_name}-${char.character_id}`}>
              {charIdx === 0 && (
                <td rowSpan={user.characters.length} style={{ fontWeight: 600 }}>
                  {user.user_name}
                </td>
              )}
              <td>
                {brId ? (
                  <Link to={`/brs/${brId}/characters/${char.character_id}`}>
                    {char.character_name}
                  </Link>
                ) : (
                  char.character_name
                )}
                {/* E1: flag log-only participants (not on any killmail) */}
                {char.has_logs && char.on_killmail === false && (
                  <span
                    className="badge badge-log-only"
                    data-testid={`log-only-badge-${char.character_id}`}
                    title="Logs only — not on a killmail"
                    style={{ marginLeft: '0.4rem', fontSize: '0.75rem' }}
                  >
                    logs only
                  </span>
                )}
              </td>
              <td>
                {char.covered ? (
                  <span className="cov-covered">
                    ✓ {char.fights_covered.length} fight{char.fights_covered.length !== 1 ? 's' : ''}
                  </span>
                ) : (
                  <span className="cov-missing">
                    ✗ {char.fights_missing.length} missing
                  </span>
                )}
              </td>
            </tr>
          ))
        )}
        {coverage.length === 0 && (
          <tr>
            <td colSpan={3} style={{ color: 'var(--text-dim)', textAlign: 'center' }}>
              No coverage data
            </td>
          </tr>
        )}
      </tbody>
    </table>
  )
}
