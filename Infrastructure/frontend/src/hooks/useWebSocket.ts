/** WebSocket hook for real-time device and sensor updates. */
import { useEffect, useState, useRef } from 'react';
import { wsClient } from '../services/websocket';
import type { Device } from '../types/device';

export interface UseWebSocketOptions {
  onDeviceUpdate?: (device: Device) => void;
  onSensorUpdate?: (key: string, value: number) => void;
}

export interface UseWebSocketReturn {
  devices: Device[];
  sensorData: Record<string, number>;
}

/** Parse live API response into flat keys ${location}_${cluster}_${sensorType} -> number. */
export function parseLiveResponse(
  location: string,
  cluster: string,
  liveData: Record<string, { data?: Array<{ value?: number }> }>
): Record<string, number> {
  const flat: Record<string, number> = {};
  if (!liveData || typeof liveData !== 'object') return flat;
  for (const [sensorType, resp] of Object.entries(liveData)) {
    const dp = Array.isArray(resp?.data) && resp.data.length > 0 ? resp.data[0] : null;
    if (dp?.value != null) flat[`${location}_${cluster}_${sensorType}`] = Number(dp.value);
  }
  return flat;
}

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
    // Connect WebSocket
    wsClient.connect();

    // Subscribe to device updates
    const unsubscribeDevice = wsClient.on('device_update', (message) => {
      setDevices(prev => prev.map(device => 
        device.location === message.location && 
        device.cluster === message.cluster && 
        device.device_name === message.device
        ? { ...device, state: message.state, mode: message.mode }
        : device
      ));
      
      // Also call the optional callback for parent component
      if (onDeviceUpdateRef.current) {
        onDeviceUpdateRef.current({
          location: message.location,
          cluster: message.cluster,
          device_name: message.device,
          state: message.state,
          mode: message.mode,
          channel: 0
        });
      }
    });

    // Subscribe to sensor updates
    const unsubscribeSensor = wsClient.on('sensor_update', (message) => {
      const sensorKey = (message as { sensor?: string; sensor_type?: string }).sensor ?? (message as { sensor_type?: string }).sensor_type;
      if (!sensorKey) return;
      const key = `${message.location}_${message.cluster}_${sensorKey}`;
      setSensorData(prev => ({
        ...prev,
        [key]: message.value
      }));
      
      // Also call the optional callback
      if (onSensorUpdateRef.current) {
        onSensorUpdateRef.current(key, message.value);
      }
    });

    return () => {
      unsubscribeDevice();
      unsubscribeSensor();
      wsClient.disconnect();
    };
  }, []);

  return { devices, sensorData };
}
