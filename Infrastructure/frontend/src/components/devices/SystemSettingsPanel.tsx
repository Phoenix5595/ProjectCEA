import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { apiClient } from '../../services/api'
import type {
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  DfrBoardConfig,
  PidLimitsPair,
  SystemConfigResponse,
} from '../../types/systemConfig'
import { logger } from '../../utils/logger'

interface DraftHardware {
  i2c_bus: string
  mcp_i2c_bus: string
  dfr0971_i2c_bus: string
  i2c_address: string
  active_low: boolean
  require_mcp: boolean
  dfr0971_boards: Array<{ board_id: number; i2c_address: string; name: string }>
}

interface DraftSafetyLimits {
  min_temperature: string
  max_temperature: string
  min_humidity: string
  max_humidity: string
  min_co2: string
  max_co2: string
}

interface DraftTuning {
  update_interval: string
  last_good_hold_period: string
  binary_hysteresis: string
}

interface DraftPidLimitsPair {
  kp_min: string
  kp_max: string
  ki_min: string
  ki_max: string
  kd_min: string
  kd_max: string
}

interface DraftPidLimits {
  heater: DraftPidLimitsPair
  fan: DraftPidLimitsPair
  co2: DraftPidLimitsPair
}

interface DraftState {
  hardware: DraftHardware
  safety_limits: DraftSafetyLimits
  tuning: DraftTuning
  pid_limits: DraftPidLimits
}

function configToDraft(config: SystemConfigResponse): DraftState {
  const pidToDraft = (pair: PidLimitsPair | null | undefined): DraftPidLimitsPair => {
    const p = pair ?? { kp_min: 0, kp_max: 0, ki_min: 0, ki_max: 0, kd_min: 0, kd_max: 0 }
    return {
      kp_min: String(p.kp_min),
      kp_max: String(p.kp_max),
      ki_min: String(p.ki_min),
      ki_max: String(p.ki_max),
      kd_min: String(p.kd_min),
      kd_max: String(p.kd_max),
    }
  }

  return {
    hardware: {
      i2c_bus: String(config.hardware.i2c_bus ?? ''),
      mcp_i2c_bus: String(config.hardware.mcp_i2c_bus ?? ''),
      dfr0971_i2c_bus: String(config.hardware.dfr0971_i2c_bus ?? ''),
      i2c_address: String(config.hardware.i2c_address ?? ''),
      active_low: config.hardware.active_low ?? false,
      require_mcp: config.hardware.require_mcp ?? false,
      dfr0971_boards: (config.hardware.dfr0971_boards ?? []).map((b: DfrBoardConfig) => ({
        board_id: b.board_id,
        i2c_address: String(b.i2c_address),
        name: b.name,
      })),
    },
    safety_limits: {
      min_temperature: String(config.safety_limits.min_temperature ?? ''),
      max_temperature: String(config.safety_limits.max_temperature ?? ''),
      min_humidity: String(config.safety_limits.min_humidity ?? ''),
      max_humidity: String(config.safety_limits.max_humidity ?? ''),
      min_co2: String(config.safety_limits.min_co2 ?? ''),
      max_co2: String(config.safety_limits.max_co2 ?? ''),
    },
    tuning: {
      update_interval: String(config.tuning.update_interval ?? ''),
      last_good_hold_period: String(config.tuning.last_good_hold_period ?? ''),
      binary_hysteresis: String(config.tuning.binary_hysteresis ?? ''),
    },
    pid_limits: {
      heater: pidToDraft(config.pid_limits.heater),
      fan: pidToDraft(config.pid_limits.fan),
      co2: pidToDraft(config.pid_limits.co2),
    },
  }
}

function numChanged(draftStr: string, original: number | null | undefined): boolean {
  const draftNum = draftStr.trim() === '' ? null : parseFloat(draftStr)
  const origNum = original ?? null
  if (draftNum === null && origNum === null) return false
  if (draftNum === null || origNum === null) return true
  return draftNum !== origNum
}

