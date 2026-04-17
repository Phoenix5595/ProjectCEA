/** Sensor polling hook for dashboard data. */
import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../services/api';
import type { Device } from '../types/device';
import type { ControlHistoryEntry } from '../types/device';
import {
  ZONES,
  FLOWER_DASHBOARD_CLUSTERS,
  getDashboardPollZones,
  buildDashboardBulkSensorKeys,
} from '../config/zones';
import { parseLiveResponse } from '../utils/sensorLive';

export interface UseSensorPollingOptions {
  interval?: number;
}

export interface UseSensorPollingReturn {
  devices: Device[];
  sensorData: Record<string, number>;
  controlHistoryByRoom: Record<string, ControlHistoryEntry[]>;
  /** `${location}_${cluster}_${device_name}` → config `display_name` for lights UI */
  lightDisplayNames: Record<string, string>;
  /** Temporary hybrid warning layer: DB/ingestion-observed clusters vs configured clusters. */
  flowerClusterWarnings: string[];
  loading: boolean;
  refresh: () => Promise<void>;
}

async function loadLightDisplayNamesMap(): Promise<Record<string, string>> {
  const pollZones = getDashboardPollZones();
  const pairs = await Promise.all(
    pollZones.map(async (zone) => {
      try {
        const res = await apiClient.getDevicesForLocationCluster(zone.location, zone.cluster);
        const devs = res?.devices as Record<string, { display_name?: string }> | undefined;
        if (!devs) return [] as [string, string][];
        const out: [string, string][] = [];
        for (const [deviceName, info] of Object.entries(devs)) {
          const dn = info?.display_name?.trim();
          if (dn) {
            out.push([`${zone.location}_${zone.cluster}_${deviceName}`, dn]);
          }
        }
        return out;
      } catch {
        return [] as [string, string][];
      }
    })
  );
  return Object.fromEntries(pairs.flat());
}

/**
 * Hook for polling sensor data, devices, and control history.
 * Refreshes live sensors every 5 seconds and control history every 30 seconds.
 */
