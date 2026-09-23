import { useCallback, useEffect, useState } from 'react'

import { apiClient } from '../services/api'
import type { Schedule } from '../types/schedule'

export interface DashboardMode {
  location: string
  cluster: string
  mode: string
}

export interface DashboardScheduleContext {
  modes: Record<string, DashboardMode>
  schedules: Schedule[]
  loading: boolean
  error: string | null
  updatedAt: number | null
  refresh: () => Promise<void>
}

function modeKey(location: string, cluster: string): string {
  return `${location}:${cluster}`
}

export function useDashboardScheduleContext(): DashboardScheduleContext {
  const [modes, setModes] = useState<Record<string, DashboardMode>>({})
  const [schedules, setSchedules] = useState<Schedule[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)

  const refresh = useCallback(async () => {
    const [modeResult, scheduleResult] = await Promise.allSettled([
      apiClient.getAllModes(),
      apiClient.getSchedules(),
    ])
    const errors: string[] = []
    let changed = false

    if (modeResult.status === 'fulfilled') {
      const nextModes: Record<string, DashboardMode> = {}
      for (const mode of Object.values(modeResult.value ?? {})) {
        if (mode?.location && mode.cluster) nextModes[modeKey(mode.location, mode.cluster)] = mode
      }
      setModes(nextModes)
      changed = true
    } else {
      errors.push('grow mode unavailable')
    }

    if (scheduleResult.status === 'fulfilled') {
      setSchedules(Array.isArray(scheduleResult.value) ? scheduleResult.value : [])
      changed = true
    } else {
      errors.push('schedules unavailable')
    }

    if (changed) setUpdatedAt(Date.now())
    setError(errors.length > 0 ? errors.join(' · ') : null)
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), 60_000)
    return () => clearInterval(interval)
  }, [refresh])

  return { modes, schedules, loading, error, updatedAt, refresh }
}

export function getDashboardMode(
  modes: Record<string, DashboardMode>,
  location: string,
  cluster: string
): string | null {
  return modes[modeKey(location, cluster)]?.mode ?? null
}
