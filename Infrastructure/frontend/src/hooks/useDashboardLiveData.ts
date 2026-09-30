import { useEffect, useMemo, useRef, useState } from 'react'

import { getSensorPollZones } from '../config/zones'
import type { Device } from '../types/device'
import {
  SENSOR_STALE_AFTER_MS,
  type SensorSampleMeta,
  type ZoneSensorStatus,
} from '../types/sensor'

import { useSensorPolling } from './useSensorPolling'
import { projectZoneSensorStatus, summarizeZoneSensors } from './zoneSensorSummary'
import { useWebSocket } from './useWebSocket'

const EMPTY_DEVICES: Device[] = []
const EMPTY_SENSOR_DATA: Record<string, number> = {}
const EMPTY_SENSOR_META: Record<string, SensorSampleMeta> = {}
const EMPTY_ZONE_STATUS: Record<string, ZoneSensorStatus> = {}

interface MergedSensorSelection {
  sensorData: Record<string, number>
  sensorMeta: Record<string, SensorSampleMeta>
  selectedWebSocketSample: boolean
}

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

/**
 * Structural equality of two merged selections: same key order and same
 * numeric values (`Object.is`), same per-key metadata identity, and the
 * same WebSocket-selection flag. Every other field is a derived value.
 */
function sameMergedSelection(a: MergedSensorSelection, b: MergedSensorSelection): boolean {
  if (a === b) return true
  if (a.selectedWebSocketSample !== b.selectedWebSocketSample) return false

  const aDataKeys = Object.keys(a.sensorData)
  const bDataKeys = Object.keys(b.sensorData)
  if (aDataKeys.length !== bDataKeys.length) return false
  for (let i = 0; i < aDataKeys.length; i++) {
    const key = aDataKeys[i]
    if (key !== bDataKeys[i]) return false
    if (!Object.is(a.sensorData[key], b.sensorData[key])) return false
  }

  const aMetaKeys = Object.keys(a.sensorMeta)
  const bMetaKeys = Object.keys(b.sensorMeta)
  if (aMetaKeys.length !== bMetaKeys.length) return false
  for (let i = 0; i < aMetaKeys.length; i++) {
    const key = aMetaKeys[i]
    if (key !== bMetaKeys[i]) return false
    if (a.sensorMeta[key] !== b.sensorMeta[key]) return false
  }
  return true
}

export function useDashboardLiveData(): DashboardLiveData {
  const polling = useSensorPolling({ interval: 1000 })
  const websocket = useWebSocket()
  const [nowMs, setNowMs] = useState(() => Date.now())
  const pollingDevices = polling.devices ?? EMPTY_DEVICES
  const pollingSensorData = polling.sensorData ?? EMPTY_SENSOR_DATA
  const pollingSensorMeta = polling.sensorMeta ?? EMPTY_SENSOR_META
  const pollingZoneStatus = polling.zoneStatus ?? EMPTY_ZONE_STATUS
  const websocketDevices = websocket.devices ?? EMPTY_DEVICES
  const websocketSensorData = websocket.sensorData ?? EMPTY_SENSOR_DATA
  const websocketSensorMeta = websocket.sensorMeta ?? EMPTY_SENSOR_META

  useEffect(() => {
    const clock = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(clock)
  }, [])

  const devices = useMemo(() => {
    const byKey = new Map<string, Device>(pollingDevices.map(device => [deviceKey(device), device]))
    for (const device of websocketDevices) byKey.set(deviceKey(device), device)
    return [...byKey.values()]
  }, [pollingDevices, websocketDevices])

  const previousMergedRef = useRef<MergedSensorSelection | null>(null)
  const merged = useMemo<MergedSensorSelection>(() => {
    const keys = new Set([...Object.keys(pollingSensorData), ...Object.keys(websocketSensorData)])
    const candidate: MergedSensorSelection = {
      sensorData: {},
      sensorMeta: {},
      selectedWebSocketSample: false,
    }

    for (const key of keys) {
      const wsMeta = websocketSensorMeta[key]
      const wsValue = websocketSensorData[key]
      const wsFresh =
        websocket.connectionState === 'open' &&
        wsMeta != null &&
        !wsMeta.invalid &&
        wsValue != null &&
        sampleAgeMs(wsMeta, nowMs) <= SENSOR_STALE_AFTER_MS
      const pollValue = pollingSensorData[key]
      const pollMeta = pollingSensorMeta[key]

      if (wsFresh) {
        candidate.sensorData[key] = wsValue
        candidate.sensorMeta[key] = wsMeta
        candidate.selectedWebSocketSample = true
      } else if (pollValue != null) {
        candidate.sensorData[key] = pollValue
        if (pollMeta) candidate.sensorMeta[key] = pollMeta
      } else if (wsValue != null && wsMeta) {
        candidate.sensorData[key] = wsValue
        candidate.sensorMeta[key] = wsMeta
      } else if (wsMeta) {
        candidate.sensorMeta[key] = wsMeta
      }
    }

    // Clock-only ticks must not invalidate downstream consumers when nothing
    // about the selection actually changed: reuse the previous object when
    // key order, numeric values, metadata identities, and the WebSocket
    // selection flag are unchanged. New receipt/observed metadata, numeric
    // changes, and expiry/source-selection flips publish immediately.
    const previous = previousMergedRef.current
    if (previous !== null && sameMergedSelection(previous, candidate)) return previous
    previousMergedRef.current = candidate
    return candidate
  }, [
    nowMs,
    pollingSensorData,
    pollingSensorMeta,
    websocket.connectionState,
    websocketSensorData,
    websocketSensorMeta,
  ])

  const mergedZoneSummaries = useMemo(
    () =>
      Object.fromEntries(
        getSensorPollZones().map(zone => {
          const key = `${zone.location}:${zone.cluster}`
          return [
            key,
            summarizeZoneSensors(`${zone.location}_${zone.cluster}_`, merged.sensorMeta),
          ]
        })
      ),
    [merged.sensorMeta]
  )

  const zoneStatus = useMemo(() => {
    const status: Record<string, ZoneSensorStatus> = {}
    for (const zone of getSensorPollZones()) {
      const key = `${zone.location}:${zone.cluster}`
      status[key] = projectZoneSensorStatus(
        mergedZoneSummaries[key],
        pollingZoneStatus[key]?.error ?? null,
        nowMs
      )
    }
    return status
  }, [mergedZoneSummaries, nowMs, pollingZoneStatus])

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
