import { useEffect, useMemo, useRef, useState } from 'react'

import { apiClient } from '../services/api'
import type { DeviceRegistryEntry } from '../types/device'
import type { PIDControlMode, PIDHistoryEntry, PIDModeInfo, PIDParameters } from '../types/pid'
import { extractErrorMessage } from '../utils/errors'

const PID_TYPES: Record<string, true> = { heating: true, cooling: true, co2: true }
const GAIN_FIELDS = ['kp', 'ki', 'kd'] as const

type GainField = (typeof GAIN_FIELDS)[number]
type GainDraft = Record<GainField, string>

interface PIDTuningPanelProps {
  readonly location: string
  readonly cluster: string
  readonly devices: readonly DeviceRegistryEntry[]
}

function toGainDraft(parameters: PIDParameters): GainDraft {
  return { kp: String(parameters.kp), ki: String(parameters.ki), kd: String(parameters.kd) }
}

function formatHistoryValue(value: number): string {
  return Number.isFinite(value) ? value.toFixed(3) : 'Unavailable'
}

function apiFailureMessage(error: unknown): string {
  const status =
    error && typeof error === 'object'
      ? (error as { response?: { status?: unknown } }).response?.status
      : undefined
  const detail = extractErrorMessage(error, 'Unable to save PID settings')
  if (status === 400 || status === 429) return `${status}: ${detail}`
  if (status === 404) return `PID tuning is unavailable for this device type. ${detail}`
  return detail
}

function finiteDraftValue(value: string): number {
  if (value.trim() === '') return Number.NaN
  return Number(value)
}

