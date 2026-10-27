/** System Settings draft model: config/settings → draft mapping, change detection, validation and save-request building. */
import { isAxiosError } from 'axios'

import type {
  ConfigUpdateRequest,
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

interface DraftHardware {
  i2c_bus: string
  mcp_i2c_bus: string
  dfr0971_i2c_bus: string
  i2c_address: string
  active_low: boolean
  require_mcp: boolean
  dfr0971_boards: Array<{ board_id: number; i2c_address: string }>
}

export interface DraftSafetyLimits {
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

export interface DraftPidLimitsPair {
  kp_min: string
  kp_max: string
  ki_min: string
  ki_max: string
  kd_min: string
  kd_max: string
}

export interface DraftPidLimits {
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

export interface DraftState {
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

export interface SaveRequest {
  config: ConfigUpdateRequest
  settings: SettingsUpdates
}

export function configToDraft(config: SystemConfigResponse, settings: SettingsResponse): DraftState {
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
      return numChanged(board.i2c_address, originalBoard.i2c_address)
    })
  if (boardsChanged) {
    // Board names are edited only in DfrBoardsPanel; the API still requires the stored name.
    hardware.dfr0971_boards = draft.hardware.dfr0971_boards.map(board => ({
      board_id: board.board_id,
      i2c_address: parseFloat(board.i2c_address),
      name: originalBoards.find(b => b.board_id === board.board_id)?.name ?? '',
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

export function buildSaveRequest(
  draft: DraftState,
  config: SystemConfigResponse,
  settings: SettingsResponse
): SaveRequest {
  return {
    config: buildConfigUpdateRequest(draft, config),
    settings: buildSettingsUpdates(draft, settings),
  }
}

export function isDirty(
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

export function validateDraft(draft: DraftState): Record<string, string> {
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

export function apiErrorMessage(
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
