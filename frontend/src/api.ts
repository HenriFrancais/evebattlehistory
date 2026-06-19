// Typed wrappers for the NV Battle Reports FastAPI backend.

export interface MeResponse {
  user_name: string
  user_rank: string
  user_teams: string[]
  main_character_id: string
  can_create_br: boolean
  impersonation_available: boolean
}

export interface RosterUserOut {
  user_name: string
  main_character_id: number | null
  rank: string
}

export interface BrListSummary {
  total: number
  wins: number
  ties: number
  losses: number
  win_rate: number
  total_isk_destroyed: number
  total_isk_lost: number
}

export interface BrSummary {
  br_id: string
  title: string | null
  source: string
  source_url: string | null
  status: string
  progress_pct: number
  result: string | null
  isk_efficiency: number | null
  our_isk_destroyed: number
  our_isk_lost: number
  fight_count: number
  battle_at: string | null
  created_at: string
}

export interface BrListResponse {
  summary: BrListSummary
  brs: BrSummary[]
}

export interface FightSideOut {
  side_idx: number
  side_kind: string | null // 'friendly' | 'hostile' | 'unassigned'
  pilot_count: number
  isk_lost: number
  losses: number
}

export interface FightOut {
  fight_id: number
  system_id: number
  started_at: string | null
  ended_at: string | null
  isk_destroyed_total: number
  largest_side_pilots: number
  sides: FightSideOut[]
}

export interface BrDetail extends BrSummary {
  fights: FightOut[]
}

export interface BrStatus {
  br_id: string
  status: string
  progress_pct: number
  error_text: string | null
}

export interface BrCreated {
  br_id: string
  status: string
}

// E4b: multi-source types
export interface BrSourceIn {
  kind: 'link' | 'window'
  url?: string
  system_id?: number
  window_start?: string  // ISO UTC string
  window_end?: string    // ISO UTC string
  label?: string
}

export interface BrSourceOut {
  source_id: number
  br_id: string
  kind: string
  url: string | null
  system_id: number | null
  window_start: string | null
  window_end: string | null
  label: string | null
  status: string
  error_text: string | null
  km_count: number
}

export interface CreateBrPayload {
  url?: string
  title?: string
  sources?: BrSourceIn[]
}

export interface LogUploadResult {
  filename: string
  file_id: string | null
  status: 'parsed' | 'unresolved' | 'duplicate' | 'error'
  event_count: number
  character_name: string | null
  message: string | null
}

export interface MyLogFile {
  file_id: string
  filename: string
  character_id: number | null
  character_name: string | null
  listener_name: string | null
  parse_status: string
  event_count: number
  log_start_at: string | null
  log_end_at: string | null
  uploaded_at: string | null
}

export interface CharacterCoverage {
  character_id: number
  character_name: string
  participated_fights: number[]
  covered: boolean
  fights_covered: number[]
  fights_missing: number[]
  /** True iff character appeared on ≥1 killmail in the BR (E1) */
  on_killmail?: boolean
  /** True iff character has ≥1 LogEvent stamped for the BR (E1) */
  has_logs?: boolean
}

/** BR participant: union of killmail participants and log-only characters (E1) */
export interface ParticipantInfo {
  character_id: number
  character_name: string | null
  user_name: string | null
  on_killmail: boolean
  has_logs: boolean
  fight_ids: number[]
}

export interface UserCoverage {
  user_name: string
  characters: CharacterCoverage[]
}

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

// Predicate tree types
export type FilterOp = '>=' | '<=' | '>' | '<' | '==' | '!=' | 'in' | 'between'
export type FilterSide = 'friendly' | 'hostile' | 'any'

export interface FilterLeaf {
  field: string
  op: FilterOp
  value: string | number | boolean | string[] | number[] | [string, string]
}

export interface FilterShipLeafBr {
  field: 'ship_fielded'
  ship: string
  op: '>=' | '<=' | '>' | '<' | '=='
  count: number
  side: 'friendly' | 'any'
}

export interface FilterShipLeafFight {
  field: 'ship_count'
  ship: string
  op: '>=' | '<=' | '>' | '<' | '=='
  count: number
  side: FilterSide
}

