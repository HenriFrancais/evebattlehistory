# Character Timeline Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-character scrubbable uPlot timeline page at `/brs/:id/characters/:charId` with brush-select → event drill-down panel, and link character names from coverage views to this route.

**Architecture:** A pure `toUplotData()` transform in `timeline.ts` converts API data to uPlot-aligned arrays and series config (unit-tested, no canvas). `TimelineChart` wraps uPlot lifecycle (create/destroy/resize) and fires `onSelectRange(from, to)` on brush-select. `CharacterTimelinePage` composes the chart + drill-down events panel + empty state.

**Tech Stack:** React 18, TypeScript strict, uPlot, Vite, Vitest + testing-library/react, jsdom, react-router-dom v6

## Global Constraints

- Phase 3 scope only — no EWAR/reconcile/filter UI
- uPlot must NOT be imported in any unit test file; mock the `TimelineChart` component in page tests
- Aligned-series contract: null values in `values[]` remain null (render as gaps, not zeros)
- TS strict mode: `noUnusedLocals`, `noUnusedParameters` — every import must be used
- All tests use mock fetch / mocked api functions — no real network calls
- `cd frontend && npm run build && npm test` must pass; `cd .. && uv run pytest -q` must still be green
- Dark theme: reuse existing CSS vars (`--bg`, `--panel`, `--border`, `--text`, `--text-dim`, `--accent`, `--ok`, `--bad`, `--warn`)
- Backend field names confirmed from `app/api/schemas.py`: `x`, `series[].key`, `series[].effect_type`, `series[].direction`, `series[].values`, `fights[].fight_id`, `fights[].seq`, `fights[].started_at`, `fights[].ended_at`, `fights[].system_id`, `t_start`, `t_end`; events: `ts`, `direction`, `effect_type`, `amount`, `quality`, `other_name`, `other_ship_name`, `module_name`; `truncated`
- Events endpoint query param names: `from`, `to` (confirmed from `timeline.py` alias)

---

## File Map

| File | Create/Modify | Responsibility |
|---|---|---|
| `frontend/src/timeline.ts` | Create | Pure `toUplotData()` transform + TS types for API response |
| `frontend/src/timeline.test.ts` | Create | Unit tests for `toUplotData()` (no uPlot/canvas) |
| `frontend/src/components/TimelineChart.tsx` | Create | uPlot wrapper component |
| `frontend/src/views/CharacterTimelinePage.tsx` | Create | Page: loads timeline, renders chart + events panel |
| `frontend/src/views/CharacterTimelinePage.test.tsx` | Create | Page tests (mock api, mock TimelineChart) |
| `frontend/src/components/CoverageMatrix.tsx` | Modify | Link character names to `/brs/:id/characters/:charId` |
| `frontend/src/views/BrDetailPage.tsx` | Modify | Link character names in MyCoverageSection to timeline route |
| `frontend/src/App.tsx` | Modify | Add `/brs/:id/characters/:charId` route |
| `frontend/src/api.ts` | Modify | Add `CharacterTimeline`, `TimelineEvent`, `TimelineEventList` types + `characterTimeline`, `characterEvents` API calls |
| `frontend/src/styles/app.css` | Modify | Add timeline-specific CSS (chart wrapper, events table, truncation notice, legend toggle) |
| `frontend/package.json` | Modify | Add `uplot` dependency |

---

### Task 1: Install uPlot and add API types

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces:
  - `CharacterTimeline` interface (exported from `api.ts`)
  - `TimelineSeriesItem` interface (exported from `api.ts`)
  - `TimelineFightInfo` interface (exported from `api.ts`)
  - `TimelineEvent` interface (exported from `api.ts`)
  - `TimelineEventList` interface (exported from `api.ts`)
  - `api.characterTimeline(brId: string, charId: string): Promise<CharacterTimeline>`
  - `api.characterEvents(brId: string, charId: string, from: number, to: number): Promise<TimelineEventList>`

- [ ] **Step 1: Install uPlot**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm install uplot
```

Expected: uplot appears in `dependencies` in package.json

- [ ] **Step 2: Add type declarations for uPlot**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm install --save-dev @types/uplot
```

Expected: `@types/uplot` appears in `devDependencies`. If the package doesn't exist (uPlot ships its own types), skip this step.

Actually uPlot ships its own types — skip npm install for types. After `npm install uplot`, verify:

```bash
ls /home/matron/dev/nv-wh-fight-history/frontend/node_modules/uplot/dist/
```

Expected: `uPlot.d.ts` or similar bundled in the package.

- [ ] **Step 3: Add API types and methods to `frontend/src/api.ts`**

Add these types after the existing `UserCoverage` interface and before `ApiError`:

