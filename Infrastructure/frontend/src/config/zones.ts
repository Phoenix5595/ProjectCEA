/**
 * Zone helpers built on top of `clusterTopology.ts`.
 *
 * Phase 5e: this file used to inline the Flower-specific cluster list
 * (`FLOWER_DASHBOARD_CLUSTERS = ['front', 'back']`) and the per-room
 * device cluster list, which made the room → cluster knowledge live in
 * three places (here, `clusterTopology.ts`, and a half-dozen call
 * sites). Now everything threads through the topology registry; the
 * exports below are kept for back-compat so the rest of the SPA does
 * not need a coordinated rename.
 */

import {
  deviceClusterFor,
  knownRooms,
  sensorClustersFor,
  TOPOLOGY,
} from './clusterTopology';

export interface Zone {
  location: string;
  cluster: string;
}

/**
 * Device-plane zones (one entry per room, cluster = device cluster).
 * Use this for actuator polling and control. Iterating sensor
 * sub-clusters against `/api/devices/...` produced the 404 noise the
 * Phase 5e fix is removing — that mistake lives in
 * `getDashboardPollZones()` below, which is **only** for sensor-plane
 * polling.
 */
export const ZONES: Zone[] = knownRooms()
  .filter((r) => r !== 'Outside') // Outside has no device-plane UI today.
  .map((location) => ({ location, cluster: deviceClusterFor(location) }));

/**
 * @deprecated Prefer `sensorClustersFor('Flower Room')`. Kept as a
 * named export so `useSensorPolling`, `getFlowerDualClimateLayers`,
 * and old call sites compile until they're updated. The value is
 * always derived from `TOPOLOGY` so the policy stays single-sourced.
 */
export const FLOWER_DASHBOARD_CLUSTERS: string[] = [
  ...(TOPOLOGY['Flower Room']?.sensorClusters ?? []),
];

/** Vertical order on the main dashboard (matches ZONES order: Flower, Veg, Lab). */
export const DASHBOARD_ROW_ZONES: Zone[] = [...ZONES];

/**
 * Sensor-plane zones — one entry per `(room, sensor sub-cluster)`.
 * Use this for `/api/sensors/{room}/{cluster}` and `/live` polling;
 * Flower fans out into `front` + `back`, every other room is `main`.
 *
 * Pre-Phase-5e the only available list was `getDashboardPollZones()`,
 * which is a *superset* (it also includes the device cluster). Calling
 * the sensor endpoint with the device cluster used to silently return
 * `{}`; the Phase 5e backend now returns 400 on that mismatch, so a
 * dedicated sensor-only list is required here.
 */
export function getSensorPollZones(): Zone[] {
  const out: Zone[] = [];
  for (const location of knownRooms()) {
    if (location === 'Outside') continue; // No dashboard rows for Outside.
    for (const cluster of sensorClustersFor(location)) {
      out.push({ location, cluster });
    }
  }
  return out;
}

/**
 * Union of every `(location, cluster)` pair the dashboard touches —
 * device clusters **and** sensor sub-clusters. This is the right set
 * for sensor-plane polling (`/api/sensors/...` and live snapshots)
 * and for bulk Redis-key fan-out (`buildDashboardBulkSensorKeys`),
 * which mixes per-room setpoints (keyed by the *device* cluster, e.g.
 * `Flower Room_main_heating_setpoint`) with per-cluster live values
 * (e.g. `Flower Room_front_dry_bulb_f`).
 *
 * **Do not** pass the result to `/api/devices/...` — Flower's `front`
 * and `back` are sensor sub-clusters and the device endpoint will
 * (correctly) reject them with a 400 + hint. Use `ZONES` directly for
 * device polling. Phase 5e fixed `useSensorPolling` to do exactly that.
 */
export function getDashboardPollZones(): Zone[] {
  const seen = new Set<string>();
  const out: Zone[] = [];
  // Device clusters first (legacy ordering — preserves Flower's `main`
  // entry at the top so dashboard rows render in their historical order).
  for (const z of ZONES) {
    const k = `${z.location}\0${z.cluster}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push(z);
    }
  }
  // Then sensor sub-clusters that aren't already represented.
  for (const location of knownRooms()) {
    if (location === 'Outside') continue;
    for (const cluster of sensorClustersFor(location)) {
      const k = `${location}\0${cluster}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ location, cluster });
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
