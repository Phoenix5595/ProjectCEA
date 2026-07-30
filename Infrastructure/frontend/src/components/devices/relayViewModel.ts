import type { ControlSnapshotResponse } from '../../services/api/devices'

export const RELAY_CHANNEL_COUNT = 16
export const RELAY_MATRIX_ROWS = 8
export const RELAY_CHANNELS = Array.from({ length: RELAY_CHANNEL_COUNT }, (_, index) => index)

/**
 * Physical relay -> MCP channel bijection. Source: .omo/evidence/task-3-relay-mcp-bugfix.md.
 * Used only by `splitRelayByPhysicalLayout` for physical column placement.
 * All user-facing R labels come from the backend's `physical_relay` field.
 */
export const RELAY_TO_CHANNEL: Readonly<Record<number, number>> = {
  1: 15, 2: 0, 3: 14, 4: 1, 5: 13, 6: 2, 7: 12, 8: 3,
  9: 11, 10: 4, 11: 10, 12: 5, 13: 9, 14: 6, 15: 8, 16: 7,
}

export interface RelayAlarmView {
  severity: string
  message: string
}

export interface RelayChannelViewModel {
  channel: number
  physicalRelay: number
  pinLabel: string
  isStateKnown: boolean
  observedState: boolean | null
  isActive: boolean
  isAssigned: boolean
  assignedDeviceName: string | null
  deviceName: string | null
  displayType: string | null
  location: string | null
  cluster: string | null
  changedAt: string | null
  commandMode: string | null
  commandExpiresAt: string | null
  syncing: boolean
  stale: boolean
  alarm: RelayAlarmView | null
  lastCommandSucceeded: boolean | null
  recoveryPending: boolean
}

export function getReadableDeviceType(deviceType: string): string {
  if (deviceType === 'co2' || deviceType === 'co2_tank') {
    return 'CO2 Tank'
  }
  const normalized = deviceType.replace(/_/g, ' ')
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

/**
 * Build a 16-slot relay view model array directly from the composite snapshot.
 * `snapshot.relays` is already ordered by physical relay (R1..R16); we preserve
 * that order. All display labels come from the backend — the frontend never
 * computes R labels from `channel + 1`.
 */
export function buildRelayChannelViewModels(
  snapshot: ControlSnapshotResponse | null,
): RelayChannelViewModel[] {
  if (!snapshot) {
    return RELAY_CHANNELS.map((channel) => emptyViewModel(channel))
  }

  const byChannel = new Map<number, ControlSnapshotResponse['relays'][number]>()
  for (const r of snapshot.relays) {
    byChannel.set(r.channel, r)
  }

  return RELAY_CHANNELS.map((channel) => {
    const r = byChannel.get(channel)
    if (!r) return emptyViewModel(channel)

    const assignment = r.assignment
    const observedState = r.observed_state
    return {
      channel,
      physicalRelay: r.physical_relay,
      pinLabel: r.pin_label,
      isStateKnown: observedState !== null,
      observedState,
      isActive: observedState === true,
      isAssigned: assignment !== null,
      assignedDeviceName: assignment?.device_name ?? null,
      deviceName: pickDisplayName(assignment),
      displayType: assignment?.device_type ? getReadableDeviceType(assignment.device_type) : null,
      location: assignment?.location ?? null,
      cluster: assignment?.cluster ?? null,
      changedAt: r.changed_at,
      commandMode: r.command_mode,
      commandExpiresAt: r.command_expires_at,
      syncing: r.syncing,
      stale: r.stale,
      alarm: r.alarm ? { severity: r.alarm.severity, message: r.alarm.message } : null,
      lastCommandSucceeded: r.last_command_succeeded,
      recoveryPending: r.recovery_pending,
    }
  })
}

function emptyViewModel(channel: number): RelayChannelViewModel {
  return {
    channel,
    physicalRelay: channel + 1,
    pinLabel: '',
    isStateKnown: false,
    observedState: null,
    isActive: false,
    isAssigned: false,
    assignedDeviceName: null,
    deviceName: null,
    displayType: null,
    location: null,
    cluster: null,
    changedAt: null,
    commandMode: null,
    commandExpiresAt: null,
    syncing: false,
    stale: false,
    alarm: null,
    lastCommandSucceeded: null,
    recoveryPending: false,
  }
}

function pickDisplayName(
  assignment: ControlSnapshotResponse['relays'][number]['assignment'],
): string | null {
  if (!assignment) return null
  return assignment.display_name || assignment.device_name
}

/**
 * Split a 16-channel view-model array into two physical layout columns.
 * - leftColumn[0..7]  = physical relays 1..8 (top to bottom)
 * - rightColumn[0..7] = physical relays 16..9 (top to bottom)
 */
export function splitRelayByPhysicalLayout(
  channels: RelayChannelViewModel[]
): { leftColumn: RelayChannelViewModel[]; rightColumn: RelayChannelViewModel[] } {
  const byPhysical = new Map<number, RelayChannelViewModel>(
    channels.map((vm) => [vm.physicalRelay, vm])
  )

  const leftColumn = [1, 2, 3, 4, 5, 6, 7, 8].map(
    (relay) => byPhysical.get(relay) ?? emptyViewModel(RELAY_TO_CHANNEL[relay])
  )
  const rightColumn = [16, 15, 14, 13, 12, 11, 10, 9].map(
    (relay) => byPhysical.get(relay) ?? emptyViewModel(RELAY_TO_CHANNEL[relay])
  )

  return { leftColumn, rightColumn }
}

export function formatElapsedSince(
  timestamp: string | null,
  nowMs: number
): string {
  if (!timestamp) {
    return 'Unknown'
  }

  const parsed = Date.parse(timestamp)
  if (Number.isNaN(parsed)) {
    return 'Unknown'
  }

  const elapsedMs = Math.max(0, nowMs - parsed)
  const elapsedSeconds = Math.floor(elapsedMs / 1000)

  const days = Math.floor(elapsedSeconds / 86400)
  const hours = Math.floor((elapsedSeconds % 86400) / 3600)
  const minutes = Math.floor((elapsedSeconds % 3600) / 60)
  const seconds = elapsedSeconds % 60

  if (days > 0) {
    return `${days}d ${hours}h ${minutes}m`
  }

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`
  }

  if (minutes > 0) {
    return `${minutes}m ${seconds}s`
  }

  return `${seconds}s`
}

export function formatCountdown(expiresAt: string | null, nowMs: number): string {
  if (!expiresAt) return ''
  const expires = Date.parse(expiresAt)
  if (Number.isNaN(expires)) return ''
  const remainingMs = expires - nowMs
  if (remainingMs <= 0) return ''
  const totalSeconds = Math.floor(remainingMs / 1000)
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