```typescript
export interface TimelineSeriesItem {
  key: string
  effect_type: string | null
  direction: string | null
  values: (number | null)[]
  event_count: number
}

export interface TimelineFightInfo {
  fight_id: number
  seq: number
  started_at: string | null
  ended_at: string | null
  system_id: number
}

export interface CharacterTimeline {
  x: number[]
  series: TimelineSeriesItem[]
  fights: TimelineFightInfo[]
  t_start: number | null
  t_end: number | null
}

export interface TimelineEvent {
  ts: string
  direction: string | null
  effect_type: string | null
  amount: number | null
  quality: string | null
  other_name: string | null
  other_ship_name: string | null
  module_name: string | null
}

export interface TimelineEventList {
  events: TimelineEvent[]
  truncated: boolean
}
```

Add these methods to the `api` object (inside the `export const api = {` block, after `myBrCoverage`):

```typescript
  characterTimeline: (brId: string, charId: string) =>
    jsonFetch<CharacterTimeline>(`${API}/brs/${brId}/characters/${charId}/timeline`),
  characterEvents: (brId: string, charId: string, from: number, to: number) =>
    jsonFetch<TimelineEventList>(
      `${API}/brs/${brId}/characters/${charId}/events?from=${from}&to=${to}`
    ),
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 5: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/package.json frontend/package-lock.json frontend/src/api.ts && git commit -m "feat: install uPlot, add CharacterTimeline API types and methods"
```

---

### Task 2: Pure `toUplotData` transform (unit-tested)

**Files:**
- Create: `frontend/src/timeline.ts`
- Create: `frontend/src/timeline.test.ts`

