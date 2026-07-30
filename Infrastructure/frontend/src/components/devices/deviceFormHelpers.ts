import type { components } from '../../generated/api';
import type { ControlSnapshotResponse } from '../../services/api/devices';

type DeviceCreate = components['schemas']['DeviceCreate'];
type LightDeviceCreate = components['schemas']['LightDeviceCreate'];
type RegistryDeviceUpdate = components['schemas']['RegistryDeviceUpdate'];

/**
 * The seven UI-approved device types mapped to the canonical backend values.
 * The form exposes these; `light` is handled by a separate create path because
 * the backend requires `board_id` and `dimming_channel` on create.
 */
export const APPROVED_DEVICE_TYPES = [
  'light',
  'heater',
  'dehumidifier',
  'exhaust',
  'humidifier',
  'co2',
  'cooling',
] as const;

export type ApprovedDeviceType = (typeof APPROVED_DEVICE_TYPES)[number];

export const NON_LIGHT_DEVICE_TYPES: readonly ApprovedDeviceType[] = APPROVED_DEVICE_TYPES.filter(
  (t) => t !== 'light',
);

export function isApprovedDeviceType(value: string): value is ApprovedDeviceType {
  return (APPROVED_DEVICE_TYPES as readonly string[]).includes(value);
}

/** Display label for an approved device type, suitable for dropdowns and table cells. */
export function approvedTypeLabel(value: ApprovedDeviceType | string): string {
  switch (value) {
    case 'co2':
      return 'CO2';
    case 'light':
      return 'Light';
    case 'heater':
      return 'Heater';
    case 'dehumidifier':
      return 'Dehumidifier';
    case 'exhaust':
      return 'Exhaust';
    case 'humidifier':
      return 'Humidifier';
    case 'cooling':
      return 'Cooling';
    default:
      return value.replace(/_/g, ' ');
  }
}

/** Unified shape of the inline create/edit form. */
export interface DeviceFormState {
  display_name: string;
  device_type: ApprovedDeviceType | '';
  room: string;
  relay_channel: string;
  board_id: string;
  dimming_channel: string;
}

export const EMPTY_DEVICE_FORM: DeviceFormState = {
  display_name: '',
  device_type: '',
  room: '',
  relay_channel: '',
  board_id: '0',
  dimming_channel: '0',
};

/** Physical relay selector option derived from the composite snapshot. */
export interface RelayOption {
  channel: number;
  physicalRelay: number;
  pinLabel: string;
  assignedDeviceName: string | null;
  assignedDisplayName: string | null;
  label: string;
}

export function buildRelayOptions(snapshot: ControlSnapshotResponse | null): RelayOption[] {
  if (!snapshot) return [];
  return snapshot.relays
    .slice()
    .sort((a, b) => a.physical_relay - b.physical_relay)
    .map((r) => {
      const assignedName = r.assignment?.device_name ?? null;
      const assignedDisplay = r.assignment?.display_name ?? null;
      const ownerSuffix = assignedDisplay ?? assignedName;
      const label = ownerSuffix
        ? `R${r.physical_relay} — ${ownerSuffix}`
        : `R${r.physical_relay}`;
      return {
        channel: r.channel,
        physicalRelay: r.physical_relay,
        pinLabel: r.pin_label,
        assignedDeviceName: assignedName,
        assignedDisplayName: assignedDisplay,
        label,
      };
    });
}

/** DFR selector option derived from the composite snapshot. */
export interface DfrOption {
  boardId: number;
  channel: number;
  assignedDeviceName: string | null;
  assignedDisplayName: string | null;
  label: string;
}

export function buildDfrOptions(snapshot: ControlSnapshotResponse | null): DfrOption[] {
  if (!snapshot) {
    const defaults: DfrOption[] = [];
    for (let board = 0; board <= 2; board++) {
      for (let channel = 0; channel <= 1; channel++) {
        defaults.push({
          boardId: board,
          channel,
          assignedDeviceName: null,
          assignedDisplayName: null,
          label: `Board ${board} · Ch ${channel}`,
        });
      }
    }
    return defaults;
  }
  const options: DfrOption[] = [];
  for (const board of snapshot.dfr_boards) {
    for (const channel of board.channels) {
      const assignedName = channel.assignment?.device_name ?? null;
      const assignedDisplay = channel.assignment?.display_name ?? null;
      const ownerSuffix = assignedDisplay ?? assignedName;
      const label = ownerSuffix
        ? `Board ${board.board_id} · Ch ${channel.channel} — ${ownerSuffix}`
        : `Board ${board.board_id} · Ch ${channel.channel}`;
      options.push({
        boardId: board.board_id,
        channel: channel.channel,
        assignedDeviceName: assignedName,
        assignedDisplayName: assignedDisplay,
        label,
      });
    }
  }
  return options;
}

