import { useCallback, useEffect, useState } from 'react'

import { DASHBOARD_ROW_ZONES } from '../config/zones'
import type { components } from '../generated/api'
import { apiClient } from '../services/api'
import type { ExpectedHarvest, PendingModeChange } from '../types/calendar'
import type { Schedule } from '../types/schedule'

type ActiveModeResponse = components['schemas']['ActiveModeResponse']

export interface DashboardScheduleContext {
  modes: Record<string, ActiveModeResponse>
  modeErrors: Record<string, string | null>
  schedules: Schedule[]
  pendingModeChange: PendingModeChange | null
  expectedHarvests: Record<string, ExpectedHarvest | null>
  loading: boolean
  error: string | null
  updatedAt: number | null
  refresh: () => Promise<void>
}

const ACTIVE_MODE_ZONES = DASHBOARD_ROW_ZONES.filter(
  ({ location, cluster }) =>
    cluster === 'main' && (location === 'Flower Room' || location === 'Veg Room')
)

function isValidActiveMode(
  value: unknown,
  location: string,
  cluster: string
): value is ActiveModeResponse {
  if (typeof value !== 'object' || value === null) return false
  const mode = value as Partial<ActiveModeResponse>
  return (
    mode.location === location &&
    mode.cluster === cluster &&
    typeof mode.mode_name === 'string' &&
    mode.mode_name.trim().length > 0
  )
}

function errorMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message
  if (typeof reason === 'string' && reason) return reason
  return 'Active grow mode request failed'
}

export function useDashboardScheduleContext(): DashboardScheduleContext {
  const [modes, setModes] = useState<Record<string, ActiveModeResponse>>({})
  const [modeErrors, setModeErrors] = useState<Record<string, string | null>>({})
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [pendingModeChange, setPendingModeChange] = useState<PendingModeChange | null>(null)
  const [expectedHarvests, setExpectedHarvests] = useState<Record<string, ExpectedHarvest | null>>(
    {}
  )
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    const [modeResults, [scheduleResult], [pendingChangeResult], harvestResults] =
      await Promise.all([
        Promise.allSettled(
          ACTIVE_MODE_ZONES.map(({ location, cluster }) =>
            apiClient.getActiveRoomMode(location, cluster)
          )
        ),
        Promise.allSettled([apiClient.getSchedules()]),
        Promise.allSettled([apiClient.getPendingModeChange('Flower Room', 'main')]),
        Promise.allSettled(
          ACTIVE_MODE_ZONES.map(({ location, cluster }) =>
            apiClient.getExpectedHarvest(location, cluster)
          )
        ),
      ])
    const nextModes: Record<string, ActiveModeResponse> = {}
    const nextModeErrors: Record<string, string | null> = {}
    const nextHarvests: Record<string, ExpectedHarvest | null> = {}
    const errors: string[] = []
    let modesFailed = false
    let changed = false

    ACTIVE_MODE_ZONES.forEach(({ location, cluster }, index) => {
      const key = `${location}:${cluster}`
      const result = modeResults[index]
      if (result) {
        if (result.status === 'fulfilled' && isValidActiveMode(result.value, location, cluster)) {
          nextModes[key] = result.value
          nextModeErrors[key] = null
          changed = true
        } else {
          nextModeErrors[key] =
            result.status === 'rejected'
              ? errorMessage(result.reason)
              : `Active grow mode response did not match ${location}/${cluster} or had no mode name`
          modesFailed = true
        }
      }

      const harvest = harvestResults[index]
      if (harvest?.status === 'fulfilled') {
        nextHarvests[key] = harvest.value
        changed = true
      } else {
        errors.push('calendar status unavailable')
      }
    })

    if (modesFailed) errors.push('grow mode unavailable')
    if (scheduleResult.status === 'fulfilled') {
      setSchedules(Array.isArray(scheduleResult.value) ? scheduleResult.value : [])
      changed = true
    } else {
      errors.push('schedules unavailable')
    }
    if (pendingChangeResult.status === 'fulfilled') {
      setPendingModeChange(pendingChangeResult.value)
      changed = true
    } else {
      errors.push('calendar status unavailable')
    }

    setModes(previous => ({ ...previous, ...nextModes }))
    setModeErrors(previous => ({ ...previous, ...nextModeErrors }))
    setExpectedHarvests(previous => ({ ...previous, ...nextHarvests }))
    if (changed) setUpdatedAt(Date.now())
    setError(errors.length > 0 ? [...new Set(errors)].join(' · ') : null)
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), 60_000)
    return () => clearInterval(interval)
  }, [refresh])

  return {
    modes,
    modeErrors,
    schedules,
    pendingModeChange,
    expectedHarvests,
    loading,
    error,
    updatedAt,
    refresh,
  }
}

export function getDashboardMode(
  modes: Record<string, ActiveModeResponse>,
  location: string,
  cluster: string
): ActiveModeResponse | null {
  return modes[`${location}:${cluster}`] ?? null
}
