import type { components } from '../generated/api'

export type CalendarSyncStatus = 'pending_push' | 'pending_delete' | 'synced' | 'error' | 'conflict'

/** Calendar event DTO from automation API. */
export interface CalendarEventDto {
  id: string
  source: 'manual' | 'mode_transition' | 'crop_batch'
  eventType: string
  title: string
  start: string
  end?: string
  location: string
  cluster?: string
  editable: boolean
  colorKey: string
  numericId?: number
  growPlanId?: string
  notes?: string
  sync_status?: CalendarSyncStatus
  sync_error_code?: string | null
  local_revision?: number
  last_synced_at?: string | null
  metadata?: Record<string, unknown>
}

export interface GrowLifecycle {
  grow_plan_id: string
  crop_batch_id: number
  state: 'open' | 'closed'
  calendar_revision: number
  original_drying_end: string | null
  first_closed_at: string | null
  closed_at: string | null
  anniversary_at: string | null
  dataset_id: string | null
  data_revision: number | null
  published_revision: number | null
}

export interface GrowClosePreview {
  lifecycle: GrowLifecycle
  revision: number
  phase_windows: Array<Record<string, unknown>>
  drying_end: string | null
  affected_counts: Record<string, unknown>
  gaps: Array<Record<string, unknown>>
  scope_unresolved: Array<Record<string, unknown>>
  conflicts: Array<Record<string, unknown>>
  newly_included: Record<string, unknown>
  released: Record<string, unknown>
  unavailable: Array<Record<string, unknown>>
}

export interface CalendarMirrorPreview {
  connection_id: number
  generation: number
  event_count: number
  backup_required: true
}

export interface CalendarSyncResult {
  ok: boolean
  confirmed: number[]
  pending: number[]
  errors: Array<{ event_id: number; code: string }>
  pushed: number
  deleted: number
}

export interface CalendarReconnectResult {
  connection_id: number
  generation: number
  event_count: number
  sync: CalendarSyncResult
}

export interface CalendarTestEntry {
  name: string
  url: string
  files_root_url?: string
  principal_url?: string
}

export type CalendarTestResult = CalendarTestEntry[]

export interface CalendarConnection {
  id: number
  instance_id: string
  generation: number
  files_root_url: string | null
  enabled: boolean
  caldav_base_url: string
  target_calendar_url: string
  account_email: string | null
  display_name: string | null
  last_sync_at: string | null
  last_error: string | null
}

/** Generated OpenAPI request contracts for manual event writes. */
export type CalendarEventCreate = components['schemas']['CalendarEventCreate']
export type CalendarEventUpdate = components['schemas']['CalendarEventUpdate']

/** Unique room-specific calendar run; the GET response can also be null. */
export type CalendarCurrentRunResponse = components['schemas']['CalendarCurrentRunResponse']

export interface CalendarRoomProfile {
  location: string
  display_name: string
  color_key: string
  sort_order: number
}

export interface FlowerCalendarModeTransitionSetting {
  enabled: boolean
}

export interface CalendarEventsResponse {
  items: CalendarEventDto[]
  next_cursor?: string | null
}

export interface FlowerGrowPlanRequest {
  idempotency_key: string
  crop_name: string
  environment: 'indoor' | 'outdoor'
  flower_end: string
  flower_weeks: number
  include_pot_phases: boolean
  clone_weeks?: number
  pot_weeks?: number
  bed_weeks?: number
  stretch_days?: number
  ripen_days?: number
  drying_days?: number
  auto_mode_transition?: boolean
}

export interface ModeScheduleResponse {
  date: string
  expected: {
    mode_name: string | null
    submode_name: string | null
    event_type?: string
    title?: string
  }
  active: {
    mode_name: string | null
    submode_name: string | null
  }
}

export const ROOM_CALENDAR_COLORS: Record<string, string> = {
  'Flower Room': 'bg-amber-500',
  'Veg Room': 'bg-emerald-500',
  Lab: 'bg-slate-500',
}

export const EVENT_TYPE_COLORS: Record<string, string> = {
  clone_window: 'bg-blue-400',
  pot_veg: 'bg-blue-500',
  bed_veg: 'bg-blue-600',
  flip_to_flower: 'bg-amber-400',
  flower_stretch: 'bg-pink-400',
  flower_bulk: 'bg-pink-500',
  flower_ripen: 'bg-pink-600',
  drying: 'bg-amber-600',
  harvest: 'bg-rose-500',
  mode_transition: 'bg-purple-500',
  planned_task: 'bg-cyan-500',
}