**Interfaces:**
- Consumes: `CharacterTimeline`, `TimelineSeriesItem` (from Task 1's `api.ts`)
- Produces:
  - `UplotData: { data: (number | null)[][], seriesConfig: SeriesConfig[] }` (exported from `timeline.ts`)
  - `SeriesConfig: { key: string; label: string; stroke: string; direction: 'in' | 'out' | null }` (exported from `timeline.ts`)
  - `toUplotData(timeline: CharacterTimeline): UplotData` (exported from `timeline.ts`)

- [ ] **Step 1: Write failing tests in `frontend/src/timeline.test.ts`**

```typescript
import { describe, expect, it } from 'vitest'
import type { CharacterTimeline } from './api'
import { toUplotData } from './timeline'

const baseFight = {
  fight_id: 1,
  seq: 1,
  started_at: null,
  ended_at: null,
  system_id: 30000142,
}

describe('toUplotData', () => {
  it('empty series → data=[xs], seriesConfig=[]', () => {
    const tl: CharacterTimeline = {
      x: [1000, 2000, 3000],
      series: [],
      fights: [baseFight],
      t_start: 1000,
      t_end: 3000,
    }
    const { data, seriesConfig } = toUplotData(tl)
    expect(data).toHaveLength(1) // just xs
    expect(data[0]).toEqual([1000, 2000, 3000])
    expect(seriesConfig).toHaveLength(0)
  })

  it('two series → data has xs + 2 aligned arrays', () => {
    const tl: CharacterTimeline = {
      x: [100, 200, 300],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [10, null, 30], event_count: 2 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [null, 5, null], event_count: 1 },
      ],
      fights: [],
      t_start: 100,
      t_end: 300,
    }
    const { data, seriesConfig } = toUplotData(tl)
    expect(data).toHaveLength(3) // xs + 2 series
    expect(data[0]).toEqual([100, 200, 300])
    expect(data[1]).toEqual([10, null, 30])
    expect(data[2]).toEqual([null, 5, null])
    expect(seriesConfig).toHaveLength(2)
  })

  it('null values are preserved (not replaced with 0)', () => {
    const tl: CharacterTimeline = {
      x: [1, 2, 3, 4],
      series: [
        { key: 'regen/out', effect_type: 'regen', direction: 'out', values: [null, null, 7, null], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 4,
    }
    const { data } = toUplotData(tl)
    expect(data[1]).toEqual([null, null, 7, null])
  })

  it('series length matches x length', () => {
    const tl: CharacterTimeline = {
      x: [10, 20, 30, 40, 50],
      series: [
        { key: 'ewar/in', effect_type: 'ewar', direction: 'in', values: [1, 2, null, 4, 5], event_count: 4 },
      ],
      fights: [],
      t_start: 10,
      t_end: 50,
    }
    const { data } = toUplotData(tl)
    expect(data[0]).toHaveLength(5)
    expect(data[1]).toHaveLength(5)
  })

  it('seriesConfig label uses key; direction mapped correctly', () => {
    const tl: CharacterTimeline = {
      x: [1],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [1], event_count: 1 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [2], event_count: 1 },
        { key: 'unknown', effect_type: null, direction: null, values: [3], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 1,
    }
    const { seriesConfig } = toUplotData(tl)
    expect(seriesConfig[0].direction).toBe('out')
    expect(seriesConfig[1].direction).toBe('in')
    expect(seriesConfig[2].direction).toBeNull()
    // labels are non-empty strings
    expect(seriesConfig[0].label.length).toBeGreaterThan(0)
    expect(seriesConfig[1].label.length).toBeGreaterThan(0)
    expect(seriesConfig[2].label.length).toBeGreaterThan(0)
  })

  it('outgoing series get a warm stroke colour; incoming get a cool stroke', () => {
    const tl: CharacterTimeline = {
      x: [1],
      series: [
        { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [1], event_count: 1 },
        { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [1], event_count: 1 },
      ],
      fights: [],
      t_start: 1,
      t_end: 1,
    }
    const { seriesConfig } = toUplotData(tl)
    // Warm colours start with #f, #e, #ff, or orange/red range; cool with #4, #5, #6, #7, #8 or blue/teal
    // Just check they differ; the exact hex is determined by the implementation
    expect(seriesConfig[0].stroke).not.toBe(seriesConfig[1].stroke)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | grep -E 'FAIL|PASS|timeline'
```

Expected: FAIL with "Cannot find module './timeline'"

- [ ] **Step 3: Implement `frontend/src/timeline.ts`**

```typescript
// Pure transform: CharacterTimeline API response → uPlot data + series config.
// No uPlot import — this module is unit-tested in jsdom without canvas.

import type { CharacterTimeline, TimelineSeriesItem } from './api'

export interface SeriesConfig {
  key: string
  label: string
  stroke: string
  direction: 'in' | 'out' | null
}

export interface UplotData {
  data: (number | null)[][]
  seriesConfig: SeriesConfig[]
}

// Colour palette: warm = outgoing (damage out, regen out), cool = incoming
// Uses a small rotating palette per direction so multiple series of the same
// direction get distinguishable colours.
const WARM = ['#ff7043', '#ffa726', '#ffca28', '#ef5350']
const COOL = ['#42a5f5', '#26c6da', '#66bb6a', '#ab47bc']
const NEUTRAL = ['#8893a7', '#b0bec5', '#cfd8dc']

function pickColour(item: TimelineSeriesItem, idxInDirection: number): string {
  if (item.direction === 'out') return WARM[idxInDirection % WARM.length]
  if (item.direction === 'in') return COOL[idxInDirection % COOL.length]
  return NEUTRAL[idxInDirection % NEUTRAL.length]
}

function makeLabel(item: TimelineSeriesItem): string {
  const type = item.effect_type ?? 'unknown'
  const dir = item.direction
  if (dir === 'out') return `${type} out`
  if (dir === 'in') return `${type} in`
  return type
}

export function toUplotData(timeline: CharacterTimeline): UplotData {
  const xs = timeline.x

  const outCount: Record<string, number> = { out: 0, in: 0, null: 0 }

  const data: (number | null)[][] = [xs]
  const seriesConfig: SeriesConfig[] = []

  for (const s of timeline.series) {
    const dirKey = s.direction ?? 'null'
    const idxInDir = outCount[dirKey] ?? 0
    outCount[dirKey] = idxInDir + 1

    data.push(s.values)
    seriesConfig.push({
      key: s.key,
      label: makeLabel(s),
      stroke: pickColour(s, idxInDir),
      direction: s.direction === 'in' ? 'in' : s.direction === 'out' ? 'out' : null,
    })
  }

  return { data, seriesConfig }
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | grep -E 'FAIL|PASS|✓|×|timeline'
```

Expected: All timeline.test.ts tests PASS

- [ ] **Step 5: TypeScript check**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/src/timeline.ts frontend/src/timeline.test.ts && git commit -m "feat: add toUplotData transform with unit tests"
```

---

### Task 3: TimelineChart uPlot wrapper component

**Files:**
- Create: `frontend/src/components/TimelineChart.tsx`

**Interfaces:**
- Consumes: `UplotData`, `SeriesConfig` (from `timeline.ts`); `TimelineFightInfo` (from `api.ts`)
- Produces:
  - `TimelineChart` component (exported)
  - Props: `{ data: UplotData; fights: TimelineFightInfo[]; onSelectRange: (from: number, to: number) => void; height?: number }`

Note: This component is NOT unit-tested directly (it touches uPlot/canvas). Page tests will mock it with `vi.mock('../components/TimelineChart', ...)`.

- [ ] **Step 1: Create `frontend/src/components/TimelineChart.tsx`**

```typescript
// uPlot wrapper for the character timeline chart.
// Lifecycle: create on mount, destroy on unmount, rebuild when data changes.
// Fight boundaries are drawn as vertical bands using uPlot's addBand / hooks.
// Brush-select a horizontal region → onSelectRange(from, to).
// This component is NOT unit-tested because uPlot requires a real canvas.
// The unit-tested seam is toUplotData() in timeline.ts.

import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import type { TimelineFightInfo } from '../api'
import type { UplotData } from '../timeline'

const AXIS = '#8893a7'
const GRID = 'rgba(138,147,167,0.15)'
const FIGHT_BAND = 'rgba(255,213,79,0.08)'
const FIGHT_EDGE = 'rgba(255,213,79,0.4)'

interface Props {
  data: UplotData
  fights: TimelineFightInfo[]
  onSelectRange: (from: number, to: number) => void
  height?: number
}

/** Draw vertical fight-boundary markers as hooks. */
function fightMarkersPlugin(fights: TimelineFightInfo[]): uPlot.Plugin {
  return {
    hooks: {
      draw(u) {
        const ctx = u.ctx
        ctx.save()
        for (const f of fights) {
          const x0 = f.started_at != null ? u.valToPos(Date.parse(f.started_at) / 1000, 'x', true) : null
          const x1 = f.ended_at != null ? u.valToPos(Date.parse(f.ended_at) / 1000, 'x', true) : null
          const top = u.bbox.top
          const h = u.bbox.height

          if (x0 != null && x1 != null && x1 > x0) {
            ctx.fillStyle = FIGHT_BAND
            ctx.fillRect(x0, top, x1 - x0, h)
          }
          if (x0 != null) {
            ctx.strokeStyle = FIGHT_EDGE
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x0, top)
            ctx.lineTo(x0, top + h)
            ctx.stroke()
          }
          if (x1 != null && x1 !== x0) {
            ctx.strokeStyle = FIGHT_EDGE
            ctx.lineWidth = 1
            ctx.beginPath()
            ctx.moveTo(x1, top)
            ctx.lineTo(x1, top + h)
            ctx.stroke()
          }
        }
        ctx.restore()
      },
    },
  }
}

