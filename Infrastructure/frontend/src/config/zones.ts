/** Hardcoded zone definitions for the CEA system. */

export interface Zone {
  location: string;
  cluster: string;
}

export const ZONES: Zone[] = [
  // Flower actuators / control APIs use `main` only; `front`/`back` are sensor clusters.
  { location: "Flower Room", cluster: "main" },
  { location: "Veg Room", cluster: "main" },
  { location: "Lab", cluster: "main" }
];

/** Flower sensor clusters for dashboard / live comparison (not device namespaces). */
export const FLOWER_DASHBOARD_CLUSTERS: string[] = ['front', 'back'];

/** Vertical order on the main dashboard (matches ZONES order: Flower, Veg, Lab). */
export const DASHBOARD_ROW_ZONES: Zone[] = [...ZONES];

/**
 * All (location, cluster) pairs to poll for live sensors and bulk keys.
 * Includes ZONES plus any extra Flower clusters not already in ZONES.
 */
export function getDashboardPollZones(): Zone[] {
  const seen = new Set<string>();
  const out: Zone[] = [];
  for (const z of ZONES) {
    const k = `${z.location}\0${z.cluster}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push(z);
    }
  }
  for (const cluster of FLOWER_DASHBOARD_CLUSTERS) {
    if (ZONES.some((z) => z.location === 'Flower Room' && z.cluster === cluster)) continue;
    const k = `Flower Room\0${cluster}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push({ location: 'Flower Room', cluster });
    }
  }
  return out;
}

export interface FlowerClimateLayer {
  label: string;
  cluster: string | null;
}

/** Two UI layers (Front / Back); second uses another cluster when configured, else null (show placeholder, no fake data). */
export function getFlowerDualClimateLayers(): FlowerClimateLayer[] {
  const clusters = FLOWER_DASHBOARD_CLUSTERS;
  return [
    { label: 'Front', cluster: clusters[0] ?? 'front' },
    { label: 'Back', cluster: clusters[1] ?? null },
  ];
}

/** Redis bulk keys for dashboard setpoints / light intensities for all poll zones. */
export function buildDashboardBulkSensorKeys(zones: Zone[]): string[] {
  const keys = new Set<string>();
  for (const z of zones) {
    const p = `${z.location}_${z.cluster}_`;
    if (z.location === 'Lab') {
      keys.add(`${p}lab_temp`);
      keys.add(`${p}water_temperature`);
      continue;
    }
    keys.add(`${p}heating_setpoint`);
    keys.add(`${p}cooling_setpoint`);
    keys.add(`${p}co2_setpoint`);
    keys.add(`${p}vpd_setpoint`);
    for (let i = 1; i <= 3; i++) {
      keys.add(`${p}light_${i}_intensity`);
    }
  }
  return [...keys];
}

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

/**
 * Human-readable cluster label for ribbons and headers.
 * Backend IDs stay `front` / `back`; UI must not show lowercase "back" (reads like browser Back).
 */
export function getClusterDisplayName(location: string, cluster: string): string {
  if (location === 'Flower Room' && cluster === 'front') return 'Front';
  if (location === 'Flower Room' && cluster === 'back') return 'Back';
  const words = cluster.replace(/_/g, ' ').trim();
  if (!words) return cluster;
  return words.replace(/\b\w/g, (c) => c.toUpperCase());
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

/** Control-plane cluster for device APIs (Flower actuators: always `main`). */
export function controlClusterForLocation(location: string): string {
  const z = ZONES.find((zone) => zone.location === location);
  return z?.cluster ?? 'main';
}

/** Map legacy persisted clusters to the control cluster (Flower front/back → main). */
export function normalizeDeviceControlCluster(
  location: string,
  cluster: string | null | undefined
): string {
  if (location === 'Flower Room' && (cluster === 'front' || cluster === 'back')) {
    return 'main';
  }
  if (cluster && cluster.length > 0) {
    return cluster;
  }
  return controlClusterForLocation(location);
}
