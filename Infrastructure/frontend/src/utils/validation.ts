/** Validation utilities for form inputs. */

export interface ValidationResult {
  isValid: boolean;
  error?: string;
}

// Setpoint ranges
const SETPOINT_RANGES = {
  temperature: { min: 10.0, max: 35.0, unit: '°C' },
  humidity: { min: 30.0, max: 90.0, unit: '%' },
  co2: { min: 400.0, max: 2000.0, unit: 'ppm' },
  vpd: { min: 0.0, max: 5.0, unit: 'kPa' },
};

// PID ranges per device type
const PID_RANGES: Record<string, { kp: { min: number; max: number }; ki: { min: number; max: number }; kd: { min: number; max: number } }> = {
  heater: {
    kp: { min: 0.0, max: 100.0 },
    ki: { min: 0.0, max: 1.0 },
    kd: { min: 0.0, max: 10.0 },
  },
  co2: {
    kp: { min: 0.0, max: 50.0 },
    ki: { min: 0.0, max: 0.5 },
    kd: { min: 0.0, max: 5.0 },
  },
};

const VALID_MODES = ['DAY', 'NIGHT', 'TRANSITION'];

export function validateSetpoint(type: keyof typeof SETPOINT_RANGES, value: number): ValidationResult {
  const range = SETPOINT_RANGES[type];
  if (value < range.min || value > range.max) {
    return {
      isValid: false,
      error: `${type} setpoint (${value}${range.unit}) must be between ${range.min}${range.unit} and ${range.max}${range.unit}`,
    };
  }
  return { isValid: true };
}

export function validatePIDParameter(deviceType: string, param: 'kp' | 'ki' | 'kd', value: number): ValidationResult {
  const ranges = PID_RANGES[deviceType];
  if (!ranges) {
    return { isValid: false, error: `Unknown device type: ${deviceType}` };
  }

  const range = ranges[param];
  if (value < range.min || value > range.max) {
    return {
      isValid: false,
      error: `${param.toUpperCase()} for ${deviceType} (${value}) must be between ${range.min} and ${range.max}`,
    };
  }
  return { isValid: true };
}

export function validateMode(mode: string): ValidationResult {
  if (!VALID_MODES.includes(mode.toUpperCase())) {
    return {
      isValid: false,
      error: `Invalid mode: ${mode}. Valid modes: ${VALID_MODES.join(', ')}`,
    };
  }
  return { isValid: true };
}

export function validateTime(timeStr: string): ValidationResult {
  const timeRegex = /^([0-1][0-9]|2[0-3]):[0-5][0-9]$/;
  if (!timeRegex.test(timeStr)) {
    return {
      isValid: false,
      error: 'Time must be in HH:MM format (24-hour)',
    };
  }
  return { isValid: true };
}

export function validateScheduleTimeRange(startTime: string, endTime: string): ValidationResult {
  const startResult = validateTime(startTime);
  if (!startResult.isValid) {
    return startResult;
  }

  const endResult = validateTime(endTime);
  if (!endResult.isValid) {
    return endResult;
  }

  // Check if start < end (or handle overnight schedules)
  const [startHour, startMin] = startTime.split(':').map(Number);
  const [endHour, endMin] = endTime.split(':').map(Number);
  const startMinutes = startHour * 60 + startMin;
  const endMinutes = endHour * 60 + endMin;

  if (startMinutes === endMinutes) {
    return {
      isValid: false,
      error: 'Start time and end time cannot be the same',
    };
  }

  return { isValid: true };
}

