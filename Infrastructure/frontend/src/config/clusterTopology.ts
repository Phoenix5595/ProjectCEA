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
 *
 * Two terms, kept distinct:
 *
 * - **Device cluster** — room-wide actuator namespace; every room's
 *   device cluster is `"main"`. `/api/devices/{room}/{cluster}` etc.
 * - **Sensor sub-cluster** — physically distinct sensor group. Only
 *   Flower Room is split today (front + back); other rooms expose a
 *   single sensor cluster also called `"main"`.
 */

export interface RoomTopology {
  readonly deviceCluster: string;
  readonly sensorClusters: readonly string[];
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
    sensorClusters: ['front', 'back'],
  },
  'Veg Room': {
    deviceCluster: 'main',
    sensorClusters: ['main'],
  },
  Lab: {
    deviceCluster: 'main',
    sensorClusters: ['main'],
  },
  Outside: {
    deviceCluster: 'main',
    sensorClusters: ['main'],
  },
};

export function knownRooms(): readonly string[] {
  return Object.keys(TOPOLOGY);
}

export function deviceClusterFor(room: string): string {
  return TOPOLOGY[room]?.deviceCluster ?? 'main';
}

export function sensorClustersFor(room: string): readonly string[] {
  return TOPOLOGY[room]?.sensorClusters ?? ['main'];
}

export function isDeviceCluster(room: string, cluster: string): boolean {
  return TOPOLOGY[room]?.deviceCluster === cluster;
}

export function isSensorCluster(room: string, cluster: string): boolean {
  return TOPOLOGY[room]?.sensorClusters.includes(cluster) ?? false;
}
