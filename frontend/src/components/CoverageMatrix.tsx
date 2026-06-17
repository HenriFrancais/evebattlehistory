// Props: coverage: UserCoverage[]
// A compact table: rows = users, for each user show their characters with covered/missing indicator

import type { UserCoverage } from '../api'

interface Props {
  coverage: UserCoverage[]
}

export function CoverageMatrix({ coverage }: Props) {
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
              <td>{char.character_name}</td>
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