export type FilterClause = FilterLeaf | FilterShipLeafBr | FilterShipLeafFight | FilterGroup
export interface FilterGroup {
  op: 'and' | 'or'
  clauses: FilterClause[]
}

// Filtered BR response (same shape as BrListResponse)
export interface FilteredBrResponse {
  summary: BrListSummary
  brs: BrSummary[]
}

// Fight with br_id for filter results
export interface FightWithBrId extends FightOut {
  br_id: string
}

// Reconcile types
export interface CharacterReconcileRow {
  character_id: number
  character_name: string | null
  log_damage_out: number
  log_damage_in: number
  km_damage_attributed: number
  delta: number
}

export interface DpsPoint {
  bucket_ts_epoch: number
  sum_damage_out: number
}

export interface FightReconcile {
  rows: CharacterReconcileRow[]
  dps_series: DpsPoint[]
}

// EWAR types
export interface EwarRow {
  character_id: number
  effect_type: string
  direction: string
  event_count: number
  first_ts: string
  last_ts: string
}

export interface CapRow {
  character_id: number
  effect_type: string
  direction: string
  sum_amount: number
  event_count: number
  first_ts: string
  last_ts: string
}

export interface LogiRow {
  character_id: number
  effect_type: string
  direction: string
  sum_amount: number
  event_count: number
  first_ts: string
  last_ts: string
}

export interface FightEwar {
  ewar: EwarRow[]
  cap: CapRow[]
  logi: LogiRow[]
}

// Fleet timeline types (E3)
export interface FleetSeriesItem {
  key: string // "{effect_type}:{direction}"
  effect_type: string
  direction: string // 'out' | 'in'
  metric: string // 'amount' | 'count'
  values: (number | null)[]
}

export interface KillEvent {
  ts: number  // epoch seconds
  killmail_id: number
  victim_character_id: number | null
  victim_character_name: string | null
  victim_ship_name: string
  victim_ship_type_id: number | null
  side_kind: string | null
  isk: number | null
}

export interface FleetTimeline {
  x: number[]
  series: FleetSeriesItem[]
  kills: KillEvent[]
  fights: TimelineFightInfo[]
  bucket_seconds: number
  t_start: number | null
  t_end: number | null
}

export interface Contribution {
  source_character_id: number | null
  source_name: string
  target_name: string
  effect_type: string
  direction: string
  group: string // 'damage' | 'cap' | 'ewar'
  value: number
  module_name: string | null
  icon_type_id: number | null
  weapon_category: string | null
}

export interface ContributionsResponse {
  at: number
  bucket_seconds: number
  rows: Contribution[]
}

export type SideKind = 'friendly' | 'hostile' | 'unassigned'

export interface SideEntity {
  entity_type: 'alliance' | 'corp'
  entity_id: number
  name: string
  side: SideKind
  overridden: boolean
  baseline: boolean
}

