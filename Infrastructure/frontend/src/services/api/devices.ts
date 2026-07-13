/** Device-related API methods (automation service). */
import type { Device, DeviceRegistryEntry, ControlHistoryEntry } from '../../types/device';
import type { LightDevice } from '../../types/light';
import type { ChannelInfo, LightNameOption, RelayBoardStateResponse } from '../../types/relay';
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
  channel?: number;
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
  updateDeviceConfig(location: string, cluster: string, device: string, displayName?: string, deviceType?: string): Promise<JsonObject>;
  getChannels(): Promise<{ channels: Record<string, ChannelInfo>; light_names: LightNameOption[] }>;
  getRelayBoardState(): Promise<RelayBoardStateResponse>;
  getDfrAssignments(): Promise<{
    boards: Array<{ board_id: number; i2c_address: string; name?: string; available?: boolean }>;
    assignments: Record<string, { '0': { location: string; cluster: string; device_name: string; display_name?: string | null } | null; '1': { location: string; cluster: string; device_name: string; display_name?: string | null } | null }>;
    lights: Array<{ location: string; cluster: string; device_name: string; display_name?: string | null; dimming_board_id?: number | null; dimming_channel?: number | null }>;
  }>;
  assignDfrChannel(location: string, cluster: string, deviceName: string, boardId: number | null, dimmingChannel: number | null): Promise<JsonObject>;
  createLight(body: { board_id: number; dimming_channel: number; room: string; display_name: string; per_room_index?: number }): Promise<LightDevice>;
  updateLight(device_id: number, body: { display_name?: string; room?: string; per_room_index?: number; relay_channel?: number | null; safety_level?: number }): Promise<LightDevice>;
  deleteLight(device_id: number): Promise<{ success: boolean; warning?: string }>;
  testLight(device_id: number): Promise<{ success: boolean }>;
  getLightsByRoom(room: string): Promise<LightDevice[]>;
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

  async updateDeviceConfig(
    this: ApiClientCore,
    location: string,
    cluster: string,
    device: string,
    displayName?: string,
    deviceType?: string
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/config`,
      {
        display_name: displayName,
        device_type: deviceType
      }
    );
    return response.data;
  },

  // Channels & relay state
  async getChannels(this: ApiClientCore): Promise<{ channels: Record<string, ChannelInfo>; light_names: LightNameOption[] }> {
    const response = await this.automationClient.get('/api/devices/channels');
    return response.data;
  },

  async getRelayBoardState(this: ApiClientCore): Promise<RelayBoardStateResponse> {
    const response = await this.automationClient.get('/api/hardware/relays/state');
    return response.data;
  },

  // DFR0971 dimming boards
  async getDfrAssignments(this: ApiClientCore): Promise<{
    boards: Array<{ board_id: number; i2c_address: string; name?: string; available?: boolean }>;
    assignments: Record<
      string,
      {
        '0': { location: string; cluster: string; device_name: string; display_name?: string | null } | null;
        '1': { location: string; cluster: string; device_name: string; display_name?: string | null } | null;
      }
    >;
    lights: Array<{
      location: string;
      cluster: string;
      device_name: string;
      display_name?: string | null;
      dimming_board_id?: number | null;
      dimming_channel?: number | null;
    }>;
  }> {
    const response = await this.automationClient.get('/api/lights/dfr/assignments');
    return response.data;
  },

  async assignDfrChannel(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceName: string,
    boardId: number | null,
    dimmingChannel: number | null
  ): Promise<JsonObject> {
    const response = await this.automationClient.put('/api/lights/dfr/assign', {
      location,
      cluster,
      device_name: deviceName,
      board_id: boardId,
      dimming_channel: dimmingChannel,
    });
    return response.data;
  },

  // Light CRUD
  async createLight(this: ApiClientCore, body: {
    board_id: number;
    dimming_channel: number;
    room: string;
    display_name: string;
    per_room_index?: number;
  }): Promise<LightDevice> {
    const response = await this.automationClient.post('/api/lights', body);
    return response.data;
  },

  async updateLight(
    this: ApiClientCore,
    device_id: number,
    body: {
      display_name?: string;
      room?: string;
      per_room_index?: number;
      relay_channel?: number | null;
      safety_level?: number;
    }
  ): Promise<LightDevice> {
    const response = await this.automationClient.put(`/api/lights/${device_id}`, body);
    return response.data;
  },

  async deleteLight(this: ApiClientCore, device_id: number): Promise<{ success: boolean; warning?: string }> {
    const response = await this.automationClient.delete(`/api/lights/${device_id}`, {
      headers: { 'X-Confirm-Destructive': 'true' }
    });
    return response.data;
  },

  async testLight(this: ApiClientCore, device_id: number): Promise<{ success: boolean }> {
    const response = await this.automationClient.post(`/api/lights/${device_id}/test`);
    return response.data;
  },

  async getLightsByRoom(this: ApiClientCore, room: string): Promise<LightDevice[]> {
    const response = await this.automationClient.get('/api/devices/channels');
    const lightNames: Array<{
      name: string;
      device_name: string;
      location: string;
      cluster: string;
      bound_relay_channel?: number | null;
      device_id?: number | null;
    }> = response.data?.light_names ?? [];
    return lightNames
      .filter((light) => light.location === room)
      .map((light) => ({
        device_id: light.device_id ?? undefined,
        device_name: light.device_name,
        display_name: light.name,
        location: light.location,
        cluster: light.cluster,
        state: 0,
        mode: 'auto',
        channel: -1,
        bound_relay_channel: light.bound_relay_channel ?? null,
      }));
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