export interface FormValidationResult {
  ok: boolean;
  error: string | null;
}

export function validateAddForm(form: DeviceFormState): FormValidationResult {
  if (!form.display_name.trim()) return { ok: false, error: 'Display name is required' };
  if (!form.device_type) return { ok: false, error: 'Device type is required' };
  if (!form.room) return { ok: false, error: 'Room is required' };
  if (form.device_type === 'light') {
    if (form.board_id === '' || form.dimming_channel === '') {
      return { ok: false, error: 'Light requires a complete DFR board and channel pair' };
    }
  }
  return { ok: true, error: null };
}

export function validateEditDisplayName(displayName: string): FormValidationResult {
  if (!displayName.trim()) return { ok: false, error: 'Display name is required' };
  return { ok: true, error: null };
}

export function buildCreateBody(form: DeviceFormState): DeviceCreate | LightDeviceCreate {
  const trimmedDisplay = form.display_name.trim();
  const relayChannel = form.relay_channel === '' ? null : Number(form.relay_channel);
  if (form.device_type === 'light') {
    return {
      device_type: 'light',
      display_name: trimmedDisplay,
      room: form.room,
      board_id: Number(form.board_id),
      dimming_channel: Number(form.dimming_channel),
      relay_channel: relayChannel,
    } satisfies LightDeviceCreate;
  }
  return {
    device_type: form.device_type as DeviceCreate['device_type'],
    display_name: trimmedDisplay,
    room: form.room,
    channel: relayChannel,
    pid_enabled: false,
    interlock_with: [],
    pid_setpoints: {},
  } satisfies DeviceCreate;
}

export interface LightEditUpdate {
  display_name: string;
  relay_channel: number | null;
  board_id: number;
  dimming_channel: number;
}

export interface NonLightEditUpdate {
  display_name: string;
  channel: number | null;
}

export type EditUpdateBody = LightEditUpdate | NonLightEditUpdate;

export function buildLightEditBody(form: DeviceFormState): LightEditUpdate {
  return {
    display_name: form.display_name.trim(),
    relay_channel: form.relay_channel === '' ? null : Number(form.relay_channel),
    board_id: Number(form.board_id),
    dimming_channel: Number(form.dimming_channel),
  };
}

export function buildNonLightEditBody(form: DeviceFormState): NonLightEditUpdate {
  return {
    display_name: form.display_name.trim(),
    channel: form.relay_channel === '' ? null : Number(form.relay_channel),
  };
}

export function toRegistryUpdate(body: EditUpdateBody): RegistryDeviceUpdate {
  return body as RegistryDeviceUpdate;
}

/**
 * Parse a 409 conflict detail from an axios error thrown by the registry
 * create/update endpoints. Returns `null` for any non-409 or shape mismatch.
 */
export function parseConflictDetail(err: unknown): {
  assignment: 'relay' | 'DFR';
  ownerDeviceId: number;
  ownerDeviceName: string;
  ownerDisplayName: string | null;
  displacedDeviceId: number | null;
} | null {
  try {
    const response = (err as { response?: { status?: number; data?: unknown } })?.response;
    if (!response || response.status !== 409) return null;
    const data = response.data as { detail?: unknown };
    const detail = data?.detail as
      | {
          assignment?: string;
          owner_device_id?: number;
          owner_device_name?: string;
          owner_display_name?: string | null;
          displaced_device_id?: number;
          displaced_device_name?: string;
          displaced_display_name?: string | null;
        }
      | undefined;
    if (!detail || typeof detail !== 'object') return null;
    if (detail.assignment === 'relay') {
      return {
        assignment: 'relay',
        ownerDeviceId: detail.displaced_device_id ?? detail.owner_device_id ?? 0,
        ownerDeviceName: detail.displaced_device_name ?? detail.owner_device_name ?? '',
        ownerDisplayName: detail.displaced_display_name ?? detail.owner_display_name ?? null,
        displacedDeviceId: detail.displaced_device_id ?? null,
      };
    }
    if (detail.assignment === 'DFR') {
      return {
        assignment: 'DFR',
        ownerDeviceId: detail.owner_device_id ?? 0,
        ownerDeviceName: detail.owner_device_name ?? '',
        ownerDisplayName: detail.owner_display_name ?? null,
        displacedDeviceId: null,
      };
    }
    return null;
  } catch {
    return null;
  }
}

/** Render an inherited schedule summary for a registry row. */
export function formatInheritedSchedule(
  count: number | undefined,
  summary: string[] | null | undefined,
): string {
  if (!count) return '—';
  if (summary && summary.length > 0) return summary.join(', ');
  return `${count} preserved`;
}