export interface BrSides {
  entities: SideEntity[]
  can_edit: boolean
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// ---------------------------------------------------------------------------
// Impersonation (DEV_MODE only): in-memory, never a cookie.
// ---------------------------------------------------------------------------

let _impersonateName: string | null = null
const _impersonateListeners: Array<() => void> = []

/** Set the active impersonation user (or null to clear). */
export function setImpersonateUser(name: string | null): void {
  _impersonateName = name
  _impersonateListeners.forEach((cb) => cb())
}

/** Get the active impersonation user name, or null. */
export function getImpersonateUser(): string | null {
  return _impersonateName
}

/** Subscribe to impersonation changes. Returns an unsubscribe fn. */
export function onImpersonateChange(cb: () => void): () => void {
  _impersonateListeners.push(cb)
  return () => {
    const idx = _impersonateListeners.indexOf(cb)
    if (idx !== -1) _impersonateListeners.splice(idx, 1)
  }
}

function _impersonateHeaders(): Record<string, string> {
  return _impersonateName ? { 'X-Impersonate-User': _impersonateName } : {}
}

async function jsonFetch<T>(input: string, init?: RequestInit): Promise<T> {
  const merged: RequestInit = {
    ...init,
    headers: {
      ..._impersonateHeaders(),
      ...(init?.headers as Record<string, string> | undefined),
    },
  }
  const res = await fetch(input, merged)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
    } catch {
      // non-JSON error body; keep statusText
    }
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

// BASE_URL comes from Vite's `base` config (always ends with "/"). When the
// app is mounted under a path prefix this keeps fetches working correctly.
const API = `${import.meta.env.BASE_URL}api`

export const api = {
  me: () => jsonFetch<MeResponse>(`${API}/me`),
  rosterUsers: () => jsonFetch<RosterUserOut[]>(`${API}/roster/users`),
  listBrs: () => jsonFetch<BrListResponse>(`${API}/brs`),
  createBr: (payload: CreateBrPayload) =>
    jsonFetch<BrCreated>(`${API}/brs`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    }),
  patchBrTitle: (id: string, title: string) =>
    jsonFetch<BrSummary>(`${API}/brs/${id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ title }),
    }),
  getSources: (id: string) => jsonFetch<BrSourceOut[]>(`${API}/brs/${id}/sources`),
  addSource: (id: string, source: BrSourceIn) =>
    jsonFetch<BrCreated>(`${API}/brs/${id}/sources`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(source),
    }),
  deleteSource: (id: string, sourceId: number) =>
    jsonFetch<void>(`${API}/brs/${id}/sources/${sourceId}`, { method: 'DELETE' }),
  refreshBr: (id: string) =>
    jsonFetch<BrStatus>(`${API}/brs/${id}/refresh`, { method: 'POST' }),
  getBr: (id: string) => jsonFetch<BrDetail>(`${API}/brs/${id}`),
  getBrStatus: (id: string) => jsonFetch<BrStatus>(`${API}/brs/${id}/status`),
  uploadLogs: async (files: File[]): Promise<LogUploadResult[]> => {
    const formData = new FormData()
    for (const file of files) {
      formData.append('files', file)
    }
    const res = await fetch(`${API}/logs`, {
      method: 'POST',
      body: formData,
      headers: _impersonateHeaders(),
    })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = await res.json()
        if (typeof body?.detail === 'string') detail = body.detail
      } catch {
        // non-JSON error body; keep statusText
      }
      throw new ApiError(res.status, detail)
    }
    return res.json() as Promise<LogUploadResult[]>
  },
  myLogs: () => jsonFetch<MyLogFile[]>(`${API}/logs/mine`),
  brCoverage: (id: string) => jsonFetch<UserCoverage[]>(`${API}/brs/${id}/coverage`),
  myBrCoverage: (id: string) => jsonFetch<UserCoverage>(`${API}/brs/${id}/my-coverage`),
  brParticipants: (id: string) => jsonFetch<ParticipantInfo[]>(`${API}/brs/${id}/participants`),
  characterTimeline: (brId: string, charId: string) =>
    jsonFetch<CharacterTimeline>(`${API}/brs/${brId}/characters/${charId}/timeline`),
  characterEvents: (brId: string, charId: string, from: number, to: number) =>
    jsonFetch<TimelineEventList>(
      `${API}/brs/${brId}/characters/${charId}/events?from=${from}&to=${to}`
    ),
  filterBrs: (tree: FilterGroup) =>
    jsonFetch<FilteredBrResponse>(`${API}/brs/filter`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tree }),
    }),
  filterFights: (tree: FilterGroup, brId?: string) =>
    jsonFetch<FightWithBrId[]>(`${API}/fights/filter`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ tree, ...(brId ? { br_id: brId } : {}) }),
    }),
  fightReconcile: (brId: string, fightId: string | number) =>
    jsonFetch<FightReconcile>(`${API}/brs/${brId}/fights/${fightId}/reconcile`),
  fightEwar: (brId: string, fightId: string | number) =>
    jsonFetch<FightEwar>(`${API}/brs/${brId}/fights/${fightId}/ewar`),
  fleetTimeline: (brId: string) =>
    jsonFetch<FleetTimeline>(`${API}/brs/${brId}/fleet-timeline`),
  contributions: (brId: string, at: number) =>
    jsonFetch<ContributionsResponse>(`${API}/brs/${brId}/contributions?at=${at}`),
  getSides: (brId: string) => jsonFetch<BrSides>(`${API}/brs/${brId}/sides`),
  setSide: (
    brId: string,
    body: { entity_type: 'alliance' | 'corp'; entity_id: number; side: SideKind | null },
  ) =>
    jsonFetch<BrSides>(`${API}/brs/${brId}/sides`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
}
