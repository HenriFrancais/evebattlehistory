import { useState } from 'react'
import type {
  FilterClause,
  FilterEntityLeaf,
  FilterGroup,
  FilterLeaf,
  FilterOp,
  FilterShipLeafBr,
  FilterShipLeafFight,
  FilterSide,
} from '../api'

export interface FilterBuilderProps {
  scope: 'br' | 'fight'
  onApply: (tree: FilterGroup) => void
  onClear?: () => void
}

type FieldKind = 'numeric' | 'enum' | 'boolean' | 'datetime'

interface FieldDef {
  label: string
  kind: FieldKind
  ops: string[]
  enumValues?: string[]
  iskBillions?: boolean // multiply value×1e9 in tree
}

const BR_FIELDS: Record<string, FieldDef> = {
  our_isk_destroyed: {
    label: 'ISK Destroyed (billions)',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
    iskBillions: true,
  },
  our_isk_lost: {
    label: 'ISK Lost (billions)',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
    iskBillions: true,
  },
  result: {
    label: 'Result',
    kind: 'enum',
    ops: ['==', 'in'],
    enumValues: ['win', 'loss', 'tie'],
  },
  battle_at: {
    label: 'Battle Date',
    kind: 'datetime',
    ops: ['>=', '<='],
  },
  source: {
    label: 'Source',
    kind: 'enum',
    ops: ['==', 'in'],
    enumValues: ['zkillboard', 'manual'],
  },
}

const FIGHT_FIELDS: Record<string, FieldDef> = {
  isk_destroyed_total: {
    label: 'ISK Destroyed Total',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
  },
  largest_side_pilots: {
    label: 'Largest Side Pilots',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
  },
  distinct_alliance_count: {
    label: 'Alliance Count',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
  },
  capitals_involved: {
    label: 'Capitals Involved',
    kind: 'boolean',
    ops: ['=='],
  },
  system_id: {
    label: 'System ID',
    kind: 'numeric',
    ops: ['>=', '<=', '>', '<', '==', '!='],
  },
  started_at: {
    label: 'Started At',
    kind: 'datetime',
    ops: ['>=', '<='],
  },
}

interface RowState {
  type: 'field' | 'ship' | 'entity'
  // field row
  field: string
  op: string
  value: string
  // ship row
  ship: string
  count: string
  side: string
  // entity row (corp/alliance name substring)
  entity: string
}

function defaultRow(scope: 'br' | 'fight'): RowState {
  const firstField = scope === 'br' ? 'our_isk_destroyed' : 'isk_destroyed_total'
  const firstOp = '>='
  return { type: 'field', field: firstField, op: firstOp, value: '', ship: '', count: '', side: scope === 'br' ? 'friendly' : 'any', entity: '' }
}

function buildClause(row: RowState, scope: 'br' | 'fight'): FilterClause | null {
  if (row.type === 'entity') {
    const name = row.entity.trim()
    if (!name) return null
    const clause: FilterEntityLeaf = { field: 'entity_involved', name }
    return clause
  }

  if (row.type === 'ship') {
    const ship = row.ship.trim()
    const count = parseInt(row.count, 10)
    if (!ship || isNaN(count)) return null
    const op = row.op as '>=' | '<=' | '>' | '<' | '=='
    if (scope === 'br') {
      const clause: FilterShipLeafBr = {
        field: 'ship_fielded',
        ship,
        op,
        count,
        side: (row.side || 'friendly') as 'friendly' | 'any',
      }
      return clause
    } else {
      const clause: FilterShipLeafFight = {
        field: 'ship_count',
        ship,
        op,
        count,
        side: (row.side || 'any') as FilterSide,
      }
      return clause
    }
  }

  // field row
  const fields = scope === 'br' ? BR_FIELDS : FIGHT_FIELDS
  const def = fields[row.field]
  if (!def) return null
  const value = row.value.trim()
  if (!value) return null

  let parsedValue: FilterLeaf['value']
  if (def.kind === 'numeric') {
    const n = parseFloat(value)
    if (isNaN(n)) return null
    parsedValue = def.iskBillions ? n * 1e9 : n
  } else if (def.kind === 'boolean') {
    parsedValue = value === 'true'
  } else {
    parsedValue = value
  }

  const clause: FilterLeaf = {
    field: row.field,
    op: row.op as FilterOp,
    value: parsedValue,
  }
  return clause
}

