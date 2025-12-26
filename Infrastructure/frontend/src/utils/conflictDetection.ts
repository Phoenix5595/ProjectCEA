/** Schedule conflict detection utilities. */

import type { Schedule } from '../types/schedule';

export interface Conflict {
  schedule1: Schedule;
  schedule2: Schedule;
  reason: string;
}

/**
 * Check if two schedules conflict.
 * 
 * Schedules conflict if:
 * - Same location/cluster
 * - Overlapping time ranges
 * - Same day of week (or one/both are daily)
 * - Same mode (if mode-based)
 */
export function checkScheduleConflict(schedule1: Schedule, schedule2: Schedule): Conflict | null {
  // Different locations/clusters don't conflict
  if (schedule1.location !== schedule2.location || schedule1.cluster !== schedule2.cluster) {
    return null;
  }

  // Same schedule doesn't conflict with itself
  if (schedule1.id === schedule2.id) {
    return null;
  }

  // Disabled schedules don't conflict
  if (!schedule1.enabled || !schedule2.enabled) {
    return null;
  }

  // Check day of week overlap
  const dayOverlap = 
    schedule1.day_of_week === null || 
    schedule2.day_of_week === null || 
    schedule1.day_of_week === schedule2.day_of_week;

  if (!dayOverlap) {
    return null; // Different days, no conflict
  }

  // Check mode overlap (if both have modes)
  if (schedule1.mode && schedule2.mode && schedule1.mode !== schedule2.mode) {
    return null; // Different modes, no conflict
  }

  // Check time overlap
  const timeOverlap = checkTimeOverlap(
    schedule1.start_time,
    schedule1.end_time,
    schedule2.start_time,
    schedule2.end_time
  );

  if (timeOverlap) {
    return {
      schedule1,
      schedule2,
      reason: `Time overlap: ${schedule1.start_time}-${schedule1.end_time} overlaps with ${schedule2.start_time}-${schedule2.end_time}`,
    };
  }

  return null;
}

/**
 * Check if two time ranges overlap (handles overnight schedules).
 */
function checkTimeOverlap(
  start1: string,
  end1: string,
  start2: string,
  end2: string
): boolean {
  const [start1Hour, start1Min] = start1.split(':').map(Number);
  const [end1Hour, end1Min] = end1.split(':').map(Number);
  const [start2Hour, start2Min] = start2.split(':').map(Number);
  const [end2Hour, end2Min] = end2.split(':').map(Number);

  const start1Minutes = start1Hour * 60 + start1Min;
  const end1Minutes = end1Hour * 60 + end1Min;
  const start2Minutes = start2Hour * 60 + start2Min;
  const end2Minutes = end2Hour * 60 + end2Min;

  // Handle overnight schedules (end < start means it wraps around midnight)
  const isOvernight1 = end1Minutes < start1Minutes;
  const isOvernight2 = end2Minutes < start2Minutes;

  if (isOvernight1 && isOvernight2) {
    // Both overnight - check overlap in either direction
    return true; // Simplified: assume conflict if both overnight
  } else if (isOvernight1) {
    // Schedule 1 is overnight
    return start2Minutes < end1Minutes || start2Minutes >= start1Minutes;
  } else if (isOvernight2) {
    // Schedule 2 is overnight
    return start1Minutes < end2Minutes || start1Minutes >= start2Minutes;
  } else {
    // Neither overnight - simple overlap check
    return start1Minutes < end2Minutes && end1Minutes > start2Minutes;
  }
}

/**
 * Find all conflicts for a schedule against a list of existing schedules.
 */
export function findConflicts(newSchedule: Schedule, existingSchedules: Schedule[]): Conflict[] {
  const conflicts: Conflict[] = [];
  
  for (const existing of existingSchedules) {
    const conflict = checkScheduleConflict(newSchedule, existing);
    if (conflict) {
      conflicts.push(conflict);
    }
  }
  
  return conflicts;
}