function boolChanged(draftBool: boolean, original: boolean | null | undefined): boolean {
  return draftBool !== (original ?? false)
}

function strChanged(draftStr: string, original: string | null | undefined): boolean {
  return draftStr !== (original ?? '')
}

function buildUpdateRequest(draft: DraftState, original: SystemConfigResponse): ConfigUpdateRequest {
  const update: ConfigUpdateRequest = {}

  const hw: ConfigUpdateRequest['hardware'] = {}
  if (numChanged(draft.hardware.i2c_bus, original.hardware.i2c_bus)) {
    hw.i2c_bus = draft.hardware.i2c_bus.trim() === '' ? null : parseFloat(draft.hardware.i2c_bus)
  }
  if (numChanged(draft.hardware.mcp_i2c_bus, original.hardware.mcp_i2c_bus)) {
    hw.mcp_i2c_bus = draft.hardware.mcp_i2c_bus.trim() === '' ? null : parseFloat(draft.hardware.mcp_i2c_bus)
  }
  if (numChanged(draft.hardware.dfr0971_i2c_bus, original.hardware.dfr0971_i2c_bus)) {
    hw.dfr0971_i2c_bus = draft.hardware.dfr0971_i2c_bus.trim() === '' ? null : parseFloat(draft.hardware.dfr0971_i2c_bus)
  }
  if (numChanged(draft.hardware.i2c_address, original.hardware.i2c_address)) {
    hw.i2c_address = draft.hardware.i2c_address.trim() === '' ? null : parseFloat(draft.hardware.i2c_address)
  }
  if (boolChanged(draft.hardware.active_low, original.hardware.active_low)) {
    hw.active_low = draft.hardware.active_low
  }
  if (boolChanged(draft.hardware.require_mcp, original.hardware.require_mcp)) {
    hw.require_mcp = draft.hardware.require_mcp
  }
  const boardsChanged = draft.hardware.dfr0971_boards.length !== (original.hardware.dfr0971_boards ?? []).length ||
    draft.hardware.dfr0971_boards.some((db, i) => {
      const ob = (original.hardware.dfr0971_boards ?? [])[i]
      if (!ob) return true
      return numChanged(db.i2c_address, ob.i2c_address) || strChanged(db.name, ob.name)
    })
  if (boardsChanged) {
    hw.dfr0971_boards = draft.hardware.dfr0971_boards.map(b => ({
      board_id: b.board_id,
      i2c_address: parseFloat(b.i2c_address),
      name: b.name,
    }))
  }
  if (Object.keys(hw).length > 0) {
    update.hardware = hw
  }

  const sl: ConfigUpdateRequest['safety_limits'] = {}
  if (numChanged(draft.safety_limits.min_temperature, original.safety_limits.min_temperature)) {
    sl.min_temperature = parseFloat(draft.safety_limits.min_temperature)
  }
  if (numChanged(draft.safety_limits.max_temperature, original.safety_limits.max_temperature)) {
    sl.max_temperature = parseFloat(draft.safety_limits.max_temperature)
  }
  if (numChanged(draft.safety_limits.min_humidity, original.safety_limits.min_humidity)) {
    sl.min_humidity = parseFloat(draft.safety_limits.min_humidity)
  }
  if (numChanged(draft.safety_limits.max_humidity, original.safety_limits.max_humidity)) {
    sl.max_humidity = parseFloat(draft.safety_limits.max_humidity)
  }
  if (numChanged(draft.safety_limits.min_co2, original.safety_limits.min_co2)) {
    sl.min_co2 = parseFloat(draft.safety_limits.min_co2)
  }
  if (numChanged(draft.safety_limits.max_co2, original.safety_limits.max_co2)) {
    sl.max_co2 = parseFloat(draft.safety_limits.max_co2)
  }
  if (Object.keys(sl).length > 0) {
    update.safety_limits = sl
  }

  const tuning: ConfigUpdateRequest['tuning'] = {}
  if (numChanged(draft.tuning.update_interval, original.tuning.update_interval)) {
    tuning.update_interval = parseFloat(draft.tuning.update_interval)
  }
  if (numChanged(draft.tuning.last_good_hold_period, original.tuning.last_good_hold_period)) {
    tuning.last_good_hold_period = parseFloat(draft.tuning.last_good_hold_period)
  }
  if (numChanged(draft.tuning.binary_hysteresis, original.tuning.binary_hysteresis)) {
    tuning.binary_hysteresis = parseFloat(draft.tuning.binary_hysteresis)
  }

  const pidTypes: Array<'heater' | 'fan' | 'co2'> = ['heater', 'fan', 'co2']
  for (const type of pidTypes) {
    const draftPair = draft.pid_limits[type]
    const origPair = original.pid_limits[type]
    const pair: Partial<PidLimitsPair> = {}
    if (numChanged(draftPair.kp_min, origPair?.kp_min)) pair.kp_min = parseFloat(draftPair.kp_min)
    if (numChanged(draftPair.kp_max, origPair?.kp_max)) pair.kp_max = parseFloat(draftPair.kp_max)
    if (numChanged(draftPair.ki_min, origPair?.ki_min)) pair.ki_min = parseFloat(draftPair.ki_min)
    if (numChanged(draftPair.ki_max, origPair?.ki_max)) pair.ki_max = parseFloat(draftPair.ki_max)
    if (numChanged(draftPair.kd_min, origPair?.kd_min)) pair.kd_min = parseFloat(draftPair.kd_min)
    if (numChanged(draftPair.kd_max, origPair?.kd_max)) pair.kd_max = parseFloat(draftPair.kd_max)
    if (Object.keys(pair).length > 0) {
      tuning.pid_limits = tuning.pid_limits ?? {}
      tuning.pid_limits[type] = pair as PidLimitsPair
    }
  }

  if (Object.keys(tuning).length > 0) {
    update.tuning = tuning
  }

  return update
}

