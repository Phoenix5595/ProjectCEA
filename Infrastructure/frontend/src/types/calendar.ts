/** Calendar event DTO from automation API. */
export interface CalendarEventDto {
  id: string;
  source: 'manual' | 'mode_transition' | 'crop_batch';
  eventType: string;
  title: string;
  start: string;
  end?: string;
  location: string;
  cluster?: string;
  editable: boolean;
  colorKey: string;
  numericId?: number;
  notes?: string;
  metadata?: Record<string, unknown>;
}

export interface CalendarRoomProfile {
  location: string;
  display_name: string;
  color_key: string;
  sort_order: number;
}

export interface CalendarEventsResponse {
  items: CalendarEventDto[];
  next_cursor?: string | null;
}

export interface FlowerGrowPlanRequest {
  idempotency_key: string;
  crop_name: string;
  environment: 'indoor' | 'outdoor';
  flower_end: string;
  flower_weeks: number;
  include_pot_phases: boolean;
  clone_weeks?: number;
  pot_weeks?: number;
  bed_weeks?: number;
  stretch_days?: number;
  ripen_days?: number;
  drying_days?: number;
  auto_mode_transition?: boolean;
}

export interface ModeScheduleResponse {
  date: string;
  expected: {
    mode_name: string | null;
    submode_name: string | null;
    event_type?: string;
    title?: string;
  };
  active: {
    mode_name: string | null;
    submode_name: string | null;
  };
}

export const ROOM_CALENDAR_COLORS: Record<string, string> = {
  'Flower Room': 'bg-amber-500',
  'Veg Room': 'bg-emerald-500',
  Lab: 'bg-slate-500',
};

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
};
