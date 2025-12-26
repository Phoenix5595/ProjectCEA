/** Hardcoded zone definitions for the CEA system. */

export interface Zone {
  location: string;
  cluster: string;
}

export const ZONES: Zone[] = [
  { location: "Flower Room", cluster: "main" },
  { location: "Veg Room", cluster: "main" },
  { location: "Lab", cluster: "main" }
];

/** Display name mapping for locations (for UI display only) */
export function getLocationDisplayName(location: string): string {
  const displayNames: Record<string, string> = {
    "Veg Room": "Vegetation Room",
    "Flower Room": "Flower Room",
    "Lab": "Lab"
  };
  return displayNames[location] || location;
}

/** Reverse mapping: convert display name back to backend location name */
export function getLocationBackendName(displayName: string): string {
  const reverseMap: Record<string, string> = {
    "Vegetation Room": "Veg Room",
    "Flower Room": "Flower Room",
    "Lab": "Lab"
  };
  return reverseMap[displayName] || displayName;
}

export function getZoneKey(location: string, cluster: string): string {
  return `${location}:${cluster}`;
}

export function parseZoneKey(key: string): { location: string; cluster: string } | null {
  const parts = key.split(':');
  if (parts.length !== 2) {
    return null;
  }
  return { location: parts[0], cluster: parts[1] };
}

