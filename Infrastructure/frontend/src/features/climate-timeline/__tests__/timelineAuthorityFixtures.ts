import { CurrentSnapshot, FutureProjection } from '../../monitoring/api'
import { RichTrajectoryEnvelope } from '../api/contracts'
import type { ClimatePeriod } from '../../../types/climatePeriod'

export const authorityNow = Date.parse('2026-01-01T12:00:12.345Z')
export const authorityWindow = { start: Date.parse('2026-01-01T00:00:00.000Z'), end: Date.parse('2026-01-02T00:00:00.000Z') }
export const authorityVersion = { contract_version: 1 as const, config_version: 37, revision: '0000009' }
export const authorityPeriod: ClimatePeriod = { period_name: 'All day', start_time: '00:00', end_time: '00:00', ramp_minutes: 0,
  heating_setpoint: 19, cooling_setpoint: null, vpd_setpoint: null, co2_setpoint: null, details: '' }
export function authorityCurrent(now = authorityNow): CurrentSnapshot {
  return CurrentSnapshot.parse({ version: authorityVersion, observed_at: new Date(now - 100).toISOString(),
    valid_until: new Date(now + 5_000).toISOString(), persistence: { state: 'pending' }, photoperiod: null,
    series: [
      { series_id: { value: 'flower_room.main.setpoint.effective_heating_setpoint' }, value: 22, quality: 'exact',
        observed_at: new Date(now - 100).toISOString(), valid_until: new Date(now + 5_000).toISOString() },
      { series_id: { value: 'flower_room.main.setpoint.profile_mode_id' }, value: 3, quality: 'exact',
        observed_at: new Date(now - 100).toISOString(), valid_until: new Date(now + 5_000).toISOString() },
      { series_id: { value: 'flower_room.main.setpoint.profile_submode_id' }, value: 2, quality: 'exact',
        observed_at: new Date(now - 100).toISOString(), valid_until: new Date(now + 5_000).toISOString() },
    ] })
}
export function authorityFuture(now = authorityNow): FutureProjection {
  return FutureProjection.parse({ version: authorityVersion, generated_at: new Date(now - 100).toISOString(),
    valid_from: new Date(now).toISOString(), valid_until: new Date(now + 3_600_000).toISOString(),
    series: [{ series_id: { value: 'climate.heating_setpoint_target' }, quality: 'estimated', value: 24,
      valid_from: new Date(now).toISOString(), valid_until: new Date(now + 3_600_000).toISOString() }] })
}
export function authorityEnvelope(value = 18, draftRevision: number | null = null): RichTrajectoryEnvelope {
  return RichTrajectoryEnvelope.parse({ contract_version: 1, room: 'Flower Room', generated_at: new Date(authorityNow).toISOString(),
    window: { start: new Date(authorityWindow.start).toISOString(), end: new Date(authorityWindow.end).toISOString(), timezone: 'UTC' },
    revision_scope: draftRevision == null ? 'saved' : 'draft', draft_revision: draftRevision == null ? null : String(draftRevision),
    base_config_revision: '0000025', assumptions: [], warnings: [],
    segments: ['scheduled', 'effective'].map(kind => ({ shape: 'step', value: kind === 'scheduled' ? value : 99,
      start: new Date(authorityWindow.start).toISOString(), end: new Date(authorityWindow.end).toISOString(),
      metric: 'heating_setpoint', unit: 'C', trajectory_kind: kind, quality: 'exact',
      source: { mode: '4', submode: null, config_revision: '0000025', draft_revision: draftRevision == null ? null : String(draftRevision),
        period: { period_id: 'all-day', label: 'All day' } } })) })
}
