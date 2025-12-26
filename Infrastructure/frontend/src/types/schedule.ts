/** Types for schedule data. */

export interface Schedule {
  id: number;
  name: string;
  location: string;
  cluster: string;
  device_name: string;
  day_of_week: number | null;  // 0-6 or null for daily
  start_time: string;  // HH:MM format
  end_time: string;    // HH:MM format
  enabled: boolean;
  mode?: string;  // DAY, NIGHT, TRANSITION
  target_intensity?: number | null;  // 0-100% for light ramp schedules
  ramp_up_duration?: number | null;  // Minutes to ramp up (0 = instant)
  ramp_down_duration?: number | null;  // Minutes to ramp down (0 = instant)
  created_at: string;
}

export interface ScheduleCreate {
  name: string;
  location: string;
  cluster: string;
  device_name: string;
  day_of_week?: number | null;
  start_time: string;
  end_time: string;
  enabled?: boolean;
  mode?: string;
  target_intensity?: number | null;  // 0-100% for light ramp schedules
  ramp_up_duration?: number | null;  // Minutes to ramp up (0 = instant)
  ramp_down_duration?: number | null;  // Minutes to ramp down (0 = instant)
}

export interface ScheduleUpdate {
  name?: string;
  day_of_week?: number | null;
  start_time?: string;
  end_time?: string;
  enabled?: boolean;
  mode?: string;
  target_intensity?: number | null;  // 0-100% for light ramp schedules
  ramp_up_duration?: number | null;  // Minutes to ramp up (0 = instant)
  ramp_down_duration?: number | null;  // Minutes to ramp down (0 = instant)
}

