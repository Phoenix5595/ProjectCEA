import { useState, useEffect, useCallback, useRef, forwardRef, useImperativeHandle } from 'react'
import { toast } from 'sonner'

import { apiClient } from '../services/api'
import { logger } from '../utils/logger'

/** Subset of fields used by this widget (zone-status and legacy per-device load). */
interface LightIntensityRowStatus {
  intensity: number
  target_intensity?: number | null
  day_target_intensity?: number | null
  schedule_sun_target_intensity?: number | null
}

interface LightDevice {
  device_name: string
  display_name?: string
  dimming_enabled?: boolean
  dimming_board_id?: string | null
  dimming_channel?: number | null
}

/** Staged fixture edits reported to the page for cross-mode activation guarding. */
export interface LightPendingState {
  /** Active mode ID the staged edits were made against. */
  modeId: number | null
  /** Device names with unsaved staged targets. */
  devices: readonly string[]
}

export interface LightIntensityProps {
  location: string | null
  cluster: string | null
  compact?: boolean
  /** Active profile ID at staging time; sent as the guarded expected_mode_id. */
  activeModeId: number | null
  /** Notified whenever the staged-edit set or its mode binding changes. */
  onPendingChange?: (state: LightPendingState) => void
  /** False while a save is in flight or the page blocks editing. */
  targetEditingEnabled?: boolean
  /** Hide normal controls without unmounting or losing a stale light draft. */
  hiddenNormalControls?: boolean
}

/** Outcome of one pending-save flush, for the page's partial-save warning. */
export interface LightPendingSaveResult {
  readonly savedDevices: readonly string[]
  readonly failedDevices: readonly string[]
}

interface LightTargetDraft {
  location: string | null
  cluster: string | null
  modeId: number | null
  targets: Record<string, number>
}

const LightIntensity = forwardRef<
  { savePendingChanges: () => Promise<LightPendingSaveResult>; discardPendingChanges: () => void },
  LightIntensityProps
