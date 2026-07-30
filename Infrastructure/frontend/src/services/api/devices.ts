/** Device-related API methods (automation service). */
import type { Device, DeviceRegistryEntry, ControlHistoryEntry } from '../../types/device';
import type { RelayBoardStateResponse } from '../../types/relay';
import type { ApiClientCore } from '../api';
import type { components } from '../../generated/api';

/** Generated control snapshot response — the single typed read model for the control plane. */
export type ControlSnapshotResponse = components['schemas']['ControlSnapshotResponse'];

/** Generated typed contract for the registry list response. */
export type RegistryDeviceResponse = components['schemas']['Device'] | components['schemas']['LightDevice'];

/** Generated typed contract for registry create request bodies. */
export type RegistryDeviceCreateBody = components['schemas']['DeviceCreate'] | components['schemas']['LightDeviceCreate'];

/** Generated typed contract for registry update request bodies. */
export type RegistryDeviceUpdateBody = components['schemas']['RegistryDeviceUpdate'];

/** Relay 409 conflict detail shape returned by the backend. */
export interface RelayConflictDetail {
  assignment: 'relay';
  displaced_device_id: number;
  displaced_device_name: string;
  displaced_display_name: string | null;
}

/** DFR 409 conflict detail shape returned by the backend (hard error, no steal). */
export interface DfrConflictDetail {
  assignment: 'DFR';
  owner_device_id: number;
  owner_device_name: string;
  owner_display_name: string | null;
}

export type AssignmentConflictDetail = RelayConflictDetail | DfrConflictDetail;

/** One of the three discriminated assigned-device commands accepted by `/command`. */
export type DeviceCommandBody =
  | { action: 'AUTO'; reason?: string }
  | { action: 'MANUAL_OFF'; reason?: string }
  | { action: 'TIMED_ON'; duration_seconds: number; reason?: string };

/** Opaque JSON object returned by backend endpoints that don't have a typed contract yet. */
type JsonObject = Record<string, unknown>;

/** Minimal shape of a device entry embedded in API responses. */
interface RawDevice {
  device_type?: string;
  display_name?: string;
  dimming_enabled?: boolean;
  dimming_board_id?: string | null;
  dimming_channel?: number | null;
  mode?: string;
  state?: number;
  channel?: number | null;
  manual_expires_at?: string | null;
}

/** Type contract for device-related API methods (for declaration merging with ApiClient). */
export interface DeviceApi {
  getControlSnapshot(): Promise<ControlSnapshotResponse>;
  getAllDevices(): Promise<Device[]>;
  getDeviceRegistry(): Promise<DeviceRegistryEntry[]>;
  createDevice(
    body: RegistryDeviceCreateBody,
    confirmedRelaySteal?: boolean,
  ): Promise<RegistryDeviceResponse & { displaced_device_id?: number | null }>;
  updateDevice(
    device_id: number,
    body: RegistryDeviceUpdateBody,
    confirmedRelaySteal?: boolean,
  ): Promise<RegistryDeviceResponse & { displaced_device_id?: number | null }>;
  deleteDevice(device_id: number): Promise<{ success: boolean; device_id: number; displaced_device_id?: number | null }>;
  getDevicesForLocationCluster(location: string, cluster: string): Promise<{ location: string; cluster: string; devices: Record<string, RawDevice> }>;
  getDevicesForLocationClusterWithDetails(location: string, cluster: string): Promise<Record<string, RawDevice>>;
  getLightsForZone(location: string, cluster: string): Promise<Array<{ device_name: string; display_name?: string; dimming_enabled?: boolean; dimming_board_id?: string | null; dimming_channel?: number | null }>>;
  getRelayBoardState(): Promise<RelayBoardStateResponse>;
  testLight(device_id: number): Promise<{ success: boolean }>;
  controlDevice(location: string, cluster: string, device: string, state: number, reason?: string, durationSeconds?: number): Promise<JsonObject>;
  commandDevice(location: string, cluster: string, device: string, command: DeviceCommandBody): Promise<JsonObject>;
  controlChannel(channel: number, state: 0 | 1, durationSeconds?: number): Promise<JsonObject>;
  setDeviceMode(location: string, cluster: string, device: string, mode: 'manual' | 'auto' | 'scheduled'): Promise<JsonObject>;
  getControlHistory(location: string, cluster: string, limit?: number): Promise<ControlHistoryEntry[]>;
}

