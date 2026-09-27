import { useEffect, useMemo, useState } from 'react'

import { getSensorPollZones } from '../config/zones'
import {
  SENSOR_STALE_AFTER_MS,
  type SensorSampleMeta,
  type ZoneSensorStatus,
} from '../types/sensor'
import type { Device } from '../types/device'
import { useSensorPolling, deriveZoneSensorStatus } from './useSensorPolling'
import { useWebSocket } from './useWebSocket'

export interface DashboardLiveData {
  devices: Device[]
  sensorData: Record<string, number>
  sensorMeta: Record<string, SensorSampleMeta>
  zoneStatus: Record<string, ZoneSensorStatus>
  lightDisplayNames: Record<string, string>
  flowerClusterWarnings: string[]
  loading: boolean
  lastPollAt: number | null
  transport: 'websocket' | 'polling' | 'degraded'
}

function sampleAgeMs(meta: SensorSampleMeta, nowMs: number): number {
  return Math.max(0, nowMs - (meta.observedAtMs ?? meta.receivedAtMs))
}

function deviceKey(device: Device): string {
  return `${device.location}:${device.cluster}:${device.device_name}`
}

export function useDashboardLiveData(): DashboardLiveData {
  const polling = useSensorPolling({ interval: 1000 })
  const websocket = useWebSocket()
  const [nowMs, setNowMs] = useState(() => Date.now())
  const pollingDevices = polling.devices ?? []
  const pollingSensorData = polling.sensorData ?? {}
  const pollingSensorMeta = polling.sensorMeta ?? {}
  const pollingZoneStatus = polling.zoneStatus ?? {}
  const websocketDevices = websocket.devices ?? []
  const websocketSensorData = websocket.sensorData ?? {}
  const websocketSensorMeta = websocket.sensorMeta ?? {}
  const websocketConnectionState = websocket.connectionState ?? 'closed'

  useEffect(() => {
    const clock = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(clock)
  }, [])

  const devices = useMemo(() => {
    const byKey = new Map<string, Device>(pollingDevices.map(device => [deviceKey(device), device]))
    for (const device of websocketDevices) byKey.set(deviceKey(device), device)
    return [...byKey.values()]
  }, [pollingDevices, websocketDevices])

  const merged = useMemo(() => {
    const keys = new Set([...Object.keys(pollingSensorData), ...Object.keys(websocketSensorData)])
    const sensorData: Record<string, number> = {}
    const sensorMeta: Record<string, SensorSampleMeta> = {}
    let selectedWebSocketSample = false

    for (const key of keys) {
      const wsMeta = websocketSensorMeta[key]
      const wsValue = websocketSensorData[key]
      const wsFresh =
        websocketConnectionState === 'open' &&
        wsMeta != null &&
        !wsMeta.invalid &&
        wsValue != null &&
        sampleAgeMs(wsMeta, nowMs) <= SENSOR_STALE_AFTER_MS
      const pollValue = pollingSensorData[key]
      const pollMeta = pollingSensorMeta[key]

      if (wsFresh) {
        sensorData[key] = wsValue
        sensorMeta[key] = wsMeta
        selectedWebSocketSample = true
      } else if (pollValue != null) {
        sensorData[key] = pollValue
        if (pollMeta) sensorMeta[key] = pollMeta
      } else if (wsValue != null && wsMeta) {
        sensorData[key] = wsValue
        sensorMeta[key] = wsMeta
      } else if (wsMeta) {
        sensorMeta[key] = wsMeta
      }
    }

    return { sensorData, sensorMeta, selectedWebSocketSample }
  }, [
    nowMs,
    pollingSensorData,
    pollingSensorMeta,
    websocketConnectionState,
    websocketSensorData,
    websocketSensorMeta,
  ])

  const zoneStatus = useMemo(() => {
    const status: Record<string, ZoneSensorStatus> = {}
    for (const zone of getSensorPollZones()) {
      const key = `${zone.location}:${zone.cluster}`
      const pollStatus = pollingZoneStatus[key]
      status[key] = deriveZoneSensorStatus(
        zone.location,
        zone.cluster,
        merged.sensorMeta,
        pollStatus?.error ?? null,
        nowMs
      )
    }
    return status
  }, [merged.sensorMeta, nowMs, pollingZoneStatus])

  const transport = useMemo<DashboardLiveData['transport']>(() => {
    if (merged.selectedWebSocketSample) return 'websocket'
    if (polling.lastPollAt != null && Object.keys(merged.sensorMeta).length > 0) return 'polling'
    return 'degraded'
  }, [merged.sensorMeta, merged.selectedWebSocketSample, polling.lastPollAt])

  return {
    devices,
    sensorData: merged.sensorData,
    sensorMeta: merged.sensorMeta,
    zoneStatus,
    lightDisplayNames: polling.lightDisplayNames ?? {},
    flowerClusterWarnings: polling.flowerClusterWarnings ?? [],
    loading: polling.loading ?? false,
    lastPollAt: polling.lastPollAt ?? null,
    transport,
  }
}