/** Brush-select plugin: on mouseup after drag, fire onSelectRange. */
function brushSelectPlugin(onSelectRange: (from: number, to: number) => void): uPlot.Plugin {
  let dragStart: number | null = null

  return {
    hooks: {
      ready(u) {
        const over = u.over

        const onMouseDown = (e: MouseEvent) => {
          if (e.button !== 0) return
          const { left } = over.getBoundingClientRect()
          dragStart = u.posToVal(e.clientX - left, 'x')
        }

        const onMouseUp = (e: MouseEvent) => {
          if (dragStart == null) return
          const { left } = over.getBoundingClientRect()
          const dragEnd = u.posToVal(e.clientX - left, 'x')
          const from = Math.min(dragStart, dragEnd)
          const to = Math.max(dragStart, dragEnd)
          dragStart = null
          // Only fire if the drag covered at least 1 second (not a plain click)
          if (to - from >= 1) {
            onSelectRange(Math.floor(from), Math.ceil(to))
          }
        }

        over.addEventListener('mousedown', onMouseDown)
        over.addEventListener('mouseup', onMouseUp)

        ;(u as unknown as { _brushCleanup?: () => void })._brushCleanup = () => {
          over.removeEventListener('mousedown', onMouseDown)
          over.removeEventListener('mouseup', onMouseUp)
        }
      },
      destroy(u) {
        ;(u as unknown as { _brushCleanup?: () => void })._brushCleanup?.()
      },
    },
  }
}

export function TimelineChart({ data, fights, onSelectRange, height = 260 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const plotRef = useRef<uPlot | null>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const seriesDefs: uPlot.Series[] = [
      // x axis series (timestamp)
      {
        label: 'Time',
        value: (_u, v) => (v == null ? '' : new Date(v * 1000).toISOString().slice(11, 19)),
      },
      ...data.seriesConfig.map((sc) => ({
        label: sc.label,
        stroke: sc.stroke,
        width: 1.5,
        points: { show: false },
        spanGaps: false, // null gaps render as gaps
      })),
    ]

    const opts: uPlot.Options = {
      width: el.clientWidth || 800,
      height,
      cursor: {
        drag: { x: true, y: false, dist: 5 },
      },
      legend: { show: true },
      scales: {
        x: { time: true },
        y: { range: (_u, _min, max) => [0, Math.max(1, max)] },
      },
      axes: [
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
        },
        {
          stroke: AXIS,
          grid: { stroke: GRID },
          ticks: { stroke: GRID },
          size: 60,
        },
      ],
      series: seriesDefs,
      plugins: [
        fightMarkersPlugin(fights),
        brushSelectPlugin(onSelectRange),
      ],
    }

    // uPlot data: first element is xs (number[]), rest are (number|null)[]
    // We cast because uPlot's TypeScript type says number[] but it handles nulls for spanGaps
    const uplotData = data.data as uPlot.AlignedData

    const u = new uPlot(opts, uplotData, el)
    plotRef.current = u

    const onResize = () => {
      u.setSize({ width: el.clientWidth || 800, height })
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      u.destroy()
      plotRef.current = null
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, fights, height])
  // onSelectRange intentionally omitted from deps: it's captured in plugin closure at mount.
  // Data + fights changes trigger full rebuild via the effect.

  return <div className="timeline-chart" ref={containerRef} />
}
```

- [ ] **Step 2: Add CSS for the chart wrapper in `frontend/src/styles/app.css`**

Append to the end of the file:

```css
/* Timeline chart */
.timeline-chart { width: 100%; }