export default function PIDTuningPanel({ location, cluster, devices }: PIDTuningPanelProps) {
  const eligibleDevices = useMemo(
    () =>
      devices.filter(
        device =>
          device.location === location &&
          device.cluster === 'main' &&
          cluster === 'main' &&
          device.device_type !== 'light' &&
          PID_TYPES[device.device_type] === true &&
          device.channel !== null &&
          device.channel !== undefined
      ),
    [cluster, devices, location]
  )
  const types = useMemo(
    () => [...new Set(eligibleDevices.map(device => device.device_type))].sort(),
    [eligibleDevices]
  )
  const [deviceType, setDeviceType] = useState<string | null>(null)
  const [parameters, setParameters] = useState<PIDParameters | null>(null)
  const [modeInfo, setModeInfo] = useState<PIDModeInfo | null>(null)
  const [mode, setMode] = useState<PIDControlMode | null>(null)
  const [history, setHistory] = useState<PIDHistoryEntry[]>([])
  const [gainDraft, setGainDraft] = useState<GainDraft>({ kp: '', ki: '', kd: '' })
  const [highDraft, setHighDraft] = useState('')
  const [lowDraft, setLowDraft] = useState('')
  const [loading, setLoading] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [historyError, setHistoryError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const requestId = useRef(0)

  useEffect(() => {
    setDeviceType(current =>
      current !== null && types.includes(current) ? current : (types[0] ?? null)
    )
  }, [types])

  useEffect(() => {
    const id = ++requestId.current
    if (deviceType === null) {
      setParameters(null)
      setModeInfo(null)
      setMode(null)
      setHistory([])
      setLoading(false)
      setHistoryLoading(false)
      setLoadError(null)
      setHistoryError(null)
      return () => {
        requestId.current += 1
      }
    }

    setLoading(true)
    setHistoryLoading(true)
    setLoadError(null)
    setHistoryError(null)
    setValidationError(null)
    setSaveMessage(null)
    void apiClient
      .getPIDParameterHistoryForRoom(location, 'main', deviceType, 20)
      .then(rows => {
        if (requestId.current !== id) return
        setHistory(rows)
        setHistoryError(null)
      })
      .catch((error: unknown) => {
        if (requestId.current !== id) return
        setHistory([])
        setHistoryError(apiFailureMessage(error))
      })
      .finally(() => {
        if (requestId.current === id) setHistoryLoading(false)
      })

    void Promise.all([
      apiClient.getPIDParametersForRoom(location, 'main', deviceType),
      apiClient.getPIDModeForRoom(location, 'main', deviceType),
    ])
      .then(([loadedParameters, loadedMode]) => {
        if (requestId.current !== id) return
        if (loadedParameters.source === 'default') {
          setParameters(null)
          setModeInfo(null)
          setMode(null)
          setLoadError('PID configuration is unavailable for this device type.')
          return
        }
        setParameters(loadedParameters)
        setGainDraft(toGainDraft(loadedParameters))
        setModeInfo(loadedMode)
        setMode(loadedMode.mode)
        setHighDraft(String(loadedMode.hysteresis_high))
        setLowDraft(String(loadedMode.hysteresis_low))
        setLoadError(null)
      })
      .catch((error: unknown) => {
        if (requestId.current !== id) return
        setParameters(null)
        setModeInfo(null)
        setMode(null)
        setLoadError(apiFailureMessage(error))
      })
      .finally(() => {
        if (requestId.current === id) setLoading(false)
      })

    return () => {
      requestId.current += 1
    }
  }, [deviceType, location])

  const selectedDevices =
    deviceType === null ? [] : eligibleDevices.filter(device => device.device_type === deviceType)
  const gainValues = {
    kp: finiteDraftValue(gainDraft.kp),
    ki: finiteDraftValue(gainDraft.ki),
    kd: finiteDraftValue(gainDraft.kd),
  }
  const hasGainChanges =
    parameters !== null && GAIN_FIELDS.some(field => gainValues[field] !== parameters[field])
  const highValue = finiteDraftValue(highDraft)
  const lowValue = finiteDraftValue(lowDraft)
  const hasThresholdChanges =
    modeInfo !== null &&
    (highValue !== modeInfo.hysteresis_high || lowValue !== modeInfo.hysteresis_low)

  const refreshHistoryAndMode = async (type: string, refreshThresholds: boolean): Promise<void> => {
    const requestToken = requestId.current
    const [refreshedMode, refreshedHistory] = await Promise.all([
      apiClient.getPIDModeForRoom(location, 'main', type),
      apiClient.getPIDParameterHistoryForRoom(location, 'main', type, 20),
    ])
    if (requestId.current !== requestToken) return
    setModeInfo(refreshedMode)
    setMode(refreshedMode.mode)
    setHistory(refreshedHistory)
    setHistoryError(null)
    if (refreshThresholds) {
      setHighDraft(String(refreshedMode.hysteresis_high))
      setLowDraft(String(refreshedMode.hysteresis_low))
    }
  }

  const saveGains = async (): Promise<void> => {
    if (deviceType === null || parameters === null || !hasGainChanges) return
    if (!GAIN_FIELDS.every(field => Number.isFinite(gainValues[field]))) {
      setValidationError('Enter finite values for Kp, Ki, and Kd.')
      return
    }
    setSaving(true)
    setValidationError(null)
    setLoadError(null)
    setSaveMessage(null)
    try {
      const updated = await apiClient.updatePIDParametersForRoom(
        location,
        'main',
        deviceType,
        gainValues
      )
      setParameters(updated)
      setGainDraft(toGainDraft(updated))
      setSaveMessage('PID gains saved.')
      try {
        await refreshHistoryAndMode(deviceType, false)
      } catch (error: unknown) {
        setHistoryError(`Saved successfully, but refresh failed: ${apiFailureMessage(error)}`)
      }
    } catch (error: unknown) {
      setLoadError(apiFailureMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const saveThresholds = async (): Promise<void> => {
    if (deviceType === null || modeInfo === null || !hasThresholdChanges) return
    if (
      !Number.isFinite(highValue) ||
      !Number.isFinite(lowValue) ||
      highValue <= 0 ||
      lowValue <= 0
    ) {
      setValidationError('Both ON/OFF thresholds must be finite positive numbers.')
      return
    }
    setSaving(true)
    setValidationError(null)
    setLoadError(null)
    setSaveMessage(null)
    try {
      const updated = await apiClient.setPIDModeForRoom(location, 'main', deviceType, {
        mode: 'on_off',
        hysteresis_high: highValue,
        hysteresis_low: lowValue,
      })
      setModeInfo(updated)
      setMode(updated.mode)
      setHighDraft(String(updated.hysteresis_high))
      setLowDraft(String(updated.hysteresis_low))
      setSaveMessage('ON/OFF thresholds saved.')
      try {
        await refreshHistoryAndMode(deviceType, true)
      } catch (error: unknown) {
        setHistoryError(`Saved successfully, but refresh failed: ${apiFailureMessage(error)}`)
      }
    } catch (error: unknown) {
      setLoadError(apiFailureMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const changeMode = async (nextMode: PIDControlMode): Promise<void> => {
    if (deviceType === null || modeInfo === null || nextMode === mode) return
    if (
      nextMode === 'on_off' &&
      (!Number.isFinite(highValue) || !Number.isFinite(lowValue) || highValue <= 0 || lowValue <= 0)
    ) {
      setValidationError(
        'Both ON/OFF thresholds must be finite positive numbers before enabling ON/OFF mode.'
      )
      return
    }
    setSaving(true)
    setLoadError(null)
    setValidationError(null)
    setSaveMessage(null)
    const update =
      nextMode === 'on_off'
        ? { mode: nextMode, hysteresis_high: highValue, hysteresis_low: lowValue }
        : { mode: nextMode }
    try {
      const updated = await apiClient.setPIDModeForRoom(location, 'main', deviceType, update)
      setModeInfo(updated)
      setMode(updated.mode)
      setHighDraft(String(updated.hysteresis_high))
      setLowDraft(String(updated.hysteresis_low))
      setSaveMessage(
        `${nextMode === 'auto_pid' ? 'Autotune' : nextMode === 'pid' ? 'PID' : 'ON/OFF'} mode updated.`
      )
      try {
        await refreshHistoryAndMode(deviceType, true)
      } catch (error: unknown) {
        setHistoryError(`Mode updated, but refresh failed: ${apiFailureMessage(error)}`)
      }
    } catch (error: unknown) {
      setLoadError(apiFailureMessage(error))
    } finally {
      setSaving(false)
    }
  }

  if (eligibleDevices.length === 0) {
    return (
      <section
        className="rounded-lg border border-border-subtle bg-surface-primary p-3"
        aria-label="PID tuning panel"
      >
        <h2 className="text-sm font-bold text-text-default">PID tuning</h2>
        <p className="mt-2 text-xs text-text-subtle">
          No assigned heating, cooling, or CO₂ relay device is available for PID tuning in this
          room/main cluster.
        </p>
      </section>
    )
  }

  return (
    <section
      className="rounded-lg border border-border-subtle bg-surface-primary p-3"
      aria-label="PID tuning panel"
      data-testid="pid-tuning-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-bold uppercase tracking-wide text-text-default">
            PID tuning
          </h2>
          <p className="mt-1 text-xs text-text-subtle">
            Settings are scoped by room, main cluster, and device type.
          </p>
        </div>
        <label className="flex min-w-48 flex-col gap-1 text-xs text-text-subtle">
          Assigned PID device type
          <select
            aria-label="Assigned PID device type"
            className="rounded border border-border-default bg-surface-secondary px-2 py-1 text-text-default"
            value={deviceType ?? ''}
            onChange={event => setDeviceType(event.target.value || null)}
            disabled={saving}
          >
            {types.map(type => (
              <option key={type} value={type}>
                {type === 'co2' ? 'CO₂' : type}
              </option>
            ))}
          </select>
        </label>
      </div>

      {deviceType !== null && (
        <>
          {selectedDevices.length > 1 && (
            <p className="mt-2 rounded border border-status-info-border bg-status-info-bg/30 px-2 py-1 text-xs text-status-info-text">
              {selectedDevices.length} assigned devices share the {deviceType} tuning scope; these
              settings apply to the type, not an individual device.
            </p>
          )}
          <p className="mt-2 text-xs text-text-subtle">
            Registry PID enabled:{' '}
            <strong className="text-text-default">
              {selectedDevices.every(device => device.pid_enabled === true)
                ? 'yes'
                : selectedDevices.every(device => device.pid_enabled === false)
                  ? 'no'
                  : selectedDevices.some(device => device.pid_enabled !== undefined)
                    ? 'mixed / partially reported'
                    : 'not reported'}
            </strong>
            . Runtime PID eligibility is based on assigned device type; this flag does not hide
            tuning.
          </p>
        </>
      )}

      {loading ? (
        <p className="mt-3 text-sm text-text-subtle" role="status">
          Loading PID settings…
        </p>
      ) : null}
      {loadError ? (
        <p
          className="mt-3 rounded border border-status-danger-border bg-status-danger-bg/30 px-2 py-1 text-sm text-status-danger-text"
          role="alert"
        >
          {loadError}
        </p>
      ) : null}
      {validationError ? (
        <p className="mt-2 text-sm text-status-warning-text" role="alert">
          {validationError}
        </p>
      ) : null}
      {saveMessage ? (
        <p className="mt-2 text-sm text-status-success-text" role="status">
          {saveMessage}
        </p>
      ) : null}

      {!loading && parameters !== null && modeInfo !== null && mode !== null && (
        <div className="mt-3 grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="space-y-3">
            <label className="flex flex-col gap-1 text-xs text-text-subtle">
              Control mode
              <select
                aria-label="PID control mode"
                className="rounded border border-border-default bg-surface-secondary px-2 py-1 text-text-default"
                value={mode}
                onChange={event => void changeMode(event.target.value as PIDControlMode)}
                disabled={saving}
              >
                <option value="on_off">ON/OFF</option>
                <option value="pid">PID</option>
                <option value="auto_pid">Autotune (AUTO)</option>
              </select>
            </label>
            {mode === 'auto_pid' && modeInfo.autotune_active && (
              <p className="text-xs text-status-warning-text" role="status">
                Autotune is active for this device type.
              </p>
            )}

            {mode !== null && (
              <fieldset className="space-y-2 rounded border border-border-subtle p-2">
                <legend className="px-1 text-xs font-semibold text-text-default">
                  ON/OFF thresholds
                </legend>
                <p className="text-xs text-text-muted">
                  These positive high/low thresholds are saved through the room PID mode endpoint.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <label className="flex flex-col gap-1 text-xs text-text-subtle">
                    Hysteresis high
                    <input
                      aria-label="Hysteresis high"
                      type="number"
                      step="any"
                      value={highDraft}
                      onChange={event => {
                        setHighDraft(event.target.value)
                        setValidationError(null)
                        setSaveMessage(null)
                      }}
                      disabled={saving}
                      className="rounded border border-border-default bg-surface-secondary px-2 py-1 font-mono text-text-default"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-xs text-text-subtle">
                    Hysteresis low
                    <input
                      aria-label="Hysteresis low"
                      type="number"
                      step="any"
                      value={lowDraft}
                      onChange={event => {
                        setLowDraft(event.target.value)
                        setValidationError(null)
                        setSaveMessage(null)
                      }}
                      disabled={saving}
                      className="rounded border border-border-default bg-surface-secondary px-2 py-1 font-mono text-text-default"
                    />
                  </label>
                </div>
                <button
                  type="button"
                  onClick={() => void saveThresholds()}
                  disabled={saving || !hasThresholdChanges}
                  className="rounded bg-accent-dim px-3 py-1.5 text-xs font-semibold text-text-default disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save ON/OFF thresholds'}
                </button>
              </fieldset>
            )}

            {mode !== 'on_off' && (
              <fieldset
                className="space-y-2 rounded border border-border-subtle p-2"
                disabled={saving || mode === 'auto_pid'}
              >
                <legend className="px-1 text-xs font-semibold text-text-default">PID gains</legend>
                <div className="grid grid-cols-3 gap-2">
                  {GAIN_FIELDS.map(field => (
                    <label key={field} className="flex flex-col gap-1 text-xs text-text-subtle">
                      {field.toUpperCase()}
                      <input
                        aria-label={`PID ${field.toUpperCase()}`}
                        type="number"
                        step="any"
                        value={gainDraft[field]}
                        onChange={event => {
                          setGainDraft(previous => ({ ...previous, [field]: event.target.value }))
                          setValidationError(null)
                          setSaveMessage(null)
                        }}
                        className="min-w-0 rounded border border-border-default bg-surface-secondary px-2 py-1 font-mono text-text-default"
                      />
                    </label>
                  ))}
                </div>
                <p className="text-xs text-text-muted">
                  Gain bounds are validated by the server; only finite values are checked locally.
                </p>
                <button
                  type="button"
                  onClick={() => void saveGains()}
                  disabled={saving || !hasGainChanges || mode === 'auto_pid'}
                  className="rounded bg-accent-dim px-3 py-1.5 text-xs font-semibold text-text-default disabled:opacity-50"
                >
                  {saving ? 'Saving…' : 'Save PID gains'}
                </button>
              </fieldset>
            )}
          </div>

          <div className="min-h-32 rounded border border-border-subtle bg-surface-base/50 p-2">
            <div className="text-xs font-semibold text-text-default">
              Recorded parameter history
            </div>
            {historyLoading && (
              <p className="mt-2 text-xs text-text-subtle" role="status">
                Loading history…
              </p>
            )}
            {historyError && (
              <p className="mt-2 text-xs text-status-warning-text" role="alert">
                {historyError}
              </p>
            )}
            {!historyLoading && !historyError && history.length === 0 && (
              <p className="mt-2 text-xs text-text-subtle">
                No saved PID changes for this device type.
              </p>
            )}
            <ol className="mt-2 max-h-48 space-y-1 overflow-y-auto">
              {history.map((entry, index) => (
                <li
                  key={`${entry.changed_at}-${index}`}
                  className="rounded border border-border-subtle bg-surface-primary px-2 py-1 text-xs text-text-subtle"
                >
                  <div className="flex flex-wrap justify-between gap-x-2">
                    <time dateTime={entry.changed_at} className="text-text-default">
                      {new Date(entry.changed_at).toLocaleString()}
                    </time>
                    <span>
                      {entry.source ?? 'Unknown source'}
                      {entry.updated_by ? ` · ${entry.updated_by}` : ''}
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-3 font-mono">
                    <span>Kp {formatHistoryValue(entry.kp)}</span>
                    <span>Ki {formatHistoryValue(entry.ki)}</span>
                    <span>Kd {formatHistoryValue(entry.kd)}</span>
                    {entry.binary_hysteresis !== null && (
                      <span>
                        Recorded binary hysteresis {formatHistoryValue(entry.binary_hysteresis)}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </section>
  )
}
