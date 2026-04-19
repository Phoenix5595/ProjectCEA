/**
 * Cluster topology contract — frontend mirror of
 * `Infrastructure/shared/cluster_topology.py`.
 *
 * This is the **frontend source of truth** for the room → cluster
 * mapping. The Python module of the same shape is the backend's source
 * of truth; the two files are intentionally tiny so the parity cost is
 * cheap. CI doesn't enforce parity yet — treat any change to one as a
 * change to both, and update `ProjectCEA/AGENTS.md` →
 * "Cluster Topology Contract" if the rules themselves change.
 *
 * Two terms, kept distinct:
 *
 * - **Device cluster** — room-wide actuator namespace. Always `"main"`.
 *   Used by `/api/devices/{room}/{cluster}`, `/api/lights/...`,
 *   `/api/control/...`. Every room has exactly one.
 * - **Sensor sub-cluster** — *physically* distinct sensor groupings.
 *   Only Flower Room is split today (`front` + `back`). Veg / Lab /
 *   Outside have NO sensor sub-clusters (they are unsplit rooms).
 *
 * Hierarchy: device cluster `main` is the **parent**; sensor
 * sub-clusters `front` / `back` are **children** of Flower's `main`.
 * `main` is a device-cluster name only — never registered as a sensor
 * sub-cluster. The single place `main` appears in a sensor URL is for
 * unsplit rooms (Veg / Lab / Outside), where the URL slot reuses the
 * device-cluster name as a *room-wide sentinel* meaning "this room
 * has no physical sub-grouping". `sensorSubclustersFor("Veg Room")`
 * returns `[]` precisely so this distinction stays visible in code.
 *
 * Why a separate file from `zones.ts`:
 *
 * - `zones.ts` is loaded with helpers that mix *policy* (which clusters
 *   exist) with *behaviour* (display names, normalization, bulk-key
 *   construction). When the policy was inlined there, every room-aware
 *   feature ended up with its own subtle re-encoding of the rules.
 * - This file owns the policy alone, and `zones.ts` now imports from
 *   it. Adding a sensor sub-cluster to (say) Veg Room now means
 *   editing `TOPOLOGY` here and `_TOPOLOGY` in the Python module —
 *   nothing else.
 */

export interface RoomTopology {
  /** Device cluster name; always `"main"` today. */
  readonly deviceCluster: string;
  /**
   * Ordered list of *physical* sensor sub-cluster identifiers under
   * this room. `[]` for rooms with no physical sub-grouping (Veg /
   * Lab / Outside). `"main"` is **never** a member of this list —
   * see module docstring.
   */
  readonly sensorSubclusters: readonly string[];
}

/**
 * Canonical registry. Add or change rooms here; do **not** introduce
 * room-keyed conditionals elsewhere in the codebase.
 *
 * Keep keys identical to the backend room names (the Python registry
 * uses the same strings; mismatches surface as 404 from the API
 * because `UnknownRoomError` resolves to a 404 response).
 */
export const TOPOLOGY: Readonly<Record<string, RoomTopology>> = {
  'Flower Room': {
    deviceCluster: 'main',
    // Order matters for UI (front first) and for deterministic poll fan-out.
    sensorSubclusters: ['front', 'back'],
  },
  'Veg Room': {
    deviceCluster: 'main',
    sensorSubclusters: [],
  },
  Lab: {
    deviceCluster: 'main',
    sensorSubclusters: [],
  },
  Outside: {
    deviceCluster: 'main',
    sensorSubclusters: [],
  },
};

export function knownRooms(): readonly string[] {
  return Object.keys(TOPOLOGY);
}

export function deviceClusterFor(room: string): string {
  return TOPOLOGY[room]?.deviceCluster ?? 'main';
}

/**
 * Return the *physical* sensor sub-clusters under a room. Returns `[]`
 * for unsplit rooms — use `sensorUrlClustersFor` instead if you want
 * the URL slugs to fan out polling against `/api/sensors/...`.
 */
export function sensorSubclustersFor(room: string): readonly string[] {
  return TOPOLOGY[room]?.sensorSubclusters ?? [];
}

/**
 * Return the URL `{cluster}` slugs accepted by
 * `/api/sensors/{room}/...`. For rooms with sub-clusters, this is the
 * sub-cluster list. For unsplit rooms it is `[deviceCluster]` — the
 * device-cluster name reused as a sentinel so the URL shape stays
 * uniform across rooms. Mirrors `sensor_url_clusters_for` in the
 * Python module.
 */
export function sensorUrlClustersFor(room: string): readonly string[] {
  const t = TOPOLOGY[room];
  if (!t) return ['main'];
  return t.sensorSubclusters.length > 0 ? t.sensorSubclusters : [t.deviceCluster];
}

/** @deprecated Prefer `sensorUrlClustersFor`. */
export function sensorClustersFor(room: string): readonly string[] {
  return sensorUrlClustersFor(room);
}

export function isDeviceCluster(room: string, cluster: string): boolean {
  return TOPOLOGY[room]?.deviceCluster === cluster;
}

/**
 * True only for *physical* sensor sub-clusters (Flower's `front`/`back`).
 * Returns `false` for the device-cluster sentinel even on unsplit rooms
 * where `cluster === 'main'` is a valid sensor URL slug.
 */
export function isSensorSubcluster(room: string, cluster: string): boolean {
  return TOPOLOGY[room]?.sensorSubclusters.includes(cluster) ?? false;
}

/** True if `cluster` is any valid sensor URL slug for `room`. */
export function isSensorCluster(room: string, cluster: string): boolean {
  return sensorUrlClustersFor(room).includes(cluster);
}