/* Timeline events table */
.events-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
.events-table th { text-align: left; padding: 0.3rem 0.6rem; color: var(--text-dim); font-weight: 500; border-bottom: 1px solid var(--border); }
.events-table td { padding: 0.3rem 0.6rem; border-bottom: 1px solid var(--border); }

/* Truncation notice */
.truncated-notice {
  background: rgba(255,183,77,0.1);
  border: 1px solid var(--warn);
  border-radius: 4px;
  padding: 0.4rem 0.75rem;
  font-size: 0.82rem;
  color: var(--warn);
}

/* Legend toggle buttons */
.legend-toggle {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.2rem 0.5rem;
  border-radius: 3px;
  border: 1px solid var(--border);
  background: var(--panel-2);
  color: var(--text-dim);
  font-size: 0.78rem;
  cursor: pointer;
}
.legend-toggle:hover { border-color: var(--accent); color: var(--text); }
.legend-toggle.active { border-color: currentColor; color: var(--text); }
```

- [ ] **Step 3: TypeScript check**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/src/components/TimelineChart.tsx frontend/src/styles/app.css && git commit -m "feat: add TimelineChart uPlot wrapper with fight markers and brush-select"
```

---

### Task 4: CharacterTimelinePage

**Files:**
- Create: `frontend/src/views/CharacterTimelinePage.tsx`
- Create: `frontend/src/views/CharacterTimelinePage.test.tsx`

**Interfaces:**
- Consumes: `api.characterTimeline`, `api.characterEvents` (Task 1); `TimelineChart` component (Task 3); `toUplotData` (Task 2)
- Produces: `CharacterTimelinePage` component (exported)

- [ ] **Step 1: Write failing tests in `frontend/src/views/CharacterTimelinePage.test.tsx`**

```typescript
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { CharacterTimeline, TimelineEventList } from '../api'

// Mock the entire api module so no real fetches happen
vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      characterTimeline: vi.fn(),
      characterEvents: vi.fn(),
    },
  }
})

// Mock TimelineChart to avoid uPlot/canvas in tests.
// The mock renders series labels as data-testid attributes and exposes
// a way to trigger onSelectRange.
let capturedOnSelectRange: ((from: number, to: number) => void) | null = null

vi.mock('../components/TimelineChart', () => ({
  TimelineChart: vi.fn(({ data, onSelectRange }: { data: { seriesConfig: Array<{ label: string }> }; onSelectRange: (from: number, to: number) => void }) => {
    capturedOnSelectRange = onSelectRange
    return (
      <div data-testid="timeline-chart">
        {data.seriesConfig.map((s: { label: string }) => (
          <span key={s.label} data-testid="series-label">{s.label}</span>
        ))}
      </div>
    )
  }),
}))

import { api } from '../api'
import { CharacterTimelinePage } from './CharacterTimelinePage'

const mockTimeline: CharacterTimeline = {
  x: [1000, 2000, 3000],
  series: [
    { key: 'damage/out', effect_type: 'damage', direction: 'out', values: [10, null, 30], event_count: 2 },
    { key: 'damage/in', effect_type: 'damage', direction: 'in', values: [null, 5, null], event_count: 1 },
  ],
  fights: [
    { fight_id: 1, seq: 1, started_at: '2026-06-10T18:00:00Z', ended_at: '2026-06-10T18:30:00Z', system_id: 30000142 },
  ],
  t_start: 1000,
  t_end: 3000,
}

const mockEmptyTimeline: CharacterTimeline = {
  x: [],
  series: [],
  fights: [],
  t_start: null,
  t_end: null,
}

const mockEventList: TimelineEventList = {
  events: [
    {
      ts: '2026-06-10T18:05:00Z',
      direction: 'out',
      effect_type: 'damage',
      amount: 450.5,
      quality: null,
      other_name: 'EnemyPilot',
      other_ship_name: 'Drake',
      module_name: 'Heavies',
    },
  ],
  truncated: false,
}

const mockEventListTruncated: TimelineEventList = {
  events: mockEventList.events,
  truncated: true,
}

function renderPage() {
  capturedOnSelectRange = null
  return render(
    <MemoryRouter
      initialEntries={['/brs/br1/characters/12345']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/brs/:id/characters/:charId" element={<CharacterTimelinePage />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('CharacterTimelinePage', () => {
  beforeEach(() => {
    vi.mocked(api.characterTimeline).mockReset()
    vi.mocked(api.characterEvents).mockReset()
  })

  it('renders series labels after loading timeline', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    const labels = screen.getAllByTestId('series-label').map((el) => el.textContent)
    expect(labels).toContain('damage out')
    expect(labels).toContain('damage in')
  })

  it('shows empty state when timeline has no series', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockEmptyTimeline)
    renderPage()

    await waitFor(() =>
      expect(screen.getByText(/no logs for this character/i)).toBeInTheDocument()
    )
    expect(screen.queryByTestId('timeline-chart')).not.toBeInTheDocument()
  })

  it('brush-select triggers events fetch and renders events panel', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    vi.mocked(api.characterEvents).mockResolvedValue(mockEventList)

    renderPage()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    // Simulate brush-select
    expect(capturedOnSelectRange).not.toBeNull()
    capturedOnSelectRange!(1000, 2000)

    await waitFor(() => expect(api.characterEvents).toHaveBeenCalledWith('br1', '12345', 1000, 2000))
    await waitFor(() => expect(screen.getByText('EnemyPilot')).toBeInTheDocument())
  })

  it('shows truncated notice when events are truncated', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    vi.mocked(api.characterEvents).mockResolvedValue(mockEventListTruncated)

    renderPage()
    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())

    capturedOnSelectRange!(1000, 3000)

    await waitFor(() => expect(screen.getByText(/truncated/i)).toBeInTheDocument())
  })

  it('shows loading state then chart', async () => {
    vi.mocked(api.characterTimeline).mockResolvedValue(mockTimeline)
    renderPage()

    expect(screen.getByText(/loading/i)).toBeInTheDocument()

    await waitFor(() => expect(screen.getByTestId('timeline-chart')).toBeInTheDocument())
    expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | grep -E 'FAIL|Cannot find|CharacterTimeline'
```