function isDirty(draft: DraftState, original: SystemConfigResponse): boolean {
  return Object.keys(buildUpdateRequest(draft, original)).length > 0
}

function validateDraft(draft: DraftState): Record<string, string> {
  const errors: Record<string, string> = {}

  const minT = parseFloat(draft.safety_limits.min_temperature)
  const maxT = parseFloat(draft.safety_limits.max_temperature)
  if (!isNaN(minT) && !isNaN(maxT) && minT >= maxT) {
    errors.max_temperature = 'Max temperature must be greater than min'
  }

  const minH = parseFloat(draft.safety_limits.min_humidity)
  const maxH = parseFloat(draft.safety_limits.max_humidity)
  if (!isNaN(minH) && !isNaN(maxH) && minH >= maxH) {
    errors.max_humidity = 'Max humidity must be greater than min'
  }

  const minC = parseFloat(draft.safety_limits.min_co2)
  const maxC = parseFloat(draft.safety_limits.max_co2)
  if (!isNaN(minC) && !isNaN(maxC) && minC >= maxC) {
    errors.max_co2 = 'Max CO2 must be greater than min'
  }

  const pidTypes: Array<'heater' | 'fan' | 'co2'> = ['heater', 'fan', 'co2']
  for (const type of pidTypes) {
    const pair = draft.pid_limits[type]
    const kpMin = parseFloat(pair.kp_min)
    const kpMax = parseFloat(pair.kp_max)
    if (!isNaN(kpMin) && !isNaN(kpMax) && kpMin > kpMax) {
      errors[`pid_${type}_kp_max`] = 'kp_max must be >= kp_min'
    }
    const kiMin = parseFloat(pair.ki_min)
    const kiMax = parseFloat(pair.ki_max)
    if (!isNaN(kiMin) && !isNaN(kiMax) && kiMin > kiMax) {
      errors[`pid_${type}_ki_max`] = 'ki_max must be >= ki_min'
    }
    const kdMin = parseFloat(pair.kd_min)
    const kdMax = parseFloat(pair.kd_max)
    if (!isNaN(kdMin) && !isNaN(kdMax) && kdMin > kdMax) {
      errors[`pid_${type}_kd_max`] = 'kd_max must be >= kd_min'
    }
  }

  return errors
}

