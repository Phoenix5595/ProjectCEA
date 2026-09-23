/** Sensor polling hook for dashboard data. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiClient } from '../services/api'
import type { Device } from '../types/device'
import {
  SENSOR_STALE_AFTER_MS,
  type SensorSampleMeta,
  type ZoneSensorStatus,
} from '../types/sensor'
import {
  ZONES,
  FLOWER_DASHBOARD_CLUSTERS,
  getDashboardPollZones,
  getSensorPollZones,
  buildDashboardBulkSensorKeys,
} from '../config/zones'
import { parseLiveSnapshot } from '../utils/sensorLive'
import { logger } from '../utils/logger'
export interface UseSensorPollingOptions {
  interval?: number
}

export interface UseSensorPollingReturn {
  devices: Device[]
  sensorData: Record<string, number>
  sensorMeta: Record<string, SensorSampleMeta>
  zoneStatus: Record<string, ZoneSensorStatus>
  lastPollAt: number | null
  /** `${location}_${cluster}_${device_name}` → config `display_name` for lights UI */
  lightDisplayNames: Record<string, string>
  /** Temporary hybrid warning layer: DB/ingestion-observed clusters vs configured clusters. */
  flowerClusterWarnings: string[]
  loading: boolean
  refresh: () => Promise<void>
}

async function loadLightDisplayNamesMap(): Promise<Record<string, string>> {
  // Phase 5e: this used to iterate `getDashboardPollZones()`, which
  // fans Flower out into `front`/`back` (sensor sub-clusters). The
  // /api/devices endpoint correctly rejects those — pre-Phase-5e with
  // a 404 ("Unknown location/cluster"), now with a 400 + hint — so
  // every dashboard tick wasted two requests and dirtied the browser
  // console. ZONES is the device-plane registry (one entry per room,
  // cluster = "main"), which is exactly what /api/devices wants.
  const pairs = await Promise.all(
    ZONES.map(async zone => {
      try {
        const res = await apiClient.getDevicesForLocationCluster(zone.location, zone.cluster)
        const devs = res?.devices as Record<string, { display_name?: string }> | undefined
        if (!devs) return [] as [string, string][]
        const out: [string, string][] = []
        for (const [deviceName, info] of Object.entries(devs)) {
          const dn = info?.display_name?.trim()
          if (dn) {
            out.push([`${zone.location}_${zone.cluster}_${deviceName}`, dn])
          }
        }
        return out
      } catch {
        return [] as [string, string][]
      }
    })
  )
  return Object.fromEntries(pairs.flat())
}
export function deriveZoneSensorStatus(
  location: string,
  cluster: string,
  sensorMeta: Record<string, SensorSampleMeta>,
  error: string | null,
  nowMs: number
): ZoneSensorStatus {
  const prefix = `${location}_${cluster}_`
  const entries = Object.entries(sensorMeta)
    .filter(([key]) => key.startsWith(prefix))
    .map(([, value]) => value)
  const invalidEntries = entries.filter(entry => entry.invalid)
  const validEntries = entries.filter(entry => !entry.invalid)

  if (validEntries.length === 0) {
    return {
      quality: invalidEntries.length > 0 ? 'bad' : 'missing',
      newestObservedAtMs: null,
      ageMs: null,
      source: entries[0]?.source ?? null,
      error,
    }
  }

  const newestEntry = validEntries.reduce((newest, entry) => {
    const newestTime = newest.observedAtMs ?? newest.receivedAtMs
    const entryTime = entry.observedAtMs ?? entry.receivedAtMs
    return entryTime > newestTime ? entry : newest
  })
  const newestObservedAtMs = validEntries.reduce<number | null>(
    (newest, entry) =>
      entry.observedAtMs != null && (newest == null || entry.observedAtMs > newest)
        ? entry.observedAtMs
        : newest,
    null
  )
  const ageMs = Math.max(0, nowMs - (newestEntry.observedAtMs ?? newestEntry.receivedAtMs))

  return {
    quality: ageMs > SENSOR_STALE_AFTER_MS ? 'stale' : 'live',
    newestObservedAtMs,
    ageMs,
    source: newestEntry.source,
    error,
  }
}

/**
 * Hook for polling sensor data and device state for the dashboard.
 * Live samples retain their age and failed zones retain their last good value.
 */
