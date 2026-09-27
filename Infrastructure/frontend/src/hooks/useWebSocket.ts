/** WebSocket hook for real-time device and sensor updates. */
import { useEffect, useRef, useState } from 'react'
import { wsClient, type WebSocketConnectionState } from '../services/websocket'
import type { Device } from '../types/device'
import type { SensorSampleMeta } from '../types/sensor'

export interface UseWebSocketOptions {
  onDeviceUpdate?: (device: Device) => void
  onSensorUpdate?: (key: string, value: number) => void
}
export interface UseWebSocketReturn {
  devices: Device[]
  sensorData: Record<string, number>
  sensorMeta: Record<string, SensorSampleMeta>
  connectionState: WebSocketConnectionState
}

interface DeviceUpdatePayload {
  location: string
  cluster: string
  device: string
  state: number
  mode: string
}

interface SensorUpdatePayload {
  location: string
  cluster: string
  sensor?: string
  sensor_type?: string
  value: number | string | null
  time?: string | number
  timestamp?: string | number
}
function parseMessageObservedAtMs(message: SensorUpdatePayload): number | null {
  const raw = message.time ?? message.timestamp
  if (raw == null) return null
  const observedAtMs = new Date(raw).getTime()
  return Number.isFinite(observedAtMs) ? observedAtMs : null
}

/**
 * Hook for managing WebSocket connection and handling real-time updates.
 * Provides device state updates and sensor data via WebSocket.
 */
export function useWebSocket({
  onDeviceUpdate,
  onSensorUpdate,
}: UseWebSocketOptions = {}): UseWebSocketReturn {
  const [devices, setDevices] = useState<Device[]>([])
  const [sensorData, setSensorData] = useState<Record<string, number>>({})
  const [sensorMeta, setSensorMeta] = useState<Record<string, SensorSampleMeta>>({})
  const [connectionState, setConnectionState] = useState<WebSocketConnectionState>(
    () => wsClient.connectionState
  )
  const onDeviceUpdateRef = useRef(onDeviceUpdate)
  const onSensorUpdateRef = useRef(onSensorUpdate)

  onDeviceUpdateRef.current = onDeviceUpdate
  onSensorUpdateRef.current = onSensorUpdate

  useEffect(() => {
    wsClient.acquire()
    const unsubscribeConnection = wsClient.subscribeConnectionState(setConnectionState)

    const unsubscribeDevice = wsClient.on('device_update', raw => {
      const message = raw as unknown as DeviceUpdatePayload
      setDevices(prev =>
        prev.map(device =>
          device.location === message.location &&
          device.cluster === message.cluster &&
          device.device_name === message.device
            ? { ...device, state: message.state, mode: message.mode }
            : device
        )
      )

      onDeviceUpdateRef.current?.({
        location: message.location,
        cluster: message.cluster,
        device_name: message.device,
        state: message.state,
        mode: message.mode,
        channel: null,
      })
    })

    const unsubscribeSensor = wsClient.on('sensor_update', raw => {
      const message = raw as unknown as SensorUpdatePayload
      const sensorKey = message.sensor ?? message.sensor_type
      if (!sensorKey) return
      const key = `${message.location}_${message.cluster}_${sensorKey}`
      const receivedAtMs = Date.now()
      const numericValue = Number(message.value)
      const invalid = !Number.isFinite(numericValue)

      setSensorMeta(prev => ({
        ...prev,
        [key]: {
          observedAtMs: parseMessageObservedAtMs(message),
          receivedAtMs,
          source: 'websocket',
          invalid,
        },
      }))
      if (invalid) return

      setSensorData(prev => ({ ...prev, [key]: numericValue }))
      onSensorUpdateRef.current?.(key, numericValue)
    })

    return () => {
      unsubscribeConnection()
      unsubscribeDevice()
      unsubscribeSensor()
      wsClient.release()
    }
  }, [])

  return { devices, sensorData, sensorMeta, connectionState }
}
