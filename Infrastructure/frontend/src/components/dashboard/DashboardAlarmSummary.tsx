import { useMemo } from 'react'

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../ui/dialog'
import type { ActiveAlarmResponse } from '../../services/api/alarms'
import { alarmIdentity } from '../../hooks/useActiveAlarms'

interface AlarmActions {
  acknowledgingKey: string | null
  acknowledgementErrors: Record<string, string>
  acknowledge: (alarm: ActiveAlarmResponse) => Promise<boolean>
}

export interface DashboardAlarmButtonProps {
  alarms: ActiveAlarmResponse[]
  onOpen: () => void
}

export interface DashboardAlarmSummaryProps extends AlarmActions {
  alarms: ActiveAlarmResponse[]
  open: boolean
  onOpenChange: (open: boolean) => void
  serviceError: string | null
}

function formatAge(openedAt: string): string {
  const elapsed = Math.max(0, Date.now() - new Date(openedAt).getTime())
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 1) return 'now'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

function severityMark(severity: string): string {
  if (severity === 'critical') return '!'
  if (severity === 'warning') return '⚠'
  return 'i'
}

function severityClass(severity: string): string {
  if (severity === 'critical')
    return 'border-status-danger-border bg-status-danger-bg text-status-danger-text'
  if (severity === 'warning')
    return 'border-status-warning-border bg-status-warning-bg text-status-warning-text'
  return 'border-border-subtle bg-surface-secondary text-text-secondary'
}

export function DashboardAlarmButton({ alarms, onOpen }: DashboardAlarmButtonProps) {
  const unacknowledged = alarms.filter(alarm => !alarm.acknowledged).length
  const label = alarms.length === 0 ? 'Alarms 0' : `${unacknowledged}/${alarms.length} alarms`
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex shrink-0 items-center gap-1 rounded-sm border border-border-subtle px-2 py-1 text-10 font-semibold uppercase tracking-wide text-text-secondary hover:border-border-emphasis hover:text-text-default"
      aria-label={`${label}; open alarm list`}
    >
      <span
        aria-hidden="true"
        className={unacknowledged > 0 ? 'text-status-danger' : 'text-status-success'}
      >
        {unacknowledged > 0 ? '!' : '✓'}
      </span>
      <span>{label}</span>
    </button>
  )
}

export function DashboardAlarmSummary({
  alarms,
  open,
  onOpenChange,
  serviceError,
  acknowledgingKey,
  acknowledgementErrors,
  acknowledge,
}: DashboardAlarmSummaryProps) {
  const unacknowledged = useMemo(() => alarms.filter(alarm => !alarm.acknowledged), [alarms])
  const highestPriority = unacknowledged[0] ?? null

  return (
    <>
      {highestPriority && (
        <div
          role="alert"
          className="flex shrink-0 items-center justify-between gap-2 rounded border border-status-danger-border bg-status-danger-bg px-3 py-1.5 text-xs text-status-danger-text"
        >
          <span className="min-w-0 truncate">
            <strong>
              {severityMark(highestPriority.severity)} {highestPriority.severity.toUpperCase()}
            </strong>{' '}
            {highestPriority.location} / {highestPriority.cluster} · {highestPriority.message} ·{' '}
            {formatAge(highestPriority.opened_at)}
          </span>
          <button type="button" className="shrink-0 underline" onClick={() => onOpenChange(true)}>
            View alarms
          </button>
        </div>
      )}

      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-h-[80vh] w-full max-w-2xl overflow-y-auto bg-surface-primary p-4">
          <DialogTitle>Active alarms</DialogTitle>
          <DialogDescription className="mb-3">
            Acknowledgement records operator recognition only; active alarms remain active.
          </DialogDescription>
          {serviceError && (
            <p className="mb-2 text-xs text-status-warning-text">Alarm service: {serviceError}</p>
          )}
          {alarms.length === 0 ? (
            <p className="text-sm text-text-muted">No active alarms.</p>
          ) : (
            <div className="flex flex-col gap-2">
              {alarms.map(alarm => {
                const key = alarmIdentity(alarm)
                const error = acknowledgementErrors[key]
                return (
                  <div
                    key={key}
                    className={`rounded border px-2 py-2 text-xs ${severityClass(alarm.severity)}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-semibold uppercase tracking-wide">
                          <span aria-hidden="true">{severityMark(alarm.severity)}</span>{' '}
                          {alarm.severity} · {alarm.location} / {alarm.cluster}
                        </div>
                        <p className="mt-1 text-text-default">{alarm.message}</p>
                        <p className="mt-1 text-10 text-text-muted">
                          {alarm.alarm_name} · opened {formatAge(alarm.opened_at)} ·{' '}
                          {alarm.acknowledged
                            ? `Acknowledged by ${alarm.acknowledged_by ?? 'operator'}`
                            : 'Unacknowledged'}
                        </p>
                      </div>
                      {!alarm.acknowledged && (
                        <button
                          type="button"
                          disabled={acknowledgingKey === key}
                          onClick={() => void acknowledge(alarm)}
                          className="shrink-0 rounded border border-border-emphasis px-2 py-1 text-10 font-semibold text-text-default disabled:opacity-50"
                        >
                          {acknowledgingKey === key ? 'Saving…' : 'Acknowledge'}
                        </button>
                      )}
                    </div>
                    {error && <p className="mt-1 text-10 text-status-danger-text">{error}</p>}
                  </div>
                )
              })}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
