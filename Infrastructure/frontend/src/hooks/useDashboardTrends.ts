import { useCallback, useEffect, useState } from 'react'

import { MonitoringApi } from '../features/monitoring/api/monitoringApi'
import type { SensorSeries } from '../features/monitoring/api/contracts'
import {
  buildTrendMetric,
  normalizeSensorSeries,
  type ClimateMetric,
  type TrendData,
} from '../components/dashboard/dashboardStatus'

const TREND_LOCATIONS = ['Flower Room', 'Veg Room', 'Lab'] as const
const TREND_WINDOW_MS = 60 * 60_000
const TREND_MAX_POINTS = 60

export interface DashboardTrends {
  byRoom: Record<string, TrendData>
  updatedAt: Record<string, number | null>
  errors: Record<string, string | null>
  loading: boolean
  refresh: () => Promise<void>
}

const monitoringApi = new MonitoringApi()

function trendCluster(location: string, series: SensorSeries): string {
  if (location !== 'Flower Room') return 'main'
  const identity = `${series.node}_${series.sensor}`.toLowerCase()
  return identity.includes('back') || identity.endsWith('_b') || identity.includes('_b_')
    ? 'back'
    : 'front'
}

function normalizeRoom(location: string, series: SensorSeries[]): TrendData {
  const byCluster: TrendData = {}
  for (const item of series) {
    const normalized = normalizeSensorSeries(item)
    if (!normalized) continue
    const cluster = trendCluster(location, item)
    const metric = normalized.metric as ClimateMetric
    const current = byCluster[cluster] ?? {}
    current[metric] = buildTrendMetric(metric, normalized.points)
    byCluster[cluster] = current
  }
  return byCluster
}

export function useDashboardTrends(): DashboardTrends {
  const [byRoom, setByRoom] = useState<Record<string, TrendData>>({})
  const [updatedAt, setUpdatedAt] = useState<Record<string, number | null>>({})
  const [errors, setErrors] = useState<Record<string, string | null>>({})
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    const end = new Date()
    const start = new Date(end.getTime() - TREND_WINDOW_MS)
    const results = await Promise.allSettled(
      TREND_LOCATIONS.map(location =>
        monitoringApi.sensorRange(
          location,
          start.toISOString(),
          end.toISOString(),
          TREND_MAX_POINTS
        )
      )
    )
    const nextErrors: Record<string, string | null> = {}
    const nextUpdated: Record<string, number | null> = {}

    results.forEach((result, index) => {
      const location = TREND_LOCATIONS[index]
      if (result.status === 'fulfilled') {
        setByRoom(previous => ({
          ...previous,
          [location]: normalizeRoom(location, result.value.series),
        }))
        nextErrors[location] = null
        nextUpdated[location] = Date.now()
      } else {
        nextErrors[location] =
          result.reason instanceof Error ? result.reason.message : 'Trend request failed'
      }
    })

    setErrors(previous => ({ ...previous, ...nextErrors }))
    setUpdatedAt(previous => ({ ...previous, ...nextUpdated }))
    setLoading(false)
  }, [])

  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), 60_000)
    return () => clearInterval(interval)
  }, [refresh])

  return { byRoom, updatedAt, errors, loading, refresh }
}
