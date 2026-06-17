// Typed wrappers for the NV Battle Reports FastAPI backend.

export interface MeResponse {
  user_name: string
  user_rank: string
  user_teams: string[]
  main_character_id: string
  can_create_br: boolean
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
  side_kind: string | null
  pilot_count: number
  isk_lost: number
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

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function jsonFetch<T>(input: string, init?: RequestInit): Promise<T> {
  const res = await fetch(input, init)
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
  listBrs: () => jsonFetch<BrListResponse>(`${API}/brs`),
  createBr: (url: string, title?: string) =>
    jsonFetch<BrCreated>(`${API}/brs`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url, ...(title ? { title } : {}) }),
    }),
  getBr: (id: string) => jsonFetch<BrDetail>(`${API}/brs/${id}`),
  getBrStatus: (id: string) => jsonFetch<BrStatus>(`${API}/brs/${id}/status`),
  uploadLogs: async (files: File[]): Promise<LogUploadResult[]> => {
    const formData = new FormData()
    for (const file of files) {
      formData.append('files', file)
    }
    const res = await fetch(`${API}/logs`, { method: 'POST', body: formData })
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
  characterTimeline: (brId: string, charId: string) =>
    jsonFetch<CharacterTimeline>(`${API}/brs/${brId}/characters/${charId}/timeline`),
  characterEvents: (brId: string, charId: string, from: number, to: number) =>
    jsonFetch<TimelineEventList>(
      `${API}/brs/${brId}/characters/${charId}/events?from=${from}&to=${to}`
    ),
}
