import { useCallback, useEffect, useRef, useState } from 'react'

import { apiClient } from '../services/api'
import type { ActiveAlarmResponse } from '../services/api/alarms'

export interface ActiveAlarmsState {
  alarms: ActiveAlarmResponse[]
  loading: boolean
  error: string | null
  updatedAt: number | null
  acknowledgingKey: string | null
  acknowledgementErrors: Record<string, string>
  refresh: () => Promise<void>
  acknowledge: (alarm: ActiveAlarmResponse) => Promise<boolean>
}

export function alarmIdentity(
  alarm: Pick<ActiveAlarmResponse, 'location' | 'cluster' | 'alarm_name'>
): string {
  return `${alarm.location}:${alarm.cluster}:${alarm.alarm_name}`
}

export function useActiveAlarms(): ActiveAlarmsState {
  const ackInFlightRef = useRef<string | null>(null)
  const [alarms, setAlarms] = useState<ActiveAlarmResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [acknowledgingKey, setAcknowledgingKey] = useState<string | null>(null)
  const [acknowledgementErrors, setAcknowledgementErrors] = useState<Record<string, string>>({})
  const inFlightRef = useRef(false)

  const refresh = useCallback(async () => {
    if (inFlightRef.current) return
    inFlightRef.current = true
    try {
      const response = await apiClient.getActiveAlarms()
      setAlarms(Array.isArray(response.alarms) ? response.alarms : [])
      setError(null)
      setUpdatedAt(Date.now())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Alarm service unavailable')
    } finally {
      setLoading(false)
      inFlightRef.current = false
    }
  }, [])

  const acknowledge = useCallback(
    async (alarm: ActiveAlarmResponse): Promise<boolean> => {
      const key = alarmIdentity(alarm)
      if (ackInFlightRef.current === key || alarm.acknowledged) return false
      ackInFlightRef.current = key
      setAcknowledgingKey(key)
      setAcknowledgementErrors(previous => {
        const next = { ...previous }
        delete next[key]
        return next
      })
      try {
        await apiClient.acknowledgeAlarm(alarm.location, alarm.cluster, alarm.alarm_name)
        await refresh()
        return true
      } catch (reason) {
        setAcknowledgementErrors(previous => ({
          ...previous,
          [key]: reason instanceof Error ? reason.message : 'Acknowledgement failed',
        }))
        return false
      } finally {
        if (ackInFlightRef.current === key) ackInFlightRef.current = null
        setAcknowledgingKey(null)
      }
    },
    [refresh]
  )

  useEffect(() => {
    void refresh()
    const interval = setInterval(() => void refresh(), 5_000)
    return () => clearInterval(interval)
  }, [refresh])

  return {
    alarms,
    loading,
    error,
    updatedAt,
    acknowledgingKey,
    acknowledgementErrors,
    refresh,
    acknowledge,
  }
}