export function useSensorPolling({ interval = 5000 }: UseSensorPollingOptions = {}): UseSensorPollingReturn {
  const [devices, setDevices] = useState<Device[]>([]);
  const [sensorData, setSensorData] = useState<Record<string, number>>({});
  const [controlHistoryByRoom, setControlHistoryByRoom] = useState<Record<string, ControlHistoryEntry[]>>({});
  const [lightDisplayNames, setLightDisplayNames] = useState<Record<string, string>>({});
  const [flowerClusterWarnings, setFlowerClusterWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);

  const loadInitialData = useCallback(async () => {
    const pollZones = getDashboardPollZones();
    const bulkKeys = buildDashboardBulkSensorKeys(pollZones);
    try {
      const [devicesData, setpointData, nameMap, ...historyResults] = await Promise.all([
        apiClient.getAllDevices().catch(() => []),
        apiClient.getSensorDataBulk(bulkKeys).catch(() => ({})),
        loadLightDisplayNamesMap(),
        ...ZONES.map((zone) => apiClient.getControlHistory(zone.location, zone.cluster, 10).catch(() => []))
      ]);

      if (devicesData) setDevices(devicesData);
      setLightDisplayNames(nameMap);
      if (setpointData && Object.keys(setpointData).length > 0) {
        setSensorData(prev => ({ ...prev, ...setpointData }));
      }

      // Build control history map
      const historyMap: Record<string, ControlHistoryEntry[]> = {};
      ZONES.forEach((zone, index) => {
        const key = `${zone.location}_${zone.cluster}`;
        historyMap[key] = historyResults[index] ?? [];
      });
      setControlHistoryByRoom(historyMap);

    } catch (error) {
      console.error('Error loading initial sensor data:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  // Live sensor polling
  useEffect(() => {
    loadInitialData();

    const refreshLiveSensors = async () => {
      try {
        const pollZones = getDashboardPollZones();
        const liveDataResults = await Promise.all(
          pollZones.map((zone) => apiClient.getLiveSensorData(zone.location, zone.cluster).catch(() => ({})))
        );

        const allLiveFlat: Record<string, number> = {};
        pollZones.forEach((zone, index) => {
          const parsed = parseLiveResponse(zone.location, zone.cluster, liveDataResults[index] as any);
          Object.assign(allLiveFlat, parsed);
        });

        const livePrefixes = pollZones.map((zone) => `${zone.location}_${zone.cluster}_`);
        setSensorData((prev) => {
          // Remove previous live keys for polled zones so disconnected clusters do not keep stale values.
          const next = Object.fromEntries(
            Object.entries(prev).filter(
              ([key]) => !livePrefixes.some((prefix) => key.startsWith(prefix))
            )
          );
          return { ...next, ...allLiveFlat };
        });
      } catch (err) {
        console.warn('Live sensor refresh failed', err);
      }
    };

    refreshLiveSensors();
    const sensorInterval = setInterval(refreshLiveSensors, interval);
    return () => clearInterval(sensorInterval);
  }, [interval, loadInitialData]);

  // Light display names from automation config (120s — matches rare config edits)
  useEffect(() => {
    const refreshNames = async () => {
      const map = await loadLightDisplayNamesMap();
      setLightDisplayNames(map);
    };
    const nameInterval = setInterval(() => void refreshNames(), 120000);
    return () => clearInterval(nameInterval);
  }, []);

  // Temporary hybrid warning layer: compare live discovered Flower clusters with configured ones.
  useEffect(() => {
    const refreshClusterWarnings = async () => {
      try {
        const allLive = await apiClient.getAllLiveSensorData();
        const discovered = new Set<string>();
        for (const row of allLive) {
          const name = row?.sensor ?? '';
          if (name.endsWith('_f')) discovered.add('front');
          if (name.endsWith('_b')) discovered.add('back');
        }
        const configured = new Set(FLOWER_DASHBOARD_CLUSTERS);
        const warnings: string[] = [];

        for (const c of configured) {
          if (!discovered.has(c)) {
            warnings.push(`Configured Flower cluster '${c}' has no live sensor stream.`);
          }
        }
        for (const c of discovered) {
          if (!configured.has(c)) {
            warnings.push(`Live Flower cluster '${c}' is discovered but not configured.`);
          }
        }

        setFlowerClusterWarnings(warnings);
        for (const message of warnings) {
          console.warn(`[flower-cluster-warning] ${message}`);
        }
      } catch (error) {
        setFlowerClusterWarnings(['Failed to compare configured vs discovered Flower clusters.']);
        console.warn('[flower-cluster-warning] comparison failed', error);
      }
    };

    void refreshClusterWarnings();
    const warningInterval = setInterval(() => void refreshClusterWarnings(), 60000);
    return () => clearInterval(warningInterval);
  }, []);

  // Control history polling (30 seconds)
  useEffect(() => {
    const refreshHistory = async () => {
      const historyByRoom: Record<string, ControlHistoryEntry[]> = {};
      await Promise.all(ZONES.map(async (zone) => {
        try {
          const list = await apiClient.getControlHistory(zone.location, zone.cluster, 10);
          historyByRoom[`${zone.location}_${zone.cluster}`] = list ?? [];
        } catch {
          historyByRoom[`${zone.location}_${zone.cluster}`] = [];
        }
      }));
      setControlHistoryByRoom(prev => ({ ...prev, ...historyByRoom }));
    };

    refreshHistory();
    const historyInterval = setInterval(refreshHistory, 30000);
    return () => clearInterval(historyInterval);
  }, []);

  return {
    devices,
    sensorData,
    controlHistoryByRoom,
    lightDisplayNames,
    flowerClusterWarnings,
    loading,
    refresh: loadInitialData
  };
}