>(function LightIntensity(
  {
    location,
    cluster,
    compact,
    activeModeId,
    onPendingChange,
    targetEditingEnabled = true,
    hiddenNormalControls = false,
  },
  ref
) {
  const [statusData, setStatusData] = useState<{
    generation: number
    lights: LightDevice[]
    statuses: Record<string, LightIntensityRowStatus>
  } | null>(null)
  const [draft, setDraft] = useState<LightTargetDraft>({
    location,
    cluster,
    modeId: null,
    targets: {},
  })
  const draftRef = useRef(draft)
  const [validationErrors, setValidationErrors] = useState<Record<string, string>>({})
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const mountedRef = useRef(true)
  const clampTimers = useRef(new Map<string, number>())
  const clampEpoch = useRef(0)
  const contextRef = useRef({
    location, cluster, activeModeId, hiddenNormalControls, targetEditingEnabled, generation: 0,
  })
  const previous = contextRef.current
  contextRef.current = {
    location,
    cluster,
    activeModeId,
    hiddenNormalControls,
    targetEditingEnabled,
    generation: previous.generation + (
      previous.location !== location || previous.cluster !== cluster ||
      previous.activeModeId !== activeModeId ||
      previous.hiddenNormalControls !== hiddenNormalControls ? 1 : 0
    ),
  }
  const inFlightStatus = useRef<{ generation: number; promise: Promise<void> } | null>(null)
  const visibleDraft = draft.location === location && draft.cluster === cluster ? draft : null
  const pendingTargets = visibleDraft?.targets ?? {}
  const pendingNames = Object.keys(pendingTargets).sort()
  const hasPending = pendingNames.length > 0
  const staleDraft = hasPending && visibleDraft?.modeId !== activeModeId
  const editingDisabled = !targetEditingEnabled || saving || staleDraft ||
    activeModeId == null || hiddenNormalControls
  const invalidTargets = Object.values(pendingTargets).some(value => value < 10 || value > 100)
  const saveDisabled = editingDisabled || invalidTargets
  const lights = statusData?.generation === contextRef.current.generation ? statusData.lights : []
  const statuses = statusData?.generation === contextRef.current.generation ? statusData.statuses : {}

  const updateDraft = useCallback((next: LightTargetDraft) => {
    draftRef.current = next
    setDraft(next)
  }, [])

  const cancelClamps = useCallback(() => {
    clampEpoch.current += 1
    for (const timer of clampTimers.current.values()) clearTimeout(timer)
    clampTimers.current.clear()
  }, [])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      contextRef.current.generation += 1
      cancelClamps()
    }
  }, [cancelClamps])

  // A room change discards that room's draft; a mode change deliberately does not.
  useEffect(() => {
    cancelClamps()
    const current = draftRef.current
    if (current.location !== location || current.cluster !== cluster) {
      updateDraft({ location, cluster, modeId: null, targets: {} })
      setValidationErrors({})
    }
  }, [location, cluster, cancelClamps, updateDraft])

  useEffect(() => {
    const sameRoom = draft.location === location && draft.cluster === cluster
    const devices = sameRoom ? Object.keys(draft.targets).sort() : []
    onPendingChange?.({
      modeId: devices.length > 0 ? draft.modeId : null,
      devices,
    })
  }, [draft, location, cluster, onPendingChange])

  const fetchLightsAndStatus = useCallback((): Promise<void> => {
    const current = contextRef.current
    if (!mountedRef.current || !current.location || !current.cluster ||
        current.hiddenNormalControls) return Promise.resolve()
    const previousRequest = inFlightStatus.current
    if (previousRequest) {
      if (previousRequest.generation === current.generation) return previousRequest.promise
      // Wait for the old room/mode read without ever exposing its result here.
      return previousRequest.promise.then(() => fetchLightsAndStatus())
    }
    const { location: room, cluster: roomCluster, generation } = current
    const promise = (async () => {
      try {
        const data = await apiClient.getZoneLightsStatus(room, roomCluster)
        if (!mountedRef.current || contextRef.current.generation !== generation) return
        const rows = data.lights ?? []
        const lightDevices: LightDevice[] = rows.map(row => ({
          device_name: row.device,
          display_name: row.display_name,
          dimming_enabled: true,
          dimming_board_id: row.board_id != null ? String(row.board_id) : null,
          dimming_channel: row.channel ?? null,
        }))
        const statusMap: Record<string, LightIntensityRowStatus> = {}
        for (const row of rows) {
          const sunTarget = row.schedule_sun_target_intensity ??
            row.day_target_intensity ?? row.target_intensity ?? null
          statusMap[row.device] = {
            intensity: row.intensity,
            target_intensity: row.target_intensity ?? null,
            day_target_intensity: sunTarget,
            schedule_sun_target_intensity: sunTarget,
          }
        }
        setStatusData({ generation, lights: lightDevices, statuses: statusMap })
      } catch (err) {
        if (mountedRef.current && contextRef.current.generation === generation) {
          logger.error('Failed to load zone light status:', err)
        }
      } finally {
        if (mountedRef.current && contextRef.current.generation === generation) setLoading(false)
      }
    })()
    inFlightStatus.current = { generation, promise }
    void promise.then(() => {
      if (inFlightStatus.current?.promise === promise) inFlightStatus.current = null
    })
    return promise
  }, [])

  useEffect(() => {
    cancelClamps()
    setStatusData(null)
    setLoading(!hiddenNormalControls && Boolean(location && cluster))
    if (hiddenNormalControls || !location || !cluster) return
    void fetchLightsAndStatus()
    const interval = setInterval(() => { void fetchLightsAndStatus() }, 5000)
    return () => clearInterval(interval)
  }, [location, cluster, activeModeId, hiddenNormalControls, fetchLightsAndStatus, cancelClamps])

  function handleTargetChange(deviceName: string, value: number) {
    if (editingDisabled || savingRef.current) return
    const clampedValue = Math.max(0, Math.min(100, value))
    const previousTimer = clampTimers.current.get(deviceName)
    clearTimeout(previousTimer)
    clampTimers.current.delete(deviceName)
    const current = draftRef.current
    const sameRoom = current.location === location && current.cluster === cluster
    updateDraft({
      location,
      cluster,
      modeId: sameRoom && Object.keys(current.targets).length > 0 ? current.modeId : activeModeId,
      targets: { ...(sameRoom ? current.targets : {}), [deviceName]: clampedValue },
    })
    setValidationErrors(prev => {
      const next = { ...prev }
      if (value < 10) next[deviceName] = 'Minimum target is 10%'
      else delete next[deviceName]
      return next
    })
    if (value >= 10) return
    const generation = contextRef.current.generation
    const epoch = clampEpoch.current
    const timer = window.setTimeout(() => {
      if (!mountedRef.current || generation !== contextRef.current.generation ||
          epoch !== clampEpoch.current || savingRef.current ||
          clampTimers.current.get(deviceName) !== timer) return
      clampTimers.current.delete(deviceName)
      const latest = draftRef.current
      if (latest.location !== location || latest.cluster !== cluster ||
          latest.modeId !== activeModeId || latest.targets[deviceName] !== clampedValue) return
      updateDraft({ ...latest, targets: { ...latest.targets, [deviceName]: 10 } })
      setValidationErrors(prev => {
        const next = { ...prev }
        delete next[deviceName]
        return next
      })
    }, 2000)
    clampTimers.current.set(deviceName, timer)
  }

  function discardPendingChanges() {
    if (savingRef.current) return
    cancelClamps()
    updateDraft({ location, cluster, modeId: null, targets: {} })
    setValidationErrors({})
  }

  /** Flush only this captured mode's targets; the page owns combined-save messaging. */
  async function savePendingChanges(): Promise<LightPendingSaveResult> {
    const captured = draftRef.current
    const entries = Object.entries(captured.targets)
    if (entries.length === 0) return { savedDevices: [], failedDevices: [] }
    cancelClamps()
    const current = contextRef.current
    if (savingRef.current || !current.targetEditingEnabled || current.hiddenNormalControls ||
        !captured.location || !captured.cluster || captured.modeId == null ||
        captured.location !== current.location || captured.cluster !== current.cluster ||
        captured.modeId !== current.activeModeId ||
        entries.some(([, value]) => value < 10 || value > 100)) {
      return { savedDevices: [], failedDevices: entries.map(([name]) => name) }
    }
    const generation = current.generation
    const failed: string[] = []
    const saved: string[] = []
    savingRef.current = true
    setSaving(true)
    try {
      for (const [deviceName, target] of entries) {
        const latestContext = contextRef.current
        if (latestContext.generation !== generation || !latestContext.targetEditingEnabled) {
          failed.push(deviceName)
          continue
        }
        try {
          const res = await apiClient.setLightIntensity(
            captured.location,
            captured.cluster,
            deviceName,
            target,
            { expectedModeId: captured.modeId }
          )
          if (!res.success || (res.rows_updated !== undefined && res.rows_updated < 1)) {
            failed.push(deviceName)
            continue
          }
          saved.push(deviceName)
          const latest = draftRef.current
          if (mountedRef.current && latest.location === captured.location &&
              latest.cluster === captured.cluster && latest.modeId === captured.modeId &&
              latest.targets[deviceName] === target) {
            const targets = { ...latest.targets }
            delete targets[deviceName]
            updateDraft({ ...latest, modeId: Object.keys(targets).length > 0 ? latest.modeId : null, targets })
          }
        } catch (err) {
          logger.error(`Failed to set light intensity for ${deviceName}:`, err)
          failed.push(deviceName)
        }
      }
      if (saved.length > 0 && contextRef.current.generation === generation) {
        await fetchLightsAndStatus()
      }
    } finally {
      savingRef.current = false
      if (mountedRef.current) setSaving(false)
    }
    return { savedDevices: saved, failedDevices: failed }
  }

  async function saveFromPanel() {
    const result = await savePendingChanges()
    if (result.failedDevices.length > 0) {
      toast.error(`Light targets failed: ${result.failedDevices.join(', ')}. Failed edits remain pending.`)
    } else if (result.savedDevices.length > 0) {
      toast.success('Light targets applied')
    }
  }

  useImperativeHandle(ref, () => ({ savePendingChanges, discardPendingChanges }))

  if (hiddenNormalControls && !hasPending && !saving) return null

  if (loading && !hasPending && !hiddenNormalControls) {
    return (
      <div className="bg-surface-primary rounded-lg border border-border-subtle p-2 h-full flex flex-col">
        <div className="text-text-muted uppercase font-bold tracking-wider text-14 mb-4">
          Light intensity
        </div>
        <div className="text-text-subtle text-sm flex-1 flex items-center justify-center">
          Loading...
        </div>
      </div>
    )
  }

  return (
    <div className="bg-surface-primary rounded-lg border border-border-subtle p-2 h-full flex flex-col">
      <div className="flex items-center justify-between mb-2">
        <div className="text-14 text-text-muted uppercase font-bold tracking-wider">
          Light intensity
        </div>
        <div className="flex items-center gap-2">
          {(hasPending || saving) && (
            <div className="flex items-center gap-1">
              {saving && (
                <span className="text-10 text-text-muted font-bold">Saving…</span>
              )}
              <button
                type="button"
                data-testid="save-light-targets"
                onClick={() => {
                  void saveFromPanel()
                }}
                disabled={saveDisabled}
                className="px-2 py-0.5 text-10 font-bold rounded bg-accent-vivid text-accent-vivid-foreground hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Save light targets
              </button>
              <button
                type="button"
                data-testid="discard-light-edits"
                onClick={() => {
                  void discardPendingChanges()
                }}
                disabled={saving}
                className="px-2 py-0.5 text-10 font-bold rounded bg-surface-tertiary text-text-default border border-border-default hover:bg-surface-quaternary disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Discard light edits
              </button>
            </div>
          )}
          {!hiddenNormalControls && lights.map(light => {
            const status = statuses[light.device_name!]
            const isOn = status && status.intensity > 0
            return (
              <div
                key={light.device_name}
                className={`text-14 px-1 py-0 rounded cursor-help transition-colors ${
                  isOn
                    ? 'bg-status-success-bg/50 text-status-success border border-status-success-border/50'
                    : 'bg-surface-secondary text-text-subtle border border-border-default'
                }`}
                title={`${light.display_name || light.device_name}: ${isOn ? 'Sun' : 'Moon'}`}
              >
                {isOn ? '☀️' : '🌙'}
              </div>
            )
          })}
        </div>
      </div>
      {staleDraft && (
        <div role="alert" className="mb-2 text-12 text-status-warning">
          Stale light edits belong to mode {visibleDraft!.modeId ?? 'unknown'}, not the active mode.
          Discard light edits before editing targets for the new active mode.
        </div>
      )}
      {(staleDraft || hiddenNormalControls) && hasPending && (
        <div className="mb-2 text-12 text-text-muted">
          Pending light targets: {pendingNames.map(name => `${name}: ${pendingTargets[name]}%`).join(', ')}
        </div>
      )}

      {!hiddenNormalControls && (loading ? (
        <div className="text-text-subtle text-sm">Loading...</div>
      ) : lights.length === 0 ? (
        <div className="text-text-subtle text-sm flex-1 flex items-center justify-center">
          No dimmable fixtures found
        </div>
      ) : (
        <div className="flex-1 overflow-hidden flex flex-col gap-2 min-h-0">
          {lights.map(light => {
            const status = statuses[light.device_name!]
            if (!status) return null

            const currentIntensity = status.intensity
            const dayTarget =
              status.day_target_intensity ??
              status.schedule_sun_target_intensity ??
              status.target_intensity ??
              0
            const savedTarget = dayTarget
            const pendingTarget = staleDraft ? undefined : pendingTargets[light.device_name!]
            const displayTarget = pendingTarget ?? savedTarget
            const sliderPosition = currentIntensity
            const isOn = status && status.intensity > 0

            return (
              <div
                key={light.device_name}
                className={`${!isOn ? 'opacity-50' : ''} flex items-center gap-3 flex-1 min-h-0 bg-surface-secondary/30 rounded px-2`}
              >
                <div
                  className={`${compact ? 'text-14 w-[80px]' : 'text-[16px] w-[100px]'} text-text-secondary font-bold whitespace-normal leading-tight tracking-wider shrink-0`}
                  title={light.display_name || light.device_name}
                >
                  {light.display_name || light.device_name}
                </div>

                <div className="flex flex-col justify-center gap-1 shrink-0 w-16">
                  <div className="flex items-center justify-between bg-surface-secondary px-1 py-0.5 rounded-sm">
                    <span className="text-accent-setpoint font-mono tabular-nums text-12 leading-none">
                      {dayTarget}%
                    </span>
                    <span className="text-text-subtle text-9 leading-none">TGT</span>
                  </div>
                  <div className="flex items-center justify-between bg-surface-secondary px-1 py-0.5 rounded-sm">
                    <span className="text-accent-data font-mono tabular-nums text-12 leading-none">
                      {currentIntensity}%
                    </span>
                    <span className="text-text-subtle text-9 leading-none">CUR</span>
                  </div>
                </div>

                <div className="flex-1 flex items-center h-full py-2">
                  <div className="relative w-full h-full min-h-[40px]">
                    <div className="absolute inset-0 bg-surface-secondary rounded overflow-hidden shadow-inner">
                      <div
                        className="absolute top-0 bottom-0 right-0 bg-linear-to-l from-btn-primary-hover to-btn-primary-data transition-[width]"
                        style={{ width: `${sliderPosition}%` }}
                      />
                    </div>
                    <input
                      type="range"
                      dir="rtl"
                      aria-label={`${light.display_name || light.device_name} light target slider`}
                      min={0}
                      max={100}
                      value={displayTarget}
                      disabled={editingDisabled}
                      onChange={e => {
                        const value = parseInt(e.target.value)
                        if (!isNaN(value)) {
                          handleTargetChange(light.device_name!, value)
                        }
                      }}
                      className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
                      title="Sun target: editable even when lights are off"
                    />
                    {/* Scale: 0% at right, 100% at left — matches fill + RTL range */}
                    {dayTarget > 0 && (
                      <div
                        className="absolute top-0 bottom-0 w-1 bg-accent-setpoint rounded-sm -translate-x-1/2 pointer-events-none"
                        style={{ left: `${100 - dayTarget}%` }}
                        title={`Sun target: ${dayTarget}%`}
                      />
                    )}
                    {pendingTarget !== undefined && (
                      <div
                        className="absolute top-0 bottom-0 w-1 bg-status-warning rounded-sm -translate-x-1/2 pointer-events-none"
                        style={{ left: `${100 - displayTarget}%` }}
                        title={`Pending: ${displayTarget}%`}
                      />
                    )}
                  </div>
                </div>

                <div className="flex flex-col items-center gap-1 shrink-0 ml-3">
                  <input
                    type="number"
                    aria-label={`${light.display_name || light.device_name} light target`}
                    min={10}
                    max={100}
                    value={displayTarget}
                    disabled={editingDisabled}
                    onChange={e => {
                      const value = parseInt(e.target.value)
                      if (!isNaN(value)) {
                        handleTargetChange(light.device_name!, value)
                      }
                    }}
                    className={`w-14 h-6 px-1 text-center bg-surface-secondary border rounded-sm text-14 text-text-input font-mono ${
                      validationErrors[light.device_name!]
                        ? 'border-status-danger'
                        : 'border-border-default'
                    }`}
                  />
                  {validationErrors[light.device_name!] && (
                    <div className="text-10 text-status-danger font-bold">
                      {validationErrors[light.device_name!]}
                    </div>
                  )}
                  {!validationErrors[light.device_name!] && (
                    <span className="text-10 text-text-subtle font-bold tracking-wide">
                      % SET
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
})
export default LightIntensity
