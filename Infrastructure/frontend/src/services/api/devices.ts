/** Device-related API methods (automation service). */
import type { Device, DeviceRegistryEntry, ControlHistoryEntry } from '../../types/device';
import type { RelayBoardStateResponse } from '../../types/relay';
import type { ApiClientCore } from '../api';

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
  getAllDevices(): Promise<Device[]>;
  getDeviceRegistry(): Promise<DeviceRegistryEntry[]>;
  createDevice(body: Record<string, unknown>): Promise<DeviceRegistryEntry>;
  updateDevice(device_id: number, body: Record<string, unknown>): Promise<DeviceRegistryEntry & { displaced_device_id?: number | null }>;
  deleteDevice(device_id: number): Promise<{ success: boolean }>;
  getDevicesForLocationCluster(location: string, cluster: string): Promise<{ location: string; cluster: string; devices: Record<string, RawDevice> }>;
  getDevicesForLocationClusterWithDetails(location: string, cluster: string): Promise<Record<string, RawDevice>>;
  getLightsForZone(location: string, cluster: string): Promise<Array<{ device_name: string; display_name?: string; dimming_enabled?: boolean; dimming_board_id?: string | null; dimming_channel?: number | null }>>;
  getRelayBoardState(): Promise<RelayBoardStateResponse>;
  testLight(device_id: number): Promise<{ success: boolean }>;
  controlDevice(location: string, cluster: string, device: string, state: number, reason?: string, durationSeconds?: number): Promise<JsonObject>;
  controlChannel(channel: number, state: 0 | 1, durationSeconds?: number): Promise<JsonObject>;
  setDeviceMode(location: string, cluster: string, device: string, mode: 'manual' | 'auto' | 'scheduled'): Promise<JsonObject>;
  getControlHistory(location: string, cluster: string, limit?: number): Promise<ControlHistoryEntry[]>;
}

export const deviceMethods = {
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

  async createDevice(this: ApiClientCore, body: Record<string, unknown>): Promise<DeviceRegistryEntry> {
    const response = await this.automationClient.post('/api/devices/registry', body);
    return response.data;
  },

  async updateDevice(this: ApiClientCore, device_id: number, body: Record<string, unknown>): Promise<DeviceRegistryEntry & { displaced_device_id?: number | null }> {
    const response = await this.automationClient.put(`/api/devices/registry/${device_id}`, body);
    return response.data;
  },

  async deleteDevice(this: ApiClientCore, device_id: number): Promise<{ success: boolean }> {
    const response = await this.automationClient.delete(`/api/devices/registry/${device_id}`, {
      headers: { 'X-Confirm-Destructive': 'true' }
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
