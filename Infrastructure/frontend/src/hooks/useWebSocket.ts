/** WebSocket hook for real-time device and sensor updates. */
import { useEffect, useState, useRef } from 'react';
import { wsClient } from '../services/websocket';
import type { Device } from '../types/device';
import { parseLiveResponse as parseLiveResponseUtil } from '../utils/sensorLive';

export interface UseWebSocketOptions {
  onDeviceUpdate?: (device: Device) => void;
  onSensorUpdate?: (key: string, value: number) => void;
}

export interface UseWebSocketReturn {
  devices: Device[];
  sensorData: Record<string, number>;
}

interface DeviceUpdatePayload {
  location: string;
  cluster: string;
  device: string;
  state: number;
  mode: string;
}

interface SensorUpdatePayload {
  location: string;
  cluster: string;
  sensor?: string;
  sensor_type?: string;
  value: number;
}

/** Re-export for callers that imported from this hook. */
export const parseLiveResponse = parseLiveResponseUtil;

/**
 * Hook for managing WebSocket connection and handling real-time updates.
 * Provides device state updates and sensor data via WebSocket.
 */
export function useWebSocket({ onDeviceUpdate, onSensorUpdate }: UseWebSocketOptions = {}): UseWebSocketReturn {
  const [devices, setDevices] = useState<Device[]>([]);
  const [sensorData, setSensorData] = useState<Record<string, number>>({});
  const onDeviceUpdateRef = useRef(onDeviceUpdate);
  const onSensorUpdateRef = useRef(onSensorUpdate);

  // Keep refs updated
  onDeviceUpdateRef.current = onDeviceUpdate;
  onSensorUpdateRef.current = onSensorUpdate;

  useEffect(() => {
    wsClient.acquire();

    const unsubscribeDevice = wsClient.on('device_update', (raw) => {
      const message = raw as unknown as DeviceUpdatePayload;
      setDevices(prev => prev.map(device =>
        device.location === message.location &&
        device.cluster === message.cluster &&
        device.device_name === message.device
        ? { ...device, state: message.state, mode: message.mode }
        : device
      ));

      if (onDeviceUpdateRef.current) {
        onDeviceUpdateRef.current({
          location: message.location,
          cluster: message.cluster,
          device_name: message.device,
          state: message.state,
          mode: message.mode,
          channel: null
        });
      }
    });

    const unsubscribeSensor = wsClient.on('sensor_update', (raw) => {
      const message = raw as unknown as SensorUpdatePayload;
      const sensorKey = message.sensor ?? message.sensor_type;
      if (!sensorKey) return;
      const key = `${message.location}_${message.cluster}_${sensorKey}`;
      setSensorData(prev => ({
        ...prev,
        [key]: message.value
      }));

      if (onSensorUpdateRef.current) {
        onSensorUpdateRef.current(key, message.value);
      }
    });

    return () => {
      unsubscribeDevice();
      unsubscribeSensor();
      wsClient.release();
    };
  }, []);

  return { devices, sensorData };
}