export function useSensorPolling({
  interval = 5000,
}: UseSensorPollingOptions = {}): UseSensorPollingReturn {
  const [devices, setDevices] = useState<Device[]>([])
  const [sensorData, setSensorData] = useState<Record<string, number>>({})
  const [sensorMeta, setSensorMeta] = useState<Record<string, SensorSampleMeta>>({})
  const [zoneErrors, setZoneErrors] = useState<Record<string, string | null>>({})
  const [lastPollAt, setLastPollAt] = useState<number | null>(null)
  const [statusNowMs, setStatusNowMs] = useState(() => Date.now())
  const [lightDisplayNames, setLightDisplayNames] = useState<Record<string, string>>({})
  const [flowerClusterWarnings, setFlowerClusterWarnings] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const liveKeysRef = useRef(new Set<string>())
  const refreshInFlightRef = useRef(false)

  const loadInitialData = useCallback(async () => {
    const pollZones = getDashboardPollZones()
    const bulkKeys = buildDashboardBulkSensorKeys(pollZones)
    try {
      const [devicesData, setpointData, nameMap] = await Promise.all([
        apiClient.getAllDevices().catch(() => []),
        apiClient.getSensorDataBulk(bulkKeys).catch(() => ({})),
        loadLightDisplayNamesMap(),
      ])

      if (devicesData) setDevices(devicesData)
      setLightDisplayNames(nameMap)
      if (setpointData && Object.keys(setpointData).length > 0) {
        setSensorData(prev => ({ ...prev, ...setpointData }))
      }
    } catch (error) {
      logger.error('Error loading initial sensor data:', error)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadInitialData()

    const refreshLiveSensors = async () => {
      if (refreshInFlightRef.current) return
      refreshInFlightRef.current = true
      try {
        const pollZones = getSensorPollZones()
        const results = await Promise.allSettled(
          pollZones.map(zone => apiClient.getLiveSensorData(zone.location, zone.cluster))
        )
        const successful: Array<{
          location: string
          cluster: string
          values: Record<string, number>
          meta: Record<string, SensorSampleMeta>
        }> = []
        const errors: Record<string, string | null> = {}
        const successfulZoneKeys = new Set<string>()

        results.forEach((result, index) => {
          const zone = pollZones[index]
          const zoneKey = `${zone.location}:${zone.cluster}`
          if (result.status === 'fulfilled') {
            const parsed = parseLiveSnapshot(zone.location, zone.cluster, result.value, Date.now())
            successful.push({ ...zone, values: parsed.values, meta: parsed.meta })
            successfulZoneKeys.add(zoneKey)
            errors[zoneKey] = null
          } else {
            errors[zoneKey] =
              result.reason instanceof Error ? result.reason.message : 'Sensor request failed'
            logger.warn(`[sensor-poll] ${zoneKey} failed`, result.reason)
          }
        })

        const prefixes = new Set(successful.map(zone => `${zone.location}_${zone.cluster}_`))
        const removedLiveKeys = [...liveKeysRef.current].filter(key =>
          [...prefixes].some(prefix => key.startsWith(prefix))
        )
        for (const key of removedLiveKeys) liveKeysRef.current.delete(key)

        const nextValues: Record<string, number> = {}
        const nextMeta: Record<string, SensorSampleMeta> = {}
        for (const zone of successful) {
          Object.assign(nextValues, zone.values)
          Object.assign(nextMeta, zone.meta)
          Object.keys(zone.meta).forEach(key => liveKeysRef.current.add(key))
        }

        setSensorData(prev => {
          const next = { ...prev }
          removedLiveKeys.forEach(key => delete next[key])
          return { ...next, ...nextValues }
        })
        setSensorMeta(prev => {
          const next = { ...prev }
          removedLiveKeys.forEach(key => delete next[key])
          return { ...next, ...nextMeta }
        })
        setZoneErrors(prev => {
          const next = { ...prev }
          for (const zone of pollZones) {
            const key = `${zone.location}:${zone.cluster}`
            if (successfulZoneKeys.has(key)) next[key] = errors[key] ?? null
            else if (errors[key]) next[key] = errors[key]
          }
          return next
        })
        setLastPollAt(Date.now())
      } catch (error) {
        logger.warn('Live sensor refresh failed', error)
      } finally {
        refreshInFlightRef.current = false
      }
    }

    void refreshLiveSensors()
    const sensorInterval = setInterval(() => void refreshLiveSensors(), interval)
    return () => clearInterval(sensorInterval)
  }, [interval, loadInitialData])

  useEffect(() => {
    const clock = setInterval(() => setStatusNowMs(Date.now()), 1000)
    return () => clearInterval(clock)
  }, [])

  useEffect(() => {
    const refreshNames = async () => {
      const map = await loadLightDisplayNamesMap()
      setLightDisplayNames(map)
    }
    const nameInterval = setInterval(() => void refreshNames(), 120000)
    return () => clearInterval(nameInterval)
  }, [])

  useEffect(() => {
    const refreshClusterWarnings = async () => {
      try {
        const allLive = await apiClient.getAllLiveSensorData()
        const discovered = new Set<string>()
        for (const row of allLive) {
          const name = row?.sensor ?? ''
          if (name.endsWith('_f')) discovered.add('front')
          if (name.endsWith('_b')) discovered.add('back')
        }
        const configured = new Set(FLOWER_DASHBOARD_CLUSTERS)
        const warnings: string[] = []

        for (const c of configured) {
          if (!discovered.has(c)) {
            warnings.push(`Configured Flower cluster '${c}' has no live sensor stream.`)
          }
        }
        for (const c of discovered) {
          if (!configured.has(c)) {
            warnings.push(`Live Flower cluster '${c}' is discovered but not configured.`)
          }
        }

        setFlowerClusterWarnings(warnings)
        for (const message of warnings) {
          logger.warn(`[flower-cluster-warning] ${message}`)
        }
      } catch (error) {
        setFlowerClusterWarnings(['Failed to compare configured vs discovered Flower clusters.'])
        logger.warn('[flower-cluster-warning] comparison failed', error)
      }
    }

    void refreshClusterWarnings()
    const warningInterval = setInterval(() => void refreshClusterWarnings(), 60000)
    return () => clearInterval(warningInterval)
  }, [])

  const zoneStatus = useMemo(
    () =>
      Object.fromEntries(
        getSensorPollZones().map(zone => {
          const key = `${zone.location}:${zone.cluster}`
          return [
            key,
            deriveZoneSensorStatus(
              zone.location,
              zone.cluster,
              sensorMeta,
              zoneErrors[key] ?? null,
              statusNowMs
            ),
          ]
        })
      ),
    [sensorMeta, statusNowMs, zoneErrors]
  )

  return {
    devices,
    sensorData,
    sensorMeta,
    zoneStatus,
    lastPollAt,
    lightDisplayNames,
    flowerClusterWarnings,
    loading,
    refresh: loadInitialData,
  }
}
