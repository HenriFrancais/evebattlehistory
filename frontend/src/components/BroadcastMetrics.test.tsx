import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { BroadcastFileInfo, BroadcastMetrics as Metrics } from '../api'

vi.mock('../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api')>()
  return {
    ...actual,
    api: {
      ...actual.api,
      broadcastMetrics: vi.fn(),
      broadcastFile: vi.fn(),
      uploadBroadcast: vi.fn(),
      deleteBroadcast: vi.fn(),
    },
  }
})
import { api } from '../api'
import { BroadcastMetrics } from './BroadcastMetrics'

const EMPTY: Metrics = {
  has_broadcasts: false,
  summary: {
    n_targets: 0, n_reps: 0, median_time_to_fire_s: null, compliance_rate: null,
    median_logi_response_s: null, median_reaction_s: null, median_damage_lead_s: null,
    late_broadcast_rate: null, false_broadcast_rate: null,
    deaths_total: 0, deaths_flagged: 0,
  },
  targets: { rows: [], per_pilot: [], compliance_rate: null, unanswered_count: 0 },
  reps: { rows: [], false_broadcast_rate: null, median_logi_response_s: null, median_damage_lead_s: null, late_broadcast_rate: null, unresolved_subjects: 0 },
  quality: { total_targets: 0, total_reps: 0, unanswered_targets: 0, false_reps: 0, late_broadcasts: 0, deaths_without_broadcast: 0, unresolved_rep_subjects: 0 },
  deaths: [],
}

const POPULATED: Metrics = {
  has_broadcasts: true,
  summary: {
    n_targets: 2, n_reps: 1, median_time_to_fire_s: 6, compliance_rate: 0.5,
    median_logi_response_s: 5, median_reaction_s: 10, median_damage_lead_s: 4,
    late_broadcast_rate: 0.2, false_broadcast_rate: 0.333,
    deaths_total: 2, deaths_flagged: 1,
  },
  targets: {
    rows: [
      { broadcast_id: 1, ts: 't', subject_name: 'Enemy', subject_ship: 'Rattlesnake', fight_id: 1, first_fire_delta_s: 6, already_primaried: false, complied: true },
      { broadcast_id: 2, ts: 't', subject_name: 'Ghost', subject_ship: 'Sabre', fight_id: 1, first_fire_delta_s: null, already_primaried: false, complied: false },
    ],
    per_pilot: [
      { character_id: 11, character_name: 'Bob', calls_fired: 1, median_switch_s: 6, compliance_rate: 1 },
    ],
    compliance_rate: 0.5, unanswered_count: 1,
  },
  reps: {
    rows: [
      { broadcast_id: 3, ts: 't', subject_name: 'Alice', subject_character_id: 10, resource: 'shield', fight_id: 1, logi_response_s: 5, repper_name: 'Logi Bro', justified: true, reaction_s: 10, damage_lead_s: 4, broadcast_late: false, has_log: true },
    ],
    false_broadcast_rate: 0.333, median_logi_response_s: 5, median_damage_lead_s: 4, late_broadcast_rate: 0.2, unresolved_subjects: 0,
  },
  quality: { total_targets: 2, total_reps: 1, unanswered_targets: 1, false_reps: 0, late_broadcasts: 0, deaths_without_broadcast: 0, unresolved_rep_subjects: 0 },
  deaths: [
    { character_id: 12, character_name: 'Carol', ship: 'Nighthawk', killmail_id: 5001, ts: 't', fight_id: 1, classification: 'late_broadcast', last_broadcast_delta_s: 3 },
    { character_id: 13, character_name: 'Dave', ship: 'Loki', killmail_id: 5002, ts: 't', fight_id: 1, classification: 'ok', last_broadcast_delta_s: 40 },
  ],
}

const FILE: BroadcastFileInfo = {
  file_id: 7, original_filename: 'bc.txt', broadcast_count: 3, uploaded_by_user: 'fc', uploaded_at: 't',
}

beforeEach(() => {
  vi.mocked(api.broadcastMetrics).mockReset()
  vi.mocked(api.broadcastFile).mockReset()
  vi.mocked(api.uploadBroadcast).mockReset()
  vi.mocked(api.deleteBroadcast).mockReset()
})

describe('BroadcastMetrics', () => {
  it('shows an empty state with an upload button for managers', async () => {
    vi.mocked(api.broadcastMetrics).mockResolvedValue(EMPTY)
    vi.mocked(api.broadcastFile).mockResolvedValue(null)
    render(<BroadcastMetrics brId="br1" canManage={true} />)
    await screen.findByTestId('broadcast-empty')
    expect(screen.getByTestId('broadcast-upload-btn')).toBeInTheDocument()
  })

  it('hides management controls from non-managers', async () => {
    vi.mocked(api.broadcastMetrics).mockResolvedValue(EMPTY)
    vi.mocked(api.broadcastFile).mockResolvedValue(null)
    render(<BroadcastMetrics brId="br1" canManage={false} />)
    await screen.findByTestId('broadcast-empty')
    expect(screen.queryByTestId('broadcast-upload-btn')).not.toBeInTheDocument()
  })

  it('renders the fleet-wide summary + manage controls for a populated BR', async () => {
    vi.mocked(api.broadcastMetrics).mockResolvedValue(POPULATED)
    vi.mocked(api.broadcastFile).mockResolvedValue(FILE)
    const onLoaded = vi.fn()
    render(<BroadcastMetrics brId="br1" canManage={true} onLoaded={onLoaded} />)
    await screen.findByTestId('broadcast-metrics')
    // Aggregate summary is shown (per-character tables moved to the Performance panel).
    const summary = within(screen.getByTestId('broadcast-summary'))
    expect(summary.getByText('6.0s')).toBeInTheDocument() // median time-to-fire
    expect(summary.getByText('1/2')).toBeInTheDocument() // flagged deaths
    // No per-character tables here anymore.
    expect(screen.queryByText('Bob')).not.toBeInTheDocument()
    expect(screen.queryByTestId('broadcast-per-pilot')).not.toBeInTheDocument()
    // Management shows Replace/Delete when a file exists.
    expect(screen.getByTestId('broadcast-replace-btn')).toBeInTheDocument()
    expect(screen.getByTestId('broadcast-delete-btn')).toBeInTheDocument()
    expect(onLoaded).toHaveBeenCalledWith(POPULATED)
  })

  it('deletes then re-fetches on confirm', async () => {
    vi.mocked(api.broadcastMetrics).mockResolvedValue(POPULATED)
    vi.mocked(api.broadcastFile).mockResolvedValue(FILE)
    vi.mocked(api.deleteBroadcast).mockResolvedValue({ ok: true })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const onChange = vi.fn()
    render(<BroadcastMetrics brId="br1" canManage={true} onChange={onChange} />)
    await screen.findByTestId('broadcast-metrics')
    await userEvent.click(screen.getByTestId('broadcast-delete-btn'))
    await waitFor(() => expect(api.deleteBroadcast).toHaveBeenCalledWith('br1', 7))
    expect(onChange).toHaveBeenCalled()
  })
})