export function FilterBuilder({ scope, onApply, onClear }: FilterBuilderProps) {
  const [groupOp, setGroupOp] = useState<'and' | 'or'>('and')
  const [rows, setRows] = useState<RowState[]>([defaultRow(scope)])

  const fields = scope === 'br' ? BR_FIELDS : FIGHT_FIELDS
  const fieldKeys = Object.keys(fields)

  function updateRow(i: number, patch: Partial<RowState>) {
    setRows((prev) => prev.map((r, idx) => (idx === i ? { ...r, ...patch } : r)))
  }

  function addRow() {
    setRows((prev) => [...prev, defaultRow(scope)])
  }

  function removeRow(i: number) {
    setRows((prev) => (prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev))
  }

  function handleApply() {
    const clauses: FilterClause[] = []
    for (const row of rows) {
      const clause = buildClause(row, scope)
      if (clause) clauses.push(clause)
    }
    if (clauses.length === 0) return
    onApply({ op: groupOp, clauses })
  }

  function handleClear() {
    setGroupOp('and')
    setRows([defaultRow(scope)])
    onClear?.()
  }

  return (
    <div className="panel" data-testid="filter-builder">
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <span className="stat-label">Match</span>
        <select
          value={groupOp}
          onChange={(e) => setGroupOp(e.target.value as 'and' | 'or')}
          data-testid="filter-group-op"
        >
          <option value="and">ALL (AND)</option>
          <option value="or">ANY (OR)</option>
        </select>
        <span className="dim">of the following</span>
      </div>

      {rows.map((row, i) => (
        <FilterRow
          key={i}
          row={row}
          index={i}
          scope={scope}
          fieldKeys={fieldKeys}
          fields={fields}
          onChange={(patch) => updateRow(i, patch)}
          onRemove={() => removeRow(i)}
        />
      ))}

      <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.75rem' }}>
        <button className="btn" onClick={addRow} data-testid="filter-add-row">
          + Add condition
        </button>
        <button className="btn btn-primary" onClick={handleApply} data-testid="filter-apply">
          Apply
        </button>
        <button className="btn" onClick={handleClear} data-testid="filter-clear">
          Clear
        </button>
      </div>
    </div>
  )
}

interface FilterRowProps {
  row: RowState
  index: number
  scope: 'br' | 'fight'
  fieldKeys: string[]
  fields: Record<string, FieldDef>
  onChange: (patch: Partial<RowState>) => void
  onRemove: () => void
}

function FilterRow({ row, index, scope, fieldKeys, fields, onChange, onRemove }: FilterRowProps) {
  const def = fields[row.field]

  function handleFieldChange(field: string) {
    const newDef = fields[field]
    const newOp = newDef?.ops[0] ?? '>='
    onChange({ field, op: newOp, value: '' })
  }

  function handleTypeChange(type: 'field' | 'ship' | 'entity') {
    const defaultSide = scope === 'br' ? 'friendly' : 'any'
    onChange({ type, op: '>=', value: '', ship: '', count: '', side: defaultSide, entity: '' })
  }

  return (
    <div
      data-testid={`filter-row-${index}`}
      style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.4rem', flexWrap: 'wrap' }}
    >
      <select
        value={row.type}
        onChange={(e) => handleTypeChange(e.target.value as 'field' | 'ship' | 'entity')}
        data-testid={`filter-row-${index}-type`}
      >
        <option value="field">Field</option>
        <option value="ship">Ship</option>
        <option value="entity">Entity</option>
      </select>

      {row.type === 'entity' ? (
        <input
          type="text"
          placeholder="Corp or alliance name"
          value={row.entity}
          onChange={(e) => onChange({ entity: e.target.value })}
          data-testid={`filter-row-${index}-entity`}
          style={{ width: '16rem' }}
        />
      ) : row.type === 'field' ? (
        <>
          <select
            value={row.field}
            onChange={(e) => handleFieldChange(e.target.value)}
            data-testid={`filter-row-${index}-field`}
          >
            {fieldKeys.map((k) => (
              <option key={k} value={k}>
                {fields[k].label}
              </option>
            ))}
          </select>

          <select
            value={row.op}
            onChange={(e) => onChange({ op: e.target.value })}
            data-testid={`filter-row-${index}-op`}
          >
            {(def?.ops ?? ['>=']).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>

          {def?.kind === 'enum' ? (
            <select
              value={row.value}
              onChange={(e) => onChange({ value: e.target.value })}
              data-testid={`filter-row-${index}-value`}
            >
              <option value="">-- select --</option>
              {def.enumValues?.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          ) : def?.kind === 'boolean' ? (
            <select
              value={row.value}
              onChange={(e) => onChange({ value: e.target.value })}
              data-testid={`filter-row-${index}-value`}
            >
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : def?.kind === 'datetime' ? (
            <input
              type="date"
              value={row.value}
              onChange={(e) => onChange({ value: e.target.value })}
              data-testid={`filter-row-${index}-value`}
            />
          ) : (
            <input
              type="text"
              placeholder={def?.iskBillions ? 'value (billions ISK)' : 'value'}
              value={row.value}
              onChange={(e) => onChange({ value: e.target.value })}
              data-testid={`filter-row-${index}-value`}
              style={{ width: '8rem' }}
            />
          )}
        </>
      ) : (
        <>
          <input
            type="text"
            placeholder="Ship name"
            value={row.ship}
            onChange={(e) => onChange({ ship: e.target.value })}
            data-testid={`filter-row-${index}-ship`}
            style={{ width: '8rem' }}
          />
          <select
            value={row.op}
            onChange={(e) => onChange({ op: e.target.value })}
            data-testid={`filter-row-${index}-ship-op`}
          >
            {(['>=', '<=', '>', '<', '=='] as const).map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <input
            type="number"
            placeholder="count"
            value={row.count}
            onChange={(e) => onChange({ count: e.target.value })}
            data-testid={`filter-row-${index}-count`}
            style={{ width: '4rem' }}
          />
          <select
            value={row.side}
            onChange={(e) => onChange({ side: e.target.value })}
            data-testid={`filter-row-${index}-side`}
          >
            <option value="friendly">friendly</option>
            {scope === 'fight' && <option value="hostile">hostile</option>}
            <option value="any">any</option>
          </select>
        </>
      )}

      <button className="btn" onClick={onRemove} data-testid={`filter-row-${index}-remove`}>
        ✕
      </button>
    </div>
  )
}
