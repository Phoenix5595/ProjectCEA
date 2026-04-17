import { useState, useEffect, useCallback } from 'react'
import { toast } from 'sonner'
import { apiClient } from '../services/api'
import { logger } from '../utils/logger'

export interface LightDevice {
  device_name: string
  display_name?: string
  dimming_enabled?: boolean
  dimming_board_id?: string | null
  dimming_channel?: number | null
}

export interface LightStatus {
  intensity: number
  target_intensity?: number | null
  day_target_intensity?: number | null
}

export interface UseLightControlReturn {
  lights: LightDevice[]
  statuses: Record<string, LightStatus>
  pendingTargets: Record<string, number>
  loading: boolean
  hasPendingChanges: boolean
  updateTarget: (deviceName: string, value: number) => void
  saveAll: () => Promise<void>
  refresh: () => Promise<void>
}

export function useLightControl(location: string, cluster: string): UseLightControlReturn {
  const [lights, setLights] = useState<LightDevice[]>([])
  const [statuses, setStatuses] = useState<Record<string, LightStatus>>({})
  const [pendingTargets, setPendingTargets] = useState<Record<string, number>>({})
  const [loading, setLoading] = useState(true)
  const [hasPendingChanges, setHasPendingChanges] = useState(false)

  const fetchLightsAndStatus = useCallback(async () => {
    if (!location || !cluster) return

    try {
      const allLights = await apiClient.getLightsForZone(location, cluster)
      const lightDevices = allLights.filter(
        (l: LightDevice) =>
          l.dimming_enabled && l.dimming_board_id !== null && l.dimming_channel !== null
      )
      setLights(lightDevices)

      const schedules = await apiClient.getSchedules(location, cluster)

      const statusPromises = lightDevices.map(async (light: LightDevice) => {
        try {
          const status = await apiClient.getLightStatus(location, cluster, light.device_name)

          const sunSchedule = schedules.find(
            (s: any) =>
              s.device_name === light.device_name &&
              (s.mode === 'SUN' || s.mode === 'DAY') &&
              s.enabled &&
              s.target_intensity !== null &&
              s.target_intensity !== undefined
          )
          const dayTargetIntensity = sunSchedule?.target_intensity ?? null

          return { deviceName: light.device_name, status, dayTargetIntensity }
        } catch (err) {
          logger.error(`Error getting light status for ${light.device_name}:`, err)
          return { deviceName: light.device_name, status: null, dayTargetIntensity: null }
        }
      })

      const results = await Promise.all(statusPromises)
      const statusMap: Record<string, LightStatus> = {}
      results.forEach(({ deviceName, status, dayTargetIntensity }) => {
        if (status) {
          statusMap[deviceName] = {
            intensity: status.intensity,
            target_intensity: status.target_intensity ?? null,
            day_target_intensity: dayTargetIntensity ?? status.target_intensity ?? null,
          }
        }
      })
      setStatuses(statusMap)
    } catch (err) {
      logger.error('Failed to load lights:', err)
    } finally {
      setLoading(false)
    }
  }, [location, cluster])

  useEffect(() => {
    fetchLightsAndStatus()
    const interval = setInterval(fetchLightsAndStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchLightsAndStatus])

  useEffect(() => {
    setHasPendingChanges(Object.keys(pendingTargets).length > 0)
  }, [pendingTargets])

  function updateTarget(deviceName: string, value: number) {
    // Validate and clamp the value
    const clampedValue = Math.max(0, Math.min(100, value))
    setPendingTargets((prev) => ({
      ...prev,
      [deviceName]: clampedValue,
    }))
    setHasPendingChanges(true)
  }

  async function saveAll() {
    const entries = Object.entries(pendingTargets)
    const nextPending = { ...pendingTargets }
    const failed: string[] = []
    for (const [deviceName, target] of entries) {
      try {
        const res = await apiClient.setLightIntensity(location, cluster, deviceName, target)
        if (res.rows_updated !== undefined && res.rows_updated < 1) {
          failed.push(deviceName)
          continue
        }
        delete nextPending[deviceName]
      } catch (err) {
        logger.error(`Failed to set light intensity for ${deviceName}:`, err)
        failed.push(deviceName)
      }
    }
    setPendingTargets(nextPending)
    if (failed.length > 0) {
      toast.error(
        `Light target update failed for: ${failed.join(', ')}. Pending changes kept for those fixtures.`
      )
    } else if (entries.length > 0) {
      toast.success('Light targets applied')
    }
    await fetchLightsAndStatus()
  }

  return {
    lights,
    statuses,
    pendingTargets,
    loading,
    hasPendingChanges,
    updateTarget,
    saveAll,
    refresh: fetchLightsAndStatus,
  }
}