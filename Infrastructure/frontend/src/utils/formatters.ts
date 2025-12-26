/** Utility functions for formatting data. */

export function formatTemperature(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${value.toFixed(1)}°C`;
}

export function formatHumidity(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${value.toFixed(1)}%`;
}

export function formatCO2(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${value.toFixed(0)} ppm`;
}

export function formatVPD(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'N/A';
  return `${value.toFixed(2)} kPa`;
}

export function formatTime(timeStr: string): string {
  // Format HH:MM time string
  return timeStr;
}

export function formatDateTime(dateStr: string): string {
  const date = new Date(dateStr);
  return date.toLocaleString();
}

export function formatMode(mode: string | null | undefined): string {
  if (!mode) return 'Default';
  return mode;
}

