import type { Device } from '../../src/types/device'
import type { ControlSnapshotResponse } from '../../src/services/api/devices'
import { RELAY_TO_CHANNEL } from '../../src/components/devices/relayViewModel'

// Identities, names and bindings mirror the current read-only device registry.
// Percentages are deterministic QA scenarios, not a claim about live output.
export const DASHBOARD_DEVICES = [
  { device_id: 2, location: 'Flower Room', cluster: 'main', device_name: 'light_f_1', display_name: 'Chilled Front', device_type: 'light', state: 0, mode: 'auto', channel: 3, board_id: 2, dimming_channel: 0, per_room_index: 1 },
  { device_id: 3, location: 'Flower Room', cluster: 'main', device_name: 'light_f_2', display_name: 'Apache', device_type: 'light', state: 0, mode: 'auto', channel: 2, board_id: 1, dimming_channel: 1, per_room_index: 2 },
  { device_id: 4, location: 'Flower Room', cluster: 'main', device_name: 'light_f_3', display_name: 'Chilled Back', device_type: 'light', state: 0, mode: 'auto', channel: 12, board_id: 2, dimming_channel: 1, per_room_index: 3 },
  { device_id: 8, location: 'Veg Room', cluster: 'main', device_name: 'light_v_1', display_name: 'Eyefinity Top', device_type: 'light', state: 1, mode: 'auto', channel: 10, board_id: 0, dimming_channel: 0, per_room_index: 1 },
  { device_id: 9, location: 'Veg Room', cluster: 'main', device_name: 'light_v_2', display_name: 'Ridgetop Bottom Right', device_type: 'light', state: 1, mode: 'auto', channel: 4, board_id: 0, dimming_channel: 1, per_room_index: 2 },
  { device_id: 10, location: 'Veg Room', cluster: 'main', device_name: 'light_v_3', display_name: 'Ridgetop Bottom Left', device_type: 'light', state: 1, mode: 'auto', channel: 11, board_id: 1, dimming_channel: 0, per_room_index: 3 },
] satisfies Array<Device & {
  device_id: number
  display_name: string
  device_type: string
  board_id: number
  dimming_channel: number
  per_room_index: number
}>

export const DASHBOARD_LIGHT_INTENSITIES: Record<string, number> = {
  'Flower Room_main_light_f_1_intensity': 0,
  'Flower Room_main_light_f_2_intensity': 0,
  'Flower Room_main_light_f_3_intensity': 0,
  'Veg Room_main_light_v_1_intensity': 80,
  'Veg Room_main_light_v_2_intensity': 40,
  'Veg Room_main_light_v_3_intensity': 40,
}

export function dashboardDeviceDetails(location: string, cluster: string) {
  return Object.fromEntries(
    DASHBOARD_DEVICES.filter(device => device.location === location && device.cluster === cluster)
      .map(device => [device.device_name, {
        ...device, dimming_enabled: true,
        dimming_board_id: String(device.board_id),
      }])
  )
}

export function dashboardControlSnapshotFixture(): ControlSnapshotResponse {
  const sampledAt = new Date().toISOString()
  const assignment = (device: (typeof DASHBOARD_DEVICES)[number] | undefined) => device ? {
    device_id: device.device_id,
    device_name: device.device_name,
    device_type: device.device_type,
    display_name: device.display_name,
    location: device.location,
    cluster: device.cluster,
    inherited_schedule_count: 0,
    inherited_schedule_summary: null,
  } : null
  return {
    generated_at: sampledAt,
    sampled_at: sampledAt,
    freshness: 'FRESH',
    registry_version: 1,
    stale_since: null,
    hardware_alarms: [],
    failsafes: [],
    relays: Object.entries(RELAY_TO_CHANNEL).map(([physicalRelay, channel]) => {
      const device = DASHBOARD_DEVICES.find(candidate => candidate.channel === channel)
      return {
        physical_relay: Number(physicalRelay), channel,
        pin_label: `${channel < 8 ? 'GPA' : 'GPB'}${channel % 8}`,
        assignment: assignment(device),
        observed_state: device ? device.state === 1 : null,
        desired_state: device ? device.state : null,
        changed_at: sampledAt,
        command_mode: device ? 'AUTO' : null,
        command_expires_at: null,
        prior_command_mode: null,
        syncing: false,
        stale: false,
        last_command_succeeded: device ? true : null,
        recovery_pending: false,
        interlock_blocked: false,
        interlock_reason: null,
        alarm: null,
      }
    }),
    dfr_boards: [0, 1, 2].map(boardId => ({
      board_id: boardId,
      available: true,
      channels: [0, 1].map(channel => {
        const device = DASHBOARD_DEVICES.find(candidate =>
          candidate.board_id === boardId && candidate.dimming_channel === channel)
        return {
          channel, available: true, assignment: assignment(device),
          commanded_intensity: device
            ? DASHBOARD_LIGHT_INTENSITIES[`${device.location}_${device.cluster}_${device.device_name}_intensity`]
            : null,
          command_acknowledged: device != null,
        }
      }),
    })),
  }
}