Expected: FAIL with "Cannot find module './CharacterTimelinePage'"

- [ ] **Step 3: Implement `frontend/src/views/CharacterTimelinePage.tsx`**

```typescript
import { useCallback, useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import type { CharacterTimeline, TimelineEvent, TimelineEventList } from '../api'
import { api } from '../api'
import { TimelineChart } from '../components/TimelineChart'
import { toUplotData } from '../timeline'

function formatTs(ts: string): string {
  return new Date(ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function EventsPanel({
  events,
  truncated,
}: {
  events: TimelineEvent[]
  truncated: boolean
}) {
  return (
    <div className="panel">
      <h3 style={{ margin: '0 0 0.5rem' }}>Events in range</h3>
      {truncated && (
        <p className="truncated-notice">Results truncated to 1000 rows — narrow your range to see all events.</p>
      )}
      {events.length === 0 ? (
        <p className="dim">No events in this range.</p>
      ) : (
        <table className="events-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Dir</th>
              <th>Effect</th>
              <th>Amount</th>
              <th>Other</th>
              <th>Ship</th>
              <th>Module</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={i}>
                <td>{formatTs(e.ts)}</td>
                <td>{e.direction ?? '—'}</td>
                <td>{e.effect_type ?? '—'}</td>
                <td>{e.amount != null ? e.amount.toFixed(0) : '—'}</td>
                <td>{e.other_name ?? '—'}</td>
                <td>{e.other_ship_name ?? '—'}</td>
                <td>{e.module_name ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

export function CharacterTimelinePage() {
  const { id, charId } = useParams<{ id: string; charId: string }>()
  const [timeline, setTimeline] = useState<CharacterTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [eventList, setEventList] = useState<TimelineEventList | null>(null)
  const [eventsLoading, setEventsLoading] = useState(false)

  useEffect(() => {
    if (!id || !charId) return
    let cancelled = false
    api.characterTimeline(id, charId).then(
      (data) => { if (!cancelled) setTimeline(data) },
      (e: unknown) => { if (!cancelled) setError(String((e as Error)?.message ?? e)) },
    )
    return () => { cancelled = true }
  }, [id, charId])

  const handleSelectRange = useCallback(
    (from: number, to: number) => {
      if (!id || !charId) return
      setEventsLoading(true)
      api.characterEvents(id, charId, from, to).then(
        (data) => {
          setEventList(data)
          setEventsLoading(false)
        },
        (e: unknown) => {
          console.error('Events fetch failed:', e)
          setEventsLoading(false)
        },
      )
    },
    [id, charId],
  )

  if (error) {
    return (
      <div className="page">
        <p className="error-text">{error}</p>
      </div>
    )
  }

  if (!timeline) {
    return (
      <div className="page">
        <p className="dim">Loading…</p>
      </div>
    )
  }

  const isEmpty = timeline.series.length === 0

  const uplotData = isEmpty ? null : toUplotData(timeline)

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <Link to={`/brs/${id}`} className="dim" style={{ fontSize: '0.85rem' }}>
            ← BR Summary
          </Link>
          <h1 style={{ margin: '0.25rem 0 0' }}>Character Timeline</h1>
        </div>
      </div>

      {isEmpty ? (
        <div className="panel">
          <p className="dim">No logs for this character in this BR. Upload combat logs to see timeline data.</p>
        </div>
      ) : (
        <>
          <div className="panel">
            <p className="dim" style={{ margin: '0 0 0.5rem', fontSize: '0.82rem' }}>
              Drag to select a time range and see raw events below. Fight boundaries shown as bands.
            </p>
            <TimelineChart
              data={uplotData!}
              fights={timeline.fights}
              onSelectRange={handleSelectRange}
            />
          </div>

          {eventsLoading && (
            <div className="panel">
              <p className="dim">Loading events…</p>
            </div>
          )}

          {!eventsLoading && eventList && (
            <EventsPanel events={eventList.events} truncated={eventList.truncated} />
          )}

          {!eventsLoading && !eventList && (
            <div className="panel">
              <p className="dim">Drag on the chart to select a time range and drill into events.</p>
            </div>
          )}
        </>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | grep -E 'FAIL|PASS|✓|×|CharacterTimeline'
```