export default function SystemSettingsPanel() {
  const [config, setConfig] = useState<SystemConfigResponse | null>(null)
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [pendingChanges, setPendingChanges] = useState<string[]>([])
  const [restarting, setRestarting] = useState(false)
  const [showActiveLowModal, setShowActiveLowModal] = useState(false)
  const [activeLowConfirmText, setActiveLowConfirmText] = useState('')
  const [inlineErrors, setInlineErrors] = useState<Record<string, string>>({})
  const [pendingSave, setPendingSave] = useState<ConfigUpdateRequest | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const restartPollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadConfig = useCallback(async () => {
    try {
      const cfg = await apiClient.getConfig()
      setConfig(prev => {
        if (!prev) {
          setDraft(configToDraft(cfg))
        }
        return cfg
      })
      setPendingChanges(cfg.pending_restart_required_changes)
    } catch (err) {
      logger.error('Failed to load config', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadConfig()
    pollRef.current = setInterval(loadConfig, 15000)
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
      if (restartPollRef.current) clearInterval(restartPollRef.current)
    }
  }, [loadConfig])

  const handleSave = async () => {
    if (!config || !draft) return

    const validationErrors = validateDraft(draft)
    if (Object.keys(validationErrors).length > 0) {
      setInlineErrors(validationErrors)
      toast.error('Please fix validation errors before saving')
      return
    }

    const update = buildUpdateRequest(draft, config)
    if (Object.keys(update).length === 0) {
      toast.info('No changes to save')
      return
    }

    if (update.hardware?.active_low !== undefined && boolChanged(draft.hardware.active_low, config.hardware.active_low)) {
      setPendingSave(update)
      setShowActiveLowModal(true)
      setActiveLowConfirmText('')
      return
    }

    await executeSave(update)
  }

  const executeSave = async (update: ConfigUpdateRequest) => {
    if (!config) return
    setSaving(true)
    setInlineErrors({})
    try {
      const response: ConfigUpdateResponse = await apiClient.putConfig(update)
      setPendingChanges(response.pending_restart_required_changes)

      const nextConfig: SystemConfigResponse = {
        ...config,
        hardware: { ...config.hardware, ...(update.hardware ?? {}) },
        safety_limits: { ...config.safety_limits, ...(update.safety_limits ?? {}) },
        tuning: {
          ...config.tuning,
          update_interval: update.tuning?.update_interval ?? config.tuning.update_interval,
          last_good_hold_period: update.tuning?.last_good_hold_period ?? config.tuning.last_good_hold_period,
          binary_hysteresis: update.tuning?.binary_hysteresis ?? config.tuning.binary_hysteresis,
        },
        pid_limits: {
          ...config.pid_limits,
          ...(update.tuning?.pid_limits ?? {}),
        },
        pending_restart_required_changes: response.pending_restart_required_changes,
        restart_hashes: response.restart_hashes,
      }
      setConfig(nextConfig)
      setDraft(configToDraft(nextConfig))
      toast.success('Saved.')
    } catch (err: any) {
      logger.error('Failed to save config', err)
      if (err.response?.status === 422) {
        setInlineErrors({ general: err.response.data?.detail || 'Validation failed' })
        toast.error(err.response.data?.detail || 'Validation failed')
      } else {
        toast.error('Failed to save config')
      }
    } finally {
      setSaving(false)
      setPendingSave(null)
      setShowActiveLowModal(false)
    }
  }

  const handleConfirmActiveLow = async () => {
    if (!pendingSave) return
    const expected = String(config?.hardware.active_low ?? false)
    if (activeLowConfirmText.trim().toLowerCase() !== expected.toLowerCase()) {
      toast.error(`Type '${expected}' to confirm`)
      return
    }
    await executeSave(pendingSave)
  }

  const handleRestart = async () => {
    setRestarting(true)
    try {
      await apiClient.restartService()
      restartPollRef.current = setInterval(async () => {
        try {
          const cfg = await apiClient.getConfig()
          setPendingChanges(cfg.pending_restart_required_changes)
          if (cfg.pending_restart_required_changes.length === 0) {
            if (restartPollRef.current) {
              clearInterval(restartPollRef.current)
              restartPollRef.current = null
            }
            setRestarting(false)
            toast.success('Service restarted. Changes applied.')
          }
        } catch (err) {
          logger.error('Restart poll failed', err)
        }
      }, 2000)
    } catch (err) {
      logger.error('Failed to restart service', err)
      setRestarting(false)
      toast.error('Failed to restart service')
    }
  }

  const updateDraft = (path: string, value: string | boolean) => {
    setDraft(prev => {
      if (!prev) return prev
      const next = structuredClone(prev)
      const parts = path.split('.')
      let target: any = next
      for (let i = 0; i < parts.length - 1; i++) {
        target = target[parts[i]]
      }
      target[parts[parts.length - 1]] = value
      return next
    })
    setInlineErrors(prev => {
      const next = { ...prev }
      delete next[path]
      return next
    })
  }

  if (loading) {
    return <div className="p-4 text-text-muted">Loading settings...</div>
  }

  if (!config || !draft) {
    return <div className="p-4 text-status-danger">Failed to load settings</div>
  }

  const dirty = isDirty(draft, config)

  return (
    <div className="space-y-6 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text-default">System Settings</h2>
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving}
          className="rounded bg-accent px-4 py-2 text-sm font-medium text-surface-base hover:bg-accent-data disabled:opacity-50 disabled:cursor-not-allowed"
          data-testid="save-button"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      {/* Restart button */}
      {pendingChanges.length > 0 && (
        <div className="flex items-center gap-3 rounded border border-red-500/50 bg-red-950/30 p-3">
          <button
            type="button"
            onClick={handleRestart}
            disabled={restarting}
            className="flash-red rounded px-4 py-2 text-sm font-bold text-white disabled:opacity-70"
            data-testid="restart-button"
          >
            {restarting ? 'Restarting...' : `Restart to apply (${pendingChanges.length})`}
          </button>
          <span className="text-xs text-text-muted">
            {pendingChanges.slice(0, 3).join(', ')}
            {pendingChanges.length > 3 && ` +${pendingChanges.length - 3} more`}
          </span>
        </div>
      )}

      {/* Inline errors */}
      {inlineErrors.general && (
        <div className="rounded border border-status-danger/50 bg-status-danger/10 p-3 text-sm text-status-danger">
          {inlineErrors.general}
        </div>
      )}

      {/* Hardware Section */}
      <section className="rounded border border-border-subtle bg-surface-primary p-4">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">Hardware</h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs text-text-secondary">I2C Bus</label>
            <input
              type="number"
              value={draft.hardware.i2c_bus}
              onChange={e => updateDraft('hardware.i2c_bus', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">MCP I2C Bus</label>
            <input
              type="number"
              value={draft.hardware.mcp_i2c_bus}
              onChange={e => updateDraft('hardware.mcp_i2c_bus', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="mcp_i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">DFR I2C Bus</label>
            <input
              type="number"
              value={draft.hardware.dfr0971_i2c_bus}
              onChange={e => updateDraft('hardware.dfr0971_i2c_bus', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="dfr0971_i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">I2C Address</label>
            <input
              type="number"
              value={draft.hardware.i2c_address}
              onChange={e => updateDraft('hardware.i2c_address', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="i2c_address"
            />
          </div>
        </div>
        <div className="mt-4 flex gap-6">
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.hardware.active_low}
              onChange={e => updateDraft('hardware.active_low', e.target.checked)}
              className="rounded"
              data-testid="active_low"
            />
            <span className="text-sm text-text-secondary">Active Low</span>
          </label>
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.hardware.require_mcp}
              onChange={e => updateDraft('hardware.require_mcp', e.target.checked)}
              className="rounded"
              data-testid="require_mcp"
            />
            <span className="text-sm text-text-secondary">Require MCP</span>
          </label>
        </div>
        <div className="mt-4">
          <h4 className="mb-2 text-xs font-medium text-text-muted">DFR0971 Boards</h4>
          {draft.hardware.dfr0971_boards.map((board, index) => (
            <div key={board.board_id} className="mb-2 flex gap-2">
              <input
                type="text"
                value={String(board.board_id)}
                readOnly
                className="w-16 rounded border border-border-default bg-surface-tertiary px-2 py-1 text-sm text-text-muted"
              />
              <input
                type="number"
                value={board.i2c_address}
                onChange={e => {
                  const next = structuredClone(draft)
                  next.hardware.dfr0971_boards[index].i2c_address = e.target.value
                  setDraft(next)
                }}
                className="w-24 rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
                data-testid={`board_${board.board_id}_address`}
              />
              <input
                type="text"
                value={board.name}
                onChange={e => {
                  const next = structuredClone(draft)
                  next.hardware.dfr0971_boards[index].name = e.target.value
                  setDraft(next)
                }}
                className="flex-1 rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
                data-testid={`board_${board.board_id}_name`}
              />
            </div>
          ))}
        </div>
      </section>

      {/* Safety Limits Section */}
      <section className="rounded border border-border-subtle bg-surface-primary p-4">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">Safety Limits</h3>
        <div className="grid grid-cols-2 gap-4">
          {[
            { key: 'min_temperature', label: 'Min Temperature' },
            { key: 'max_temperature', label: 'Max Temperature' },
            { key: 'min_humidity', label: 'Min Humidity' },
            { key: 'max_humidity', label: 'Max Humidity' },
            { key: 'min_co2', label: 'Min CO2' },
            { key: 'max_co2', label: 'Max CO2' },
          ].map(({ key, label }) => (
            <div key={key}>
              <label className="mb-1 block text-xs text-text-secondary">{label}</label>
              <input
                type="number"
                value={draft.safety_limits[key as keyof DraftSafetyLimits]}
                onChange={e => updateDraft(`safety_limits.${key}`, e.target.value)}
                className={`w-full rounded border px-2 py-1 text-sm text-text-default ${
                  inlineErrors[key] ? 'border-status-danger bg-status-danger/10' : 'border-border-default bg-surface-secondary'
                }`}
                data-testid={key}
              />
              {inlineErrors[key] && (
                <span className="mt-1 block text-xs text-status-danger">{inlineErrors[key]}</span>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Tuning Section */}
      <section className="rounded border border-border-subtle bg-surface-primary p-4">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">Tuning</h3>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Update Interval (1-5s)</label>
            <input
              type="number"
              min={1}
              max={5}
              value={draft.tuning.update_interval}
              onChange={e => updateDraft('tuning.update_interval', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="update_interval"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Last Good Hold (s)</label>
            <input
              type="number"
              value={draft.tuning.last_good_hold_period}
              onChange={e => updateDraft('tuning.last_good_hold_period', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="last_good_hold_period"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Binary Hysteresis</label>
            <input
              type="number"
              step={0.1}
              value={draft.tuning.binary_hysteresis}
              onChange={e => updateDraft('tuning.binary_hysteresis', e.target.value)}
              className="w-full rounded border border-border-default bg-surface-secondary px-2 py-1 text-sm text-text-default"
              data-testid="binary_hysteresis"
            />
          </div>
        </div>

        {/* PID Limits */}
        <div className="mt-4 space-y-4">
          {(['heater', 'fan', 'co2'] as const).map(deviceType => (
            <div key={deviceType} className="rounded border border-border-subtle/50 bg-surface-secondary/50 p-3">
              <h4 className="mb-2 text-xs font-medium uppercase text-text-muted">{deviceType} PID Limits</h4>
              <div className="grid grid-cols-3 gap-3">
                {(['kp', 'ki', 'kd'] as const).map(param => (
                  <div key={param} className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="mb-1 block text-[10px] text-text-subtle">{param}_min</label>
                      <input
                        type="number"
                        step={0.1}
                        value={draft.pid_limits[deviceType][`${param}_min` as keyof DraftPidLimitsPair]}
                        onChange={e => updateDraft(`pid_limits.${deviceType}.${param}_min`, e.target.value)}
                        className={`w-full rounded border px-1.5 py-1 text-xs text-text-default ${
                          inlineErrors[`pid_${deviceType}_${param}_max`] ? 'border-status-danger' : 'border-border-default'
                        } bg-surface-secondary`}
                        data-testid={`pid_${deviceType}_${param}_min`}
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-[10px] text-text-subtle">{param}_max</label>
                      <input
                        type="number"
                        step={0.1}
                        value={draft.pid_limits[deviceType][`${param}_max` as keyof DraftPidLimitsPair]}
                        onChange={e => updateDraft(`pid_limits.${deviceType}.${param}_max`, e.target.value)}
                        className={`w-full rounded border px-1.5 py-1 text-xs text-text-default ${
                          inlineErrors[`pid_${deviceType}_${param}_max`] ? 'border-status-danger' : 'border-border-default'
                        } bg-surface-secondary`}
                        data-testid={`pid_${deviceType}_${param}_max`}
                      />
                    </div>
                  </div>
                ))}
              </div>
              {inlineErrors[`pid_${deviceType}_kp_max`] && (
                <span className="mt-1 block text-xs text-status-danger">{inlineErrors[`pid_${deviceType}_kp_max`]}</span>
              )}
              {inlineErrors[`pid_${deviceType}_ki_max`] && (
                <span className="mt-1 block text-xs text-status-danger">{inlineErrors[`pid_${deviceType}_ki_max`]}</span>
              )}
              {inlineErrors[`pid_${deviceType}_kd_max`] && (
                <span className="mt-1 block text-xs text-status-danger">{inlineErrors[`pid_${deviceType}_kd_max`]}</span>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Active Low Confirmation Modal */}
      {showActiveLowModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60" data-testid="active-low-modal">
          <div className="w-full max-w-md rounded-lg border border-border-emphasis bg-surface-primary p-6 shadow-xl">
            <h3 className="mb-3 text-lg font-semibold text-status-danger">WARNING</h3>
            <p className="mb-4 text-sm text-text-secondary">
              Changing active_low inverts all 16 relays on next restart. The live driver keeps the OLD value until then — relays will NOT flip immediately. Heaters/lights/fans will toggle state only after the service restarts. Type the current value (&apos;true&apos;/&apos;false&apos;) to confirm.
            </p>
            <input
              type="text"
              value={activeLowConfirmText}
              onChange={e => setActiveLowConfirmText(e.target.value)}
              placeholder={`Type ${String(config.hardware.active_low ?? false)} to confirm`}
              className="mb-4 w-full rounded border border-border-default bg-surface-secondary px-3 py-2 text-sm text-text-default"
              data-testid="active-low-confirm-input"
            />
            <div className="flex justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setShowActiveLowModal(false)
                  setPendingSave(null)
                }}
                className="rounded border border-border-default px-4 py-2 text-sm text-text-secondary hover:bg-surface-secondary"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleConfirmActiveLow}
                disabled={activeLowConfirmText.trim().toLowerCase() !== String(config.hardware.active_low ?? false).toLowerCase()}
                className="rounded bg-status-danger px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed"
                data-testid="active-low-confirm-button"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