export const deviceMethods = {
  async getControlSnapshot(this: ApiClientCore): Promise<ControlSnapshotResponse> {
    const response = await this.automationClient.get('/api/devices/control-snapshot');
    return response.data;
  },

  // Device listing
  async getAllDevices(this: ApiClientCore): Promise<Device[]> {
    const response = await this.automationClient.get('/api/devices');
    return response.data;
  },

  // Device Registry CRUD
  async getDeviceRegistry(this: ApiClientCore): Promise<DeviceRegistryEntry[]> {
    const response = await this.automationClient.get('/api/devices/registry');
    return response.data;
  },

  async createDevice(
    this: ApiClientCore,
    body: RegistryDeviceCreateBody,
    confirmedRelaySteal = false,
  ): Promise<RegistryDeviceResponse & { displaced_device_id?: number | null }> {
    const response = await this.automationClient.post('/api/devices/registry', body, {
      params: confirmedRelaySteal ? { confirmed_relay_steal: true } : undefined,
    });
    return response.data;
  },

  async updateDevice(
    this: ApiClientCore,
    device_id: number,
    body: RegistryDeviceUpdateBody,
    confirmedRelaySteal = false,
  ): Promise<RegistryDeviceResponse & { displaced_device_id?: number | null }> {
    const response = await this.automationClient.put(`/api/devices/registry/${device_id}`, body, {
      params: confirmedRelaySteal ? { confirmed_relay_steal: true } : undefined,
    });
    return response.data;
  },

  async deleteDevice(
    this: ApiClientCore,
    device_id: number,
  ): Promise<{ success: boolean; device_id: number; displaced_device_id?: number | null }> {
    const response = await this.automationClient.delete(`/api/devices/registry/${device_id}`, {
      headers: { 'X-Confirm-Destructive': 'true' },
    });
    return response.data;
  },

  async getDevicesForLocationCluster(this: ApiClientCore, location: string, cluster: string): Promise<{ location: string; cluster: string; devices: Record<string, RawDevice> }> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data;
  },

  async getDevicesForLocationClusterWithDetails(this: ApiClientCore, location: string, cluster: string): Promise<Record<string, RawDevice>> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data.devices || {};
  },

  async getLightsForZone(this: ApiClientCore, location: string, cluster: string): Promise<Array<{ device_name: string; display_name?: string; dimming_enabled?: boolean; dimming_board_id?: string | null; dimming_channel?: number | null }>> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    const devices: Record<string, RawDevice> = response.data.devices || {};
    return Object.entries(devices)
      .filter(([, device]) => device.device_type === 'light')
      .map(([deviceName, device]) => ({
        device_name: deviceName,
        display_name: device.display_name,
        dimming_enabled: device.dimming_enabled,
        dimming_board_id: device.dimming_board_id,
        dimming_channel: device.dimming_channel
      }));
  },

  // Channels & relay state
  async getRelayBoardState(this: ApiClientCore): Promise<RelayBoardStateResponse> {
    const response = await this.automationClient.get('/api/hardware/relays/state');
    return response.data;
  },

  async testLight(this: ApiClientCore, device_id: number): Promise<{ success: boolean }> {
    const response = await this.automationClient.post(`/api/lights/${device_id}/test`);
    return response.data;
  },

  // Device control
  async commandDevice(
    this: ApiClientCore,
    location: string,
    cluster: string,
    device: string,
    command: DeviceCommandBody,
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/command`,
      command,
    );
    return response.data;
  },

  async controlDevice(
    this: ApiClientCore,
    location: string,
    cluster: string,
    device: string,
    state: number,
    reason?: string,
    durationSeconds?: number
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/control`,
      {
        state,
        reason: reason || 'Manual override',
        duration_seconds: durationSeconds
      }
    );
    return response.data;
  },

  async controlChannel(this: ApiClientCore, channel: number, state: 0 | 1, durationSeconds?: number): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/hardware/relays/channel/${channel}/state`,
      { state, duration_seconds: durationSeconds ?? null }
    );
    return response.data;
  },

  async setDeviceMode(
    this: ApiClientCore,
    location: string,
    cluster: string,
    device: string,
    mode: 'manual' | 'auto' | 'scheduled'
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/mode`,
      { mode }
    );
    return response.data;
  },

  // Control history
  async getControlHistory(
    this: ApiClientCore,
    location: string,
    cluster: string,
    limit?: number
  ): Promise<ControlHistoryEntry[]> {
    const response = await this.automationClient.get('/api/control/history', {
      params: { location, cluster, limit: limit ?? 10 },
    });
    return response.data ?? [];
  },
};