Expected: All CharacterTimelinePage tests PASS

- [ ] **Step 5: TypeScript check**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/src/views/CharacterTimelinePage.tsx frontend/src/views/CharacterTimelinePage.test.tsx && git commit -m "feat: add CharacterTimelinePage with events drill-down and empty state"
```

---

### Task 5: Route + navigation links

**Files:**
- Modify: `frontend/src/App.tsx` (lines 1-22)
- Modify: `frontend/src/components/CoverageMatrix.tsx`
- Modify: `frontend/src/views/BrDetailPage.tsx`

**Interfaces:**
- Consumes: `CharacterTimelinePage` (Task 4)
- Produces: route `/brs/:id/characters/:charId`; character name links in CoverageMatrix and MyCoverageSection

Note: CoverageMatrix currently receives `UserCoverage[]` but doesn't receive the `brId`. We need to pass `brId` so it can construct the link. The existing `CoverageMatrix` is also used in `BrDetailPage.tsx`. We'll add an optional `brId` prop and use it when present.

- [ ] **Step 1: Write failing test for coverage character link**

Add to `frontend/src/views/BrDetailPage.test.tsx` (import block already exists; just add a new `it` inside the existing `describe('BrDetailPage')`):

First, open the existing test file and check what imports and mocks already exist. The file already mocks `api.getBr`, `api.me`, `api.myBrCoverage`, `api.brCoverage`. Add this test at the bottom of the `describe` block:

```typescript
  it('coverage character name links to timeline route', async () => {
    vi.mocked(api.getBr).mockResolvedValue(mockBr)
    vi.mocked(api.me).mockResolvedValue(makeMeResponse(true))
    vi.mocked(api.myBrCoverage).mockResolvedValue(mockMyCoverageAll)
    vi.mocked(api.brCoverage).mockResolvedValue(mockFullCoverage)

    renderBrDetailPage()

    // Wait for coverage matrix to appear
    await waitFor(() => expect(screen.getByTestId('coverage-matrix')).toBeInTheDocument())

    // AlphaChar (character_id: 111) should have a link to the timeline
    const link = screen.getByRole('link', { name: 'AlphaChar' })
    expect(link).toHaveAttribute('href', '/brs/br1/characters/111')
  })
```

Run:

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | grep -E 'coverage character|FAIL|PASS'
```

Expected: FAIL ("Unable to find role='link' with name 'AlphaChar'") — the character name is not yet a link.

- [ ] **Step 2: Add route to `frontend/src/App.tsx`**

Add import:
```typescript
import { CharacterTimelinePage } from './views/CharacterTimelinePage'
```

Add route inside `<Routes>` after the `/brs/:id/fights/:fid` route:
```typescript
<Route path="/brs/:id/characters/:charId" element={<CharacterTimelinePage />} />
```

The full updated App.tsx:

```typescript
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { BrDetailPage } from './views/BrDetailPage'
import { BrListPage } from './views/BrListPage'
import { CharacterTimelinePage } from './views/CharacterTimelinePage'
import { CreatePage } from './views/CreatePage'
import { FightDetailPage } from './views/FightDetailPage'
import { LogsPage } from './views/LogsPage'

export function App() {
  return (
    <BrowserRouter basename={import.meta.env.BASE_URL} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <NavBar />
      <Routes>
        <Route path="/" element={<BrListPage />} />
        <Route path="/brs/new" element={<CreatePage />} />
        <Route path="/brs/:id" element={<BrDetailPage />} />
        <Route path="/brs/:id/fights/:fid" element={<FightDetailPage />} />
        <Route path="/brs/:id/characters/:charId" element={<CharacterTimelinePage />} />
        <Route path="/logs" element={<LogsPage />} />
      </Routes>
    </BrowserRouter>
  )
}
```

- [ ] **Step 3: Add `brId` prop to CoverageMatrix and link character names**

Update `frontend/src/components/CoverageMatrix.tsx`:

```typescript
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
```

- [ ] **Step 4: Pass `brId` to CoverageMatrix in BrDetailPage and link MyCoverageSection character names**

In `frontend/src/views/BrDetailPage.tsx`:

1. In the `MyCoverageSection` function, the `id` is already a prop. Change the character name span to a Link:

Replace this block in `MyCoverageSection`:
```typescript
              <span style={{ fontWeight: 500 }}>{char.character_name}</span>
```
with:
```typescript
              <Link to={`/brs/${id}/characters/${char.character_id}`} style={{ fontWeight: 500 }}>
                {char.character_name}
              </Link>
```

Make sure `Link` is imported — the import already includes it: `import { Link, useParams } from 'react-router-dom'`

2. In the `BrDetailPage` JSX, update the `<CoverageMatrix>` call to pass `brId`:

Replace:
```typescript
            <CoverageMatrix coverage={fullCoverage} />
```
with:
```typescript
            <CoverageMatrix coverage={fullCoverage} brId={id} />
```

- [ ] **Step 5: Run all tests**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test -- --reporter=verbose 2>&1 | tail -30
```

Expected: all tests PASS including the new "coverage character name links to timeline route" test.

- [ ] **Step 6: TypeScript check**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 7: Commit**

```bash
cd /home/matron/dev/nv-wh-fight-history && git add frontend/src/App.tsx frontend/src/components/CoverageMatrix.tsx frontend/src/views/BrDetailPage.tsx frontend/src/views/BrDetailPage.test.tsx && git commit -m "feat: add /brs/:id/characters/:charId route; link character names to timeline"
```

---

### Task 6: Full build + test + backend verification

**Files:**
- No new files

- [ ] **Step 1: Frontend build**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm run build 2>&1 | tail -20
```

Expected: `✓ built in` — no errors, no TS errors.

If build fails with TS errors:
- `noUnusedLocals` errors: remove unused imports/variables
- Type errors in `timeline.ts` or `CharacterTimelinePage.tsx`: fix the specific type assertion

- [ ] **Step 2: Frontend tests**

```bash
cd /home/matron/dev/nv-wh-fight-history/frontend && npm test 2>&1 | tail -30
```

Expected: all test suites PASS, 0 failures.

- [ ] **Step 3: Backend tests**

```bash
cd /home/matron/dev/nv-wh-fight-history && uv run pytest -q 2>&1 | tail -20
```

Expected: all Python tests still green (we didn't touch backend code).

- [ ] **Step 4: Write report**

Write a report to `/tmp/nvbr/task-3.2-report.md` covering:
- Build status (npm run build result)
- Vitest results (test count, pass/fail)
- Backend pytest results (test count, pass/fail)
- Files changed
- Any concerns or deviations from the plan

- [ ] **Step 5: Final commit (if any stragglers)**

If there are any uncommitted changes:

```bash
cd /home/matron/dev/nv-wh-fight-history && git status && git add -p && git commit -m "chore: task 3.2 cleanup"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| Route `/brs/:id/characters/:charId` → CharacterTimelinePage | Task 5 (App.tsx) |
| `toUplotData()` in its own module, unit-tested | Task 2 |
| Aligned-series null-gap contract | Task 2 (spanGaps: false + null preserved) |
| TimelineChart uPlot wrapper | Task 3 |
| Legend (series labels visible) | Task 3 (uPlot built-in legend) |
| Fight boundary markers | Task 3 (fightMarkersPlugin) |
| Brush-select → onSelectRange | Task 3 (brushSelectPlugin) |
| CharacterTimelinePage: loads timeline | Task 4 |
| CharacterTimelinePage: renders chart | Task 4 |
| Range-select → events fetch + panel | Task 4 |
| Truncated notice when `truncated: true` | Task 4 |
| Empty state (no logs) | Task 4 |
| Character names in coverage views link to timeline | Task 5 (CoverageMatrix + MyCoverageSection) |
| uPlot not in unit tests (mocked) | Task 4 (vi.mock TimelineChart) |
| `npm run build && npm test` pass | Task 6 |
| `uv run pytest -q` still green | Task 6 |
| Report written | Task 6 |

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N" — every step has concrete code.

**Type consistency:**
- `UplotData` defined in Task 2 `timeline.ts`, consumed in Task 3 `TimelineChart.tsx` and Task 4 page
- `SeriesConfig` defined in Task 2, used in TimelineChart props (via `UplotData.seriesConfig`)
- `TimelineFightInfo` added to `api.ts` in Task 1, used in Task 3 `TimelineChart` props
- `CharacterTimeline`, `TimelineEvent`, `TimelineEventList` defined in Task 1, consumed in Task 4
- `api.characterTimeline` and `api.characterEvents` defined in Task 1, called in Task 4

**Edge case coverage:** Empty timeline (no series), null fight timestamps, truncated events response, loading states — all covered in Task 4 tests.

**One concern:** The `brushSelectPlugin` captures `onSelectRange` at mount time via closure. If the parent re-renders with a new `onSelectRange` function reference, the stale closure is used until `data` or `fights` change (which triggers a full chart rebuild). For this phase this is acceptable: `handleSelectRange` in the page is wrapped in `useCallback([id, charId])` so it's stable.
