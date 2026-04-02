/** Sensor polling hook for dashboard data. */
import { useEffect, useState, useCallback } from 'react';
import { apiClient } from '../services/api';
import type { Device } from '../types/device';
import type { ControlHistoryEntry } from '../types/device';
import { ZONES } from '../config/zones';
import { parseLiveResponse } from '../utils/sensorLive';

export interface UseSensorPollingOptions {
  interval?: number;
}

export interface UseSensorPollingReturn {
  devices: Device[];
  sensorData: Record<string, number>;
  controlHistoryByRoom: Record<string, ControlHistoryEntry[]>;
  loading: boolean;
  refresh: () => Promise<void>;
}

/**
 * Hook for polling sensor data, devices, and control history.
 * Refreshes live sensors every 5 seconds and control history every 30 seconds.
 */
export function useSensorPolling({ interval = 5000 }: UseSensorPollingOptions = {}): UseSensorPollingReturn {
  const [devices, setDevices] = useState<Device[]>([]);
  const [sensorData, setSensorData] = useState<Record<string, number>>({});
  const [controlHistoryByRoom, setControlHistoryByRoom] = useState<Record<string, ControlHistoryEntry[]>>({});
  const [loading, setLoading] = useState(true);

  const loadInitialData = useCallback(async () => {
    try {
      const [devicesData, setpointData, ...historyResults] = await Promise.all([
        apiClient.getAllDevices().catch(() => []),
        apiClient.getSensorDataBulk([
          'Flower Room_main_heating_setpoint', 'Flower Room_main_cooling_setpoint',
          'Flower Room_main_co2_setpoint', 'Flower Room_main_vpd_setpoint',
          'Veg Room_main_heating_setpoint', 'Veg Room_main_cooling_setpoint',
          'Veg Room_main_co2_setpoint', 'Veg Room_main_vpd_setpoint',
          'Veg Room_main_light_1_intensity', 'Veg Room_main_light_2_intensity',
          'Veg Room_main_light_3_intensity', 'Flower Room_main_light_1_intensity',
          'Flower Room_main_light_2_intensity', 'Flower Room_main_light_3_intensity',
          'Lab_main_lab_temp', 'Lab_main_water_temperature'
        ]).catch(() => ({})),
        ...ZONES.map(zone => apiClient.getControlHistory(zone.location, zone.cluster, 10).catch(() => []))
      ]);

      if (devicesData) setDevices(devicesData);
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
        const liveDataResults = await Promise.all(
          ZONES.map(zone => apiClient.getLiveSensorData(zone.location, zone.cluster).catch(() => ({})))
        );
        
        const allLiveFlat: Record<string, number> = {};
        ZONES.forEach((zone, index) => {
          const parsed = parseLiveResponse(zone.location, zone.cluster, liveDataResults[index] as any);
          Object.assign(allLiveFlat, parsed);
        });
        
        if (Object.keys(allLiveFlat).length > 0) {
          setSensorData(prev => ({ ...prev, ...allLiveFlat }));
        }
      } catch (err) {
        console.warn('Live sensor refresh failed', err);
      }
    };

    refreshLiveSensors();
    const sensorInterval = setInterval(refreshLiveSensors, interval);
    return () => clearInterval(sensorInterval);
  }, [interval, loadInitialData]);

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
    loading,
    refresh: loadInitialData
  };
}
