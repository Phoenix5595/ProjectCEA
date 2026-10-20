import { isAxiosError } from 'axios'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

import { apiClient } from '../../services/api'
import type {
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  DfrBoardConfig,
  HardwareConfig,
  PidLimitsGroup,
  PidLimitsPair,
  SafetyLimitsConfig,
  SettingsResponse,
  SettingsServiceName,
  SettingsUpdateRequest,
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

interface DraftSoilSettings {
  poll_interval_seconds: string
  discovery_interval_seconds: string
}

interface DraftOnewireSettings {
  poll_interval_seconds: string
}

interface DraftWeatherSettings {
  station_icao: string
  poll_interval_seconds: string
  api_url: string
  room_name: string
  device_name: string
}

interface DraftState {
  hardware: DraftHardware
  safety_limits: DraftSafetyLimits
  tuning: DraftTuning
  pid_limits: DraftPidLimits
  soil: DraftSoilSettings
  onewire: DraftOnewireSettings
  weather: DraftWeatherSettings
}

type SettingsUpdates = {
  [Service in SettingsServiceName]: SettingsUpdateRequest<Service>
}

interface SaveRequest {
  config: ConfigUpdateRequest
  settings: SettingsUpdates
}

function configToDraft(config: SystemConfigResponse, settings: SettingsResponse): DraftState {
  const automation = settings.services.automation.settings
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
  const safety = automation.safety_limits.value
  const soil = settings.services.soil.settings
  const onewire = settings.services.onewire.settings
  const weather = settings.services.weather.settings

  return {
    hardware: {
      i2c_bus: String(config.hardware.i2c_bus ?? ''),
      mcp_i2c_bus: String(config.hardware.mcp_i2c_bus ?? ''),
      dfr0971_i2c_bus: String(config.hardware.dfr0971_i2c_bus ?? ''),
      i2c_address: String(config.hardware.i2c_address ?? ''),
      active_low: config.hardware.active_low ?? false,
      require_mcp: config.hardware.require_mcp ?? false,
      dfr0971_boards: (config.hardware.dfr0971_boards ?? []).map((board: DfrBoardConfig) => ({
        board_id: board.board_id,
        i2c_address: String(board.i2c_address),
        name: board.name,
      })),
    },
    safety_limits: {
      min_temperature: String(safety.min_temperature ?? ''),
      max_temperature: String(safety.max_temperature ?? ''),
      min_humidity: String(safety.min_humidity ?? ''),
      max_humidity: String(safety.max_humidity ?? ''),
      min_co2: String(safety.min_co2 ?? ''),
      max_co2: String(safety.max_co2 ?? ''),
    },
    tuning: {
      update_interval: String(automation.update_interval.value),
      last_good_hold_period: String(automation.last_good_hold_period.value),
      binary_hysteresis: String(automation.binary_hysteresis.value),
    },
    pid_limits: {
      heater: pidToDraft(automation.pid_limits.value.heater),
      fan: pidToDraft(automation.pid_limits.value.fan),
      co2: pidToDraft(automation.pid_limits.value.co2),
    },
    soil: {
      poll_interval_seconds: String(soil.poll_interval_seconds.value),
      discovery_interval_seconds: String(soil.discovery_interval_seconds.value),
    },
    onewire: {
      poll_interval_seconds: String(onewire.poll_interval_seconds.value),
    },
    weather: {
      station_icao: weather.station_icao.value,
      poll_interval_seconds: String(weather.poll_interval_seconds.value),
      api_url: weather.api_url.value,
      room_name: weather.room_name.value,
      device_name: weather.device_name.value,
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


function buildConfigUpdateRequest(
  draft: DraftState,
  original: SystemConfigResponse
): ConfigUpdateRequest {
  const hardware: HardwareConfig = {}
  if (numChanged(draft.hardware.i2c_bus, original.hardware.i2c_bus)) {
    hardware.i2c_bus = draft.hardware.i2c_bus.trim() === '' ? null : parseFloat(draft.hardware.i2c_bus)
  }
  if (numChanged(draft.hardware.mcp_i2c_bus, original.hardware.mcp_i2c_bus)) {
    hardware.mcp_i2c_bus =
      draft.hardware.mcp_i2c_bus.trim() === '' ? null : parseFloat(draft.hardware.mcp_i2c_bus)
  }
  if (numChanged(draft.hardware.dfr0971_i2c_bus, original.hardware.dfr0971_i2c_bus)) {
    hardware.dfr0971_i2c_bus =
      draft.hardware.dfr0971_i2c_bus.trim() === ''
        ? null
        : parseFloat(draft.hardware.dfr0971_i2c_bus)
  }
  if (numChanged(draft.hardware.i2c_address, original.hardware.i2c_address)) {
    hardware.i2c_address =
      draft.hardware.i2c_address.trim() === '' ? null : parseFloat(draft.hardware.i2c_address)
  }
  if (draft.hardware.active_low !== (original.hardware.active_low ?? false)) {
    hardware.active_low = draft.hardware.active_low
  }
  if (draft.hardware.require_mcp !== (original.hardware.require_mcp ?? false)) {
    hardware.require_mcp = draft.hardware.require_mcp
  }
  const originalBoards = original.hardware.dfr0971_boards ?? []
  const boardsChanged =
    draft.hardware.dfr0971_boards.length !== originalBoards.length ||
    draft.hardware.dfr0971_boards.some((board, index) => {
      const originalBoard = originalBoards[index]
      if (!originalBoard) return true
      return (
        numChanged(board.i2c_address, originalBoard.i2c_address) ||
        board.name !== originalBoard.name
      )
    })
  if (boardsChanged) {
    hardware.dfr0971_boards = draft.hardware.dfr0971_boards.map(board => ({
      board_id: board.board_id,
      i2c_address: parseFloat(board.i2c_address),
      name: board.name,
    }))
  }

  return Object.keys(hardware).length > 0 ? { hardware } : {}
}

function buildSettingsUpdates(draft: DraftState, original: SettingsResponse): SettingsUpdates {
  const automationSettings = original.services.automation.settings
  const automation: SettingsUpdateRequest<'automation'> = {}
  if (numChanged(draft.tuning.update_interval, automationSettings.update_interval.value)) {
    automation.update_interval = parseFloat(draft.tuning.update_interval)
  }
  if (numChanged(draft.tuning.last_good_hold_period, automationSettings.last_good_hold_period.value)) {
    automation.last_good_hold_period = parseFloat(draft.tuning.last_good_hold_period)
  }
  if (numChanged(draft.tuning.binary_hysteresis, automationSettings.binary_hysteresis.value)) {
    automation.binary_hysteresis = parseFloat(draft.tuning.binary_hysteresis)
  }

  const safetyKeys = [
    'min_temperature',
    'max_temperature',
    'min_humidity',
    'max_humidity',
    'min_co2',
    'max_co2',
  ] as const
  const originalSafety = automationSettings.safety_limits.value
  if (safetyKeys.some(key => numChanged(draft.safety_limits[key], originalSafety[key]))) {
    const safetyLimits: SafetyLimitsConfig = {}
    for (const key of safetyKeys) {
      const value = draft.safety_limits[key]
      safetyLimits[key] = value.trim() === '' ? null : parseFloat(value)
    }
    automation.safety_limits = safetyLimits
  }

  const pidLimits: PidLimitsGroup = { ...automationSettings.pid_limits.value }
  let pidChanged = false
  for (const type of ['heater', 'fan', 'co2'] as const) {
    const draftPair = draft.pid_limits[type]
    const originalPair = automationSettings.pid_limits.value[type]
    const pairChanged =
      numChanged(draftPair.kp_min, originalPair?.kp_min) ||
      numChanged(draftPair.kp_max, originalPair?.kp_max) ||
      numChanged(draftPair.ki_min, originalPair?.ki_min) ||
      numChanged(draftPair.ki_max, originalPair?.ki_max) ||
      numChanged(draftPair.kd_min, originalPair?.kd_min) ||
      numChanged(draftPair.kd_max, originalPair?.kd_max)
    if (pairChanged) {
      pidChanged = true
      pidLimits[type] = {
        kp_min: parseFloat(draftPair.kp_min),
        kp_max: parseFloat(draftPair.kp_max),
        ki_min: parseFloat(draftPair.ki_min),
        ki_max: parseFloat(draftPair.ki_max),
        kd_min: parseFloat(draftPair.kd_min),
        kd_max: parseFloat(draftPair.kd_max),
      }
    }
  }
  if (pidChanged) automation.pid_limits = pidLimits

  const soilSettings = original.services.soil.settings
  const soil: SettingsUpdateRequest<'soil'> = {}
  if (numChanged(draft.soil.poll_interval_seconds, soilSettings.poll_interval_seconds.value)) {
    soil.poll_interval_seconds = parseFloat(draft.soil.poll_interval_seconds)
  }
  if (
    numChanged(
      draft.soil.discovery_interval_seconds,
      soilSettings.discovery_interval_seconds.value
    )
  ) {
    soil.discovery_interval_seconds = parseFloat(draft.soil.discovery_interval_seconds)
  }

  const onewireSettings = original.services.onewire.settings
  const onewire: SettingsUpdateRequest<'onewire'> = {}
  if (
    numChanged(draft.onewire.poll_interval_seconds, onewireSettings.poll_interval_seconds.value)
  ) {
    onewire.poll_interval_seconds = parseFloat(draft.onewire.poll_interval_seconds)
  }

  const weatherSettings = original.services.weather.settings
  const weather: SettingsUpdateRequest<'weather'> = {}
  if (draft.weather.station_icao !== weatherSettings.station_icao.value) {
    weather.station_icao = draft.weather.station_icao
  }
  if (numChanged(draft.weather.poll_interval_seconds, weatherSettings.poll_interval_seconds.value)) {
    weather.poll_interval_seconds = parseFloat(draft.weather.poll_interval_seconds)
  }
  if (draft.weather.api_url !== weatherSettings.api_url.value) {
    weather.api_url = draft.weather.api_url
  }

  return { automation, soil, onewire, weather }
}

function buildSaveRequest(
  draft: DraftState,
  config: SystemConfigResponse,
  settings: SettingsResponse
): SaveRequest {
  return {
    config: buildConfigUpdateRequest(draft, config),
    settings: buildSettingsUpdates(draft, settings),
  }
}

function isDirty(
  draft: DraftState,
  config: SystemConfigResponse,
  settings: SettingsResponse
): boolean {
  const update = buildSaveRequest(draft, config, settings)
  return (
    Object.keys(update.config.hardware ?? {}).length > 0 ||
    Object.values(update.settings).some(serviceUpdate => Object.keys(serviceUpdate).length > 0)
  )
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

  for (const type of ['heater', 'fan', 'co2'] as const) {
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

function apiErrorMessage(
  error: unknown,
  fallback: string
): string {
  if (
    isAxiosError<{
      detail?: string | { code?: string; key?: string; message?: string; reason?: string }
    }>(error)
  ) {
    const detail = error.response?.data?.detail
    if (typeof detail === 'string' && detail.trim() !== '') return detail
    if (detail && typeof detail === 'object') {
      const description = detail.reason ?? detail.message ?? detail.code
      if (description) return detail.key ? `${detail.key}: ${description}` : description
    }
    if (error.message) return error.message
  }
  return fallback
}

export default function SystemSettingsPanel() {
  const [config, setConfig] = useState<SystemConfigResponse | null>(null)
  const [settings, setSettings] = useState<SettingsResponse | null>(null)
  const [draft, setDraft] = useState<DraftState | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [pendingChanges, setPendingChanges] = useState<string[]>([])
  const [restarting, setRestarting] = useState(false)
  const [showActiveLowModal, setShowActiveLowModal] = useState(false)
  const [activeLowConfirmText, setActiveLowConfirmText] = useState('')
  const [inlineErrors, setInlineErrors] = useState<Record<string, string>>({})
  const [pendingSave, setPendingSave] = useState<SaveRequest | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const restartPollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const loadConfig = useCallback(async () => {
    try {
      const [cfg, currentSettings] = await Promise.all([
        apiClient.getConfig(),
        apiClient.getSettings(),
      ])
      setConfig(cfg)
      setSettings(currentSettings)
      setDraft(previous => previous ?? configToDraft(cfg, currentSettings))
      setPendingChanges(cfg.pending_restart_required_changes)
      setLoadError(null)
    } catch (err) {
      logger.error('Failed to load system settings', err)
      setLoadError(apiErrorMessage(err, 'Failed to load system settings'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadConfig()
    pollRef.current = setInterval(loadConfig, 15000)
    return () => {
      clearInterval(pollRef.current)
      clearInterval(restartPollRef.current)
    }
  }, [loadConfig])

  const handleSave = async () => {
    if (!config || !settings || !draft) return

    const validationErrors = validateDraft(draft)
    if (Object.keys(validationErrors).length > 0) {
      setInlineErrors(validationErrors)
      toast.error('Please fix validation errors before saving')
      return
    }

    const update = buildSaveRequest(draft, config, settings)
    const hasHardwareChanges = Object.keys(update.config.hardware ?? {}).length > 0
    const hasSettingsChanges = Object.values(update.settings).some(
      serviceUpdate => Object.keys(serviceUpdate).length > 0
    )
    if (!hasHardwareChanges && !hasSettingsChanges) {
      toast.info('No changes to save')
      return
    }

    if (
      update.config.hardware?.active_low !== undefined &&
      draft.hardware.active_low !== (config.hardware.active_low ?? false)
    ) {
      setPendingSave(update)
      setShowActiveLowModal(true)
      setActiveLowConfirmText('')
      return
    }

    await executeSave(update)
  }

  const executeSave = async (update: SaveRequest) => {
    if (!config || !settings || !draft) return
    setSaving(true)
    setInlineErrors({})

    let nextConfig = config
    let nextSettings = settings
    try {
      if (Object.keys(update.config.hardware ?? {}).length > 0) {
        const response: ConfigUpdateResponse = await apiClient.putConfig(update.config)
        nextConfig = {
          ...config,
          hardware: { ...config.hardware, ...(update.config.hardware ?? {}) },
          pending_restart_required_changes: response.pending_restart_required_changes,
          restart_hashes: response.restart_hashes,
        }
        setConfig(nextConfig)
        setPendingChanges(response.pending_restart_required_changes)
        const syncedDraft = configToDraft(nextConfig, nextSettings)
        setDraft(previous => (previous ? { ...previous, hardware: syncedDraft.hardware } : previous))
      }

      if (Object.keys(update.settings.automation).length > 0) {
        nextSettings = {
          ...nextSettings,
          services: {
            ...nextSettings.services,
            automation: await apiClient.putSettings('automation', update.settings.automation),
          },
        }
        setSettings(nextSettings)
        const syncedDraft = configToDraft(nextConfig, nextSettings)
        setDraft(previous =>
          previous
            ? {
                ...previous,
                safety_limits: syncedDraft.safety_limits,
                tuning: syncedDraft.tuning,
                pid_limits: syncedDraft.pid_limits,
              }
            : previous
        )
      }

      if (Object.keys(update.settings.soil).length > 0) {
        nextSettings = {
          ...nextSettings,
          services: {
            ...nextSettings.services,
            soil: await apiClient.putSettings('soil', update.settings.soil),
          },
        }
        setSettings(nextSettings)
        const syncedDraft = configToDraft(nextConfig, nextSettings)
        setDraft(previous => (previous ? { ...previous, soil: syncedDraft.soil } : previous))
      }

      if (Object.keys(update.settings.onewire).length > 0) {
        nextSettings = {
          ...nextSettings,
          services: {
            ...nextSettings.services,
            onewire: await apiClient.putSettings('onewire', update.settings.onewire),
          },
        }
        setSettings(nextSettings)
        const syncedDraft = configToDraft(nextConfig, nextSettings)
        setDraft(previous => (previous ? { ...previous, onewire: syncedDraft.onewire } : previous))
      }

      if (Object.keys(update.settings.weather).length > 0) {
        nextSettings = {
          ...nextSettings,
          services: {
            ...nextSettings.services,
            weather: await apiClient.putSettings('weather', update.settings.weather),
          },
        }
        setSettings(nextSettings)
        const syncedDraft = configToDraft(nextConfig, nextSettings)
        setDraft(previous => (previous ? { ...previous, weather: syncedDraft.weather } : previous))
      }

      setConfig(nextConfig)
      setSettings(nextSettings)
      setDraft(configToDraft(nextConfig, nextSettings))
      setLoadError(null)
      toast.success('Saved.')
    } catch (err: unknown) {
      logger.error('Failed to save system settings', err)
      const message = apiErrorMessage(err, 'Failed to save system settings')
      setInlineErrors({ general: message })
      toast.error(message)
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
              restartPollRef.current = undefined
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
      let target: Record<string, unknown> = next as unknown as Record<string, unknown>
      for (let i = 0; i < parts.length - 1; i++) {
        const key = parts[i]
        if (key === undefined) return prev
        const child = target[key]
        if (typeof child !== 'object' || child === null) return prev
        target = child as Record<string, unknown>
      }
      const key = parts[parts.length - 1]
      if (key === undefined) return prev
      target[key] = value
      return next
    })
    setInlineErrors(prev => {
      const next = { ...prev }
      delete next[path]
      delete next.general
      return next
    })
  }

  if (loading) {
    return <div className="p-4 text-text-muted">Loading settings...</div>
  }

  if (!config || !settings || !draft) {
    return (
      <div className="p-4 text-status-danger" role="alert">
        {loadError ?? 'Failed to load settings'}
      </div>
    )
  }

  const dirty = isDirty(draft, config, settings)

  return (
    <div className="space-y-6 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-text-default">System Settings</h2>
        <button
          type="button"
          onClick={handleSave}
          disabled={!dirty || saving}
          className="rounded bg-accent-vivid px-4 py-2 text-sm font-medium text-accent-vivid-foreground hover:bg-accent-data disabled:opacity-50 disabled:cursor-not-allowed"
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
      {loadError && (
        <div
          className="rounded border border-status-danger/50 bg-status-danger/10 p-3 text-sm text-status-danger"
          role="alert"
        >
          {loadError}
        </div>
      )}

      {/* Hardware Section */}
      <section className="rounded border border-border-subtle bg-surface-primary p-4">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Hardware
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-xs text-text-secondary">I2C Bus</label>
            <Input
              type="number"
              value={draft.hardware.i2c_bus}
              onChange={e => updateDraft('hardware.i2c_bus', e.target.value)}
              data-testid="i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">MCP I2C Bus</label>
            <Input
              type="number"
              value={draft.hardware.mcp_i2c_bus}
              onChange={e => updateDraft('hardware.mcp_i2c_bus', e.target.value)}
              data-testid="mcp_i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">DFR I2C Bus</label>
            <Input
              type="number"
              value={draft.hardware.dfr0971_i2c_bus}
              onChange={e => updateDraft('hardware.dfr0971_i2c_bus', e.target.value)}
              data-testid="dfr0971_i2c_bus"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">I2C Address</label>
            <Input
              type="number"
              value={draft.hardware.i2c_address}
              onChange={e => updateDraft('hardware.i2c_address', e.target.value)}
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
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Safety Limits
        </h3>
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
                  inlineErrors[key]
                    ? 'border-status-danger bg-status-danger/10'
                    : 'border-border-default bg-surface-secondary'
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
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Tuning
        </h3>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Update Interval (1-5s)</label>
            <Input
              type="number"
              min={1}
              max={5}
              value={draft.tuning.update_interval}
              onChange={e => updateDraft('tuning.update_interval', e.target.value)}
              data-testid="update_interval"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Last Good Hold (s)</label>
            <Input
              type="number"
              value={draft.tuning.last_good_hold_period}
              onChange={e => updateDraft('tuning.last_good_hold_period', e.target.value)}
              data-testid="last_good_hold_period"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-secondary">Binary Hysteresis</label>
            <Input
              type="number"
              step={0.1}
              value={draft.tuning.binary_hysteresis}
              onChange={e => updateDraft('tuning.binary_hysteresis', e.target.value)}
              data-testid="binary_hysteresis"
            />
          </div>
        </div>

        {/* PID Limits */}
        <div className="mt-4 space-y-4">
          {(['heater', 'fan', 'co2'] as const).map(deviceType => (
            <div
              key={deviceType}
              className="rounded border border-border-subtle/50 bg-surface-secondary/50 p-3"
            >
              <h4 className="mb-2 text-xs font-medium uppercase text-text-muted">
                {deviceType} PID Limits
              </h4>
              <div className="grid grid-cols-3 gap-3">
                {(['kp', 'ki', 'kd'] as const).map(param => (
                  <div key={param} className="grid grid-cols-2 gap-2">
                    <div>
                      <label className="mb-1 block text-10 text-text-subtle">{param}_min</label>
                      <input
                        type="number"
                        step={0.1}
                        value={
                          draft.pid_limits[deviceType][`${param}_min` as keyof DraftPidLimitsPair]
                        }
                        onChange={e =>
                          updateDraft(`pid_limits.${deviceType}.${param}_min`, e.target.value)
                        }
                        className={`w-full rounded border px-1.5 py-1 text-xs text-text-default ${
                          inlineErrors[`pid_${deviceType}_${param}_max`]
                            ? 'border-status-danger'
                            : 'border-border-default'
                        } bg-surface-secondary`}
                        data-testid={`pid_${deviceType}_${param}_min`}
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-10 text-text-subtle">{param}_max</label>
                      <input
                        type="number"
                        step={0.1}
                        value={
                          draft.pid_limits[deviceType][`${param}_max` as keyof DraftPidLimitsPair]
                        }
                        onChange={e =>
                          updateDraft(`pid_limits.${deviceType}.${param}_max`, e.target.value)
                        }
                        className={`w-full rounded border px-1.5 py-1 text-xs text-text-default ${
                          inlineErrors[`pid_${deviceType}_${param}_max`]
                            ? 'border-status-danger'
                            : 'border-border-default'
                        } bg-surface-secondary`}
                        data-testid={`pid_${deviceType}_${param}_max`}
                      />
                    </div>
                  </div>
                ))}
              </div>
              {inlineErrors[`pid_${deviceType}_kp_max`] && (
                <span className="mt-1 block text-xs text-status-danger">
                  {inlineErrors[`pid_${deviceType}_kp_max`]}
                </span>
              )}
              {inlineErrors[`pid_${deviceType}_ki_max`] && (
                <span className="mt-1 block text-xs text-status-danger">
                  {inlineErrors[`pid_${deviceType}_ki_max`]}
                </span>
              )}
              {inlineErrors[`pid_${deviceType}_kd_max`] && (
                <span className="mt-1 block text-xs text-status-danger">
                  {inlineErrors[`pid_${deviceType}_kd_max`]}
                </span>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* Services Section */}
      <section className="rounded border border-border-subtle bg-surface-primary p-4">
        <h3 className="mb-4 text-sm font-semibold uppercase tracking-wide text-text-muted">
          Services
        </h3>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-text-muted">Soil</h4>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="mb-1 block text-xs text-text-secondary">
                  Soil Polling Interval (s)
                </label>
                <Input
                  type="number"
                  min={settings.services.soil.schema.poll_interval_seconds.minimum ?? undefined}
                  max={settings.services.soil.schema.poll_interval_seconds.maximum ?? undefined}
                  value={draft.soil.poll_interval_seconds}
                  onChange={e => updateDraft('soil.poll_interval_seconds', e.target.value)}
                  data-testid="soil_poll_interval_seconds"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-text-secondary">
                  Soil Discovery Interval (s)
                </label>
                <Input
                  type="number"
                  min={settings.services.soil.schema.discovery_interval_seconds.minimum ?? undefined}
                  max={settings.services.soil.schema.discovery_interval_seconds.maximum ?? undefined}
                  value={draft.soil.discovery_interval_seconds}
                  onChange={e => updateDraft('soil.discovery_interval_seconds', e.target.value)}
                  data-testid="soil_discovery_interval_seconds"
                />
              </div>
            </div>
          </div>

          <div>
            <h4 className="mb-2 text-xs font-medium uppercase text-text-muted">1-Wire</h4>
            <label className="mb-1 block text-xs text-text-secondary">
              1-Wire Polling Interval (s)
            </label>
            <Input
              type="number"
              min={settings.services.onewire.schema.poll_interval_seconds.minimum ?? undefined}
              max={settings.services.onewire.schema.poll_interval_seconds.maximum ?? undefined}
              value={draft.onewire.poll_interval_seconds}
              onChange={e => updateDraft('onewire.poll_interval_seconds', e.target.value)}
              data-testid="onewire_poll_interval_seconds"
            />
          </div>

          <div className="col-span-2">
            <h4 className="mb-2 text-xs font-medium uppercase text-text-muted">Weather Station</h4>
            <div className="grid grid-cols-3 gap-4">
              <div>
                <label className="mb-1 block text-xs text-text-secondary">Station ICAO</label>
                <Input
                  type="text"
                  pattern={settings.services.weather.schema.station_icao.pattern ?? undefined}
                  value={draft.weather.station_icao}
                  onChange={e => updateDraft('weather.station_icao', e.target.value)}
                  data-testid="weather_station_icao"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-text-secondary">
                  Weather Poll Interval (s)
                </label>
                <Input
                  type="number"
                  min={settings.services.weather.schema.poll_interval_seconds.minimum ?? undefined}
                  max={settings.services.weather.schema.poll_interval_seconds.maximum ?? undefined}
                  value={draft.weather.poll_interval_seconds}
                  onChange={e => updateDraft('weather.poll_interval_seconds', e.target.value)}
                  data-testid="weather_poll_interval_seconds"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-text-secondary">Weather API URL</label>
                <Input
                  type="url"
                  value={draft.weather.api_url}
                  onChange={e => updateDraft('weather.api_url', e.target.value)}
                  data-testid="weather_api_url"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-text-secondary">Weather Room</label>
                <Input
                  type="text"
                  value={draft.weather.room_name}
                  readOnly
                  data-testid="weather_room_name"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-text-secondary">Weather Device</label>
                <Input
                  type="text"
                  value={draft.weather.device_name}
                  readOnly
                  data-testid="weather_device_name"
                />
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Active Low Confirmation Modal */}
      <Dialog
        open={showActiveLowModal}
        onOpenChange={o => {
          if (!o) {
            setShowActiveLowModal(false)
            setPendingSave(null)
          }
        }}
      >
        <DialogContent
          className="w-full max-w-md rounded-lg border-border-emphasis bg-surface-primary p-6 shadow-xl"
          data-testid="active-low-modal"
        >
          <DialogTitle className="normal-case tracking-normal mb-3 text-lg font-semibold text-status-danger">
            WARNING
          </DialogTitle>
          <DialogDescription className="mb-4 text-sm text-text-secondary">
            Changing active_low inverts all 16 relays on next restart. The live driver keeps the OLD
            value until then — relays will NOT flip immediately. Heaters/lights/fans will toggle
            state only after the service restarts. Type the current value
            (&apos;true&apos;/&apos;false&apos;) to confirm.
          </DialogDescription>
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
              disabled={
                activeLowConfirmText.trim().toLowerCase() !==
                String(config.hardware.active_low ?? false).toLowerCase()
              }
              className="rounded bg-status-danger px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50 disabled:cursor-not-allowed"
              data-testid="active-low-confirm-button"
            >
              Confirm
            </button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
