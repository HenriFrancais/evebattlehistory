// Per-killmail loss detail: attacker damage attribution + item losses.
import { useEffect, useState } from 'react'
import type { ItemLossBreakdown, LossDamageAttribution } from '../api'
import { api } from '../api'
import { fmtCompact } from '../format'

interface Props {
  brId: string
  killmailId: number
}

export function LossDetailPanel({ brId, killmailId }: Props) {
  const [damage, setDamage] = useState<LossDamageAttribution | null>(null)
  const [items, setItems] = useState<ItemLossBreakdown | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setDamage(null)
    setItems(null)
    Promise.all([
      api.lossDamage(brId, killmailId),
      api.lossItems(brId, killmailId),
    ]).then(
      ([dmg, itm]) => {
        if (!cancelled) {
          setDamage(dmg)
          setItems(itm)
          setLoading(false)
        }
      },
      (e: unknown) => {
        if (!cancelled) {
          setError(String((e as Error)?.message ?? e))
          setLoading(false)
        }
      },
    )
    return () => { cancelled = true }
  }, [brId, killmailId])

  if (loading) return <p className="dim">Loading…</p>
  if (error) return <p className="error-text">{error}</p>
  if (!damage) return null

  const damageTaken = damage.damage_taken ?? damage.total_attributed

  return (
    <div data-testid="loss-detail-panel">
      {/* Effective tank summary */}
      <div style={{ marginBottom: '0.75rem', fontSize: '0.85rem' }}>
        <span style={{ fontWeight: 600 }}>
          absorbed {damageTaken.toLocaleString()} damage
        </span>
        {' '}before dying
      </div>

      {/* Attacker breakdown */}
      <div style={{ marginBottom: '0.75rem' }}>
        <div className="dim" style={{ fontSize: '0.75rem', marginBottom: '0.3rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          Attackers
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <tbody>
            {damage.attackers.map((atk, idx) => (
              <tr key={atk.character_id ?? `anon-${idx}`} data-testid="attacker-row">
                <td style={{ padding: '0.15rem 0.3rem' }} title={atk.character_name ?? undefined}>
                  {atk.character_name ?? '(unknown)'}
                  {atk.final_blow && (
                    <span
                      data-testid="final-blow-marker"
                      title="Final blow"
                      style={{ marginLeft: '0.35rem', fontSize: '0.72rem', color: 'var(--bad)', fontWeight: 600 }}
                    >
                      ✦ FB
                    </span>
                  )}
                </td>
                <td style={{ padding: '0.15rem 0.3rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                  {fmtCompact(atk.damage_done)}
                </td>
                <td style={{ padding: '0.15rem 0.3rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: 'var(--text-dim)' }}>
                  {(atk.share * 100).toFixed(1)}%
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Item losses */}
      {items && items.slots.length > 0 && (
        <div>
          <div className="dim" style={{ fontSize: '0.75rem', marginBottom: '0.3rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            Items
          </div>
          {items.slots.map((slot) => (
            <div key={slot.location} style={{ marginBottom: '0.4rem' }}>
              <div className="dim" style={{ fontSize: '0.72rem', marginBottom: '0.15rem' }}>
                {slot.location}
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
                <tbody>
                  {slot.items.map((item) => (
                    <tr key={item.type_id} data-testid="item-row">
                      <td style={{ padding: '0.12rem 0.3rem' }}>
                        <img
                          src={`https://images.evetech.net/types/${item.type_id}/icon?size=32`}
                          width={16}
                          height={16}
                          alt=""
                          style={{ verticalAlign: 'middle', marginRight: '0.3rem' }}
                        />
                        {item.name}
                      </td>
                      <td style={{ padding: '0.12rem 0.3rem', textAlign: 'right', whiteSpace: 'nowrap', fontSize: '0.75rem' }}>
                        {item.qty_destroyed > 0 && (
                          <span title="destroyed" style={{ color: 'var(--bad)', marginRight: '0.4rem' }}>
                            ✗ {item.qty_destroyed}
                          </span>
                        )}
                        {item.qty_dropped > 0 && (
                          <span title="dropped" style={{ color: 'var(--ok)' }}>
                            ↓ {item.qty_dropped}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
