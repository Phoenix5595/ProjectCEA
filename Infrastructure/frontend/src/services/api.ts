/** API client for backend communication. */
import axios, { AxiosInstance } from 'axios';
import type { SensorDataResponse } from '../types/sensor';
import type { Device, ControlHistoryEntry } from '../types/device';
import type { PIDParameters, PIDParameterUpdate, PIDModeInfo, PIDModeUpdate, AutotuneState } from '../types/pid';
import type { Schedule, ScheduleCreate, ScheduleUpdate } from '../types/schedule';
import type { LightStatus, LightTargetSetResponse } from '../types/light';
import type { RoomMode, FlowerSubmode, RoomModeWithParams, SetModeRequest, UpdateParametersRequest } from '../types/modes';
import type { ChannelInfo, LightNameOption } from '../types/relay';
import { AUTOMATION_API_URL, BACKEND_API_URL, CEA_API_KEY, WEATHER_API_URL } from '../config/env';

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
}

interface WeatherMeasurement {
  value?: number | string;
  unit?: string;
}

export interface WeatherResponse {
  timestamp?: string;
  data?: {
    temp?: WeatherMeasurement;
    temperature?: WeatherMeasurement;
    rh?: WeatherMeasurement;
    humidity?: WeatherMeasurement;
    pressure?: WeatherMeasurement;
    wind_speed?: WeatherMeasurement;
    wind_direction?: WeatherMeasurement;
    description?: { value?: string };
    [key: string]: unknown;
  };
}

export interface SystemStatusResponse {
  system?: {
    cpu_percent?: number;
    memory_percent?: number;
    disk_percent?: number;
    uptime_seconds?: number;
    load_avg?: number[];
    process_count?: number;
    cpu_temp_c?: number;
    throttle_status?: string;
  };
  devices?: Record<string, Record<string, Record<string, { intensity?: number; load_percent?: number }>>>;
  service_health?: Array<{ name: string; status: string; latency_ms?: number }>;
}

class ApiClient {
  private backendClient: AxiosInstance;
  private automationClient: AxiosInstance;
  private weatherClient: AxiosInstance;

  constructor() {
    const baseHeaders: Record<string, string> = {
      'Content-Type': 'application/json',
    };
    if (CEA_API_KEY) {
      baseHeaders['X-API-Key'] = CEA_API_KEY;
    }

    this.backendClient = axios.create({
      baseURL: BACKEND_API_URL,
      headers: { ...baseHeaders },
      timeout: 10000,
    });

    this.automationClient = axios.create({
      baseURL: AUTOMATION_API_URL,
      headers: { ...baseHeaders },
      timeout: 30000,
    });

    this.weatherClient = axios.create({
      baseURL: WEATHER_API_URL,
      headers: { ...baseHeaders },
      timeout: 10000,
    });
    
    // Add response interceptor for better error handling
    this.automationClient.interceptors.response.use(
      (response) => response,
      (error) => {
        // Transform network errors into more user-friendly messages
        if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
          error.message = 'Request timed out. Please check your connection and try again.';
        } else if (error.code === 'ERR_NETWORK' || !error.response) {
          error.message = 'Network error: Unable to connect to the automation service. Please check if the service is running.';
        }
        return Promise.reject(error);
      }
    );
  }

  // Sensors (backend service)
  async getLiveSensorData(location: string, cluster: string): Promise<SensorDataResponse> {
    const response = await this.backendClient.get(`/api/sensors/${location}/${cluster}/live`);
    return response.data;
  }

  async getSensorDataBulk(keys: string[]): Promise<Record<string, number>> {
    const response = await this.backendClient.post('/api/sensor-data', { keys });
    return response.data ?? {};
  }

  async getAllLiveSensorData(): Promise<Array<{ sensor: string; value: number; time: string; unit: string }>> {
    const response = await this.backendClient.get('/api/sensors/live/all');
    return Array.isArray(response.data) ? response.data : [];
  }

  // Devices (automation service)
  async getAllDevices(): Promise<Device[]> {
    const response = await this.automationClient.get('/api/devices');
    return response.data;
  }

  async getDevicesForLocationCluster(location: string, cluster: string): Promise<{ location: string; cluster: string; devices: Record<string, RawDevice> }> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data;
  }

  // DFR0971 (automation service)
  async getDfrAssignments(): Promise<{
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
  }

  async assignDfrChannel(
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
  }

  async updateDeviceConfig(
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
  }

  async getChannels(): Promise<{ channels: Record<string, ChannelInfo>; light_names: LightNameOption[] }> {
    const response = await this.automationClient.get('/api/devices/channels');
    return response.data;
  }

  async updateChannelDevice(
    channel: number,
    deviceName: string,
    deviceType: string,
    location: string,
    cluster: string,
    lightName?: string
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/channels/${channel}`,
      {
        device_name: deviceName,
        device_type: deviceType,
        location: location,
        cluster: cluster,
        light_name: lightName
      }
    );
    return response.data;
  }

  async getRelayBoardState(): Promise<{ channels: boolean[]; mcp_connected: boolean; simulation: boolean }> {
    const response = await this.automationClient.get('/api/hardware/relays/state');
    return response.data;
  }

  async clearChannelDevice(channel: number): Promise<JsonObject> {
    const response = await this.automationClient.delete(`/api/devices/channels/${channel}`);
    return response.data;
  }

  // Notes (automation service, persisted outside deploy)
  async getNotes(location: string, cluster: string, mode: string): Promise<{ content: string }> {
    const response = await this.automationClient.get(`/api/notes/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(mode)}`);
    return response.data;
  }

  async saveNotes(location: string, cluster: string, mode: string, content: string): Promise<{ content: string }> {
    const response = await this.automationClient.put(
      `/api/notes/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(mode)}`,
      { content }
    );
    return response.data;
  }

  // PID Parameters (automation service)
  async getAllPIDParameters(): Promise<Record<string, PIDParameters>> {
    const response = await this.automationClient.get('/api/pid/parameters');
    return response.data;
  }

  async getPIDParameters(deviceType: string): Promise<PIDParameters> {
    const response = await this.automationClient.get(`/api/pid/parameters/${deviceType}`);
    return response.data;
  }

  async updatePIDParameters(deviceType: string, params: PIDParameterUpdate): Promise<PIDParameters> {
    const response = await this.automationClient.post(`/api/pid/parameters/${deviceType}`, params);
    return response.data;
  }
  async resetPIDParameters(deviceType: string): Promise<PIDParameters> {
    const response = await this.automationClient.post(`/api/pid/parameters/${deviceType}/reset`);
    return response.data;
  }

  async getPIDParameterHistory(deviceType: string, limit: number = 50): Promise<import('../types/pid').PIDHistoryEntry[]> {
    const response = await this.automationClient.get(`/api/pid/parameters/${deviceType}/history`, {
      params: { limit }
    });
    return response.data;
  }

  // PID Control Modes (automation service)
  async getPIDMode(deviceType: string): Promise<PIDModeInfo> {
    const response = await this.automationClient.get(`/api/pid/mode/${deviceType}`);
    return response.data;
  }

  async setPIDMode(deviceType: string, update: PIDModeUpdate): Promise<PIDModeInfo> {
    const response = await this.automationClient.post(`/api/pid/mode/${deviceType}`, update);
    return response.data;
  }

  async getAutotuneStatus(deviceType: string): Promise<AutotuneState> {
    const response = await this.automationClient.get(`/api/pid/autotune/${deviceType}/status`);
    return response.data;
  }

  async stopAutotune(deviceType: string): Promise<AutotuneState> {
    const response = await this.automationClient.post(`/api/pid/autotune/${deviceType}/stop`);
    return response.data;
  }

  // Schedules (automation service)
  async getSchedules(location?: string, cluster?: string): Promise<Schedule[]> {
    const params: Record<string, string> = {};
    if (location) params.location = location;
    if (cluster) params.cluster = cluster;
    const response = await this.automationClient.get('/api/schedules', { params });
    return response.data;
  }

  async getSchedulesForDevice(location: string, cluster: string, deviceName: string): Promise<Schedule[]> {
    const params: Record<string, string> = { location, cluster, device_name: deviceName };
    const response = await this.automationClient.get('/api/schedules', { params });
    return response.data;
  }

  async createSchedule(schedule: ScheduleCreate): Promise<Schedule> {
    const response = await this.automationClient.post('/api/schedules', schedule);
    return response.data;
  }

  async updateSchedule(scheduleId: number, schedule: ScheduleUpdate): Promise<Schedule> {
    const response = await this.automationClient.put(`/api/schedules/${scheduleId}`, schedule);
    return response.data;
  }

  async deleteSchedule(scheduleId: number): Promise<void> {
    await this.automationClient.delete(`/api/schedules/${scheduleId}`);
  }

  // Modes (automation service)
  async getMode(location: string, cluster: string): Promise<{ location: string; cluster: string; mode: string }> {
    const response = await this.automationClient.get(`/api/mode/${location}/${cluster}`);
    return response.data;
  }

  async getAllModes(): Promise<Record<string, { location: string; cluster: string; mode: string }>> {
    const response = await this.automationClient.get('/api/mode');
    return response.data;
  }

  // Lights (automation service)
  async getLightStatus(location: string, cluster: string, deviceName: string): Promise<LightStatus> {
    const response = await this.automationClient.get(`/api/lights/${location}/${cluster}/${deviceName}/status`);
    return response.data;
  }

  /** All dimmable lights in zone in one request (avoids N sequential status calls for LightIntensity). */
  async getZoneLightsStatus(location: string, cluster: string): Promise<{
    lights: Array<{
      device: string;
      display_name?: string;
      intensity: number;
      target_intensity?: number | null;
      day_target_intensity?: number | null;
      schedule_sun_target_intensity?: number | null;
      scheduler_nominal_intensity?: number | null;
      voltage?: number;
      board_id?: string;
      channel?: number;
    }>;
  }> {
    const response = await this.automationClient.get(`/api/lights/${location}/${cluster}/zone-status`);
    return response.data;
  }

  async setLightIntensity(
    location: string,
    cluster: string,
    deviceName: string,
    intensity: number
  ): Promise<LightTargetSetResponse> {
    const response = await this.automationClient.post(`/api/lights/${location}/${cluster}/${deviceName}/target`, {
      target_intensity: intensity
    });
    return response.data;
  }

  async getLightSchedule(location: string, cluster: string, deviceName: string): Promise<{ start_time: string; end_time: string; target_intensity: number }> {
    const response = await this.automationClient.get(`/api/lights/${location}/${cluster}/${deviceName}/schedule`);
    return response.data;
  }

  async updateLightSchedule(location: string, cluster: string, deviceName: string, startTime: string, endTime: string): Promise<JsonObject> {
    const response = await this.automationClient.put(`/api/lights/${location}/${cluster}/${deviceName}/schedule`, {
      start_time: startTime,
      end_time: endTime
    });
    return response.data;
  }

  async getDevicesForLocationClusterWithDetails(location: string, cluster: string): Promise<Record<string, RawDevice>> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data.devices || {};
  }

  async getLightsForZone(location: string, cluster: string): Promise<Array<{ device_name: string; display_name?: string; dimming_enabled?: boolean; dimming_board_id?: string | null; dimming_channel?: number | null }>> {
    const devices = await this.getDevicesForLocationClusterWithDetails(location, cluster);
    return Object.entries(devices)
      .filter(([_, device]) => device.device_type === 'light')
      .map(([deviceName, device]) => ({
        device_name: deviceName,
        display_name: device.display_name,
        dimming_enabled: device.dimming_enabled,
        dimming_board_id: device.dimming_board_id,
        dimming_channel: device.dimming_channel
      }));
  }

  // Room Schedule (automation service)
  async getClimateSchedule(location: string, cluster: string): Promise<JsonObject> {
    const response = await this.automationClient.get(`/api/climate-schedule/${location}/${cluster}`);
    return response.data;
  }

  async saveClimateSchedule(location: string, cluster: string, schedule: JsonObject): Promise<JsonObject> {
    const response = await this.automationClient.post(`/api/climate-schedule/${location}/${cluster}`, schedule);
    return response.data;
  }

  async getClimatePeriods(
    location: string,
    cluster: string,
    modeId?: number | null,
    submodeId?: number | null
  ): Promise<JsonObject[]> {
    const params: Record<string, number> = {};
    if (modeId != null) {
      params.mode_id = modeId;
      if (submodeId != null) {
        params.submode_id = submodeId;
      }
    }
    const response = await this.automationClient.get(`/api/climate-periods/${location}/${cluster}`, {
      params: Object.keys(params).length > 0 ? params : undefined,
    });
    return response.data;
  }

  async saveClimatePeriods(location: string, cluster: string, periods: JsonObject[], modeId?: number, submodeId?: number): Promise<JsonObject> {
    const response = await this.automationClient.post(`/api/climate-periods/${location}/${cluster}`, { 
      periods,
      mode_id: modeId ?? null,
      submode_id: submodeId ?? null
    });
    return response.data;
  }

  async getRoomSchedule(location: string, cluster: string): Promise<JsonObject> {
    const response = await this.automationClient.get(`/api/room-schedule/${location}/${cluster}`);
    return response.data;
  }

  async saveRoomSchedule(location: string, cluster: string, schedule: JsonObject): Promise<void> {
    await this.automationClient.post(`/api/room-schedule/${location}/${cluster}`, schedule);
  }

  // Weather Service
  async getLatestWeather(): Promise<WeatherResponse> {
    const response = await this.weatherClient.get('/weather/latest');
    return response.data;
  }

  // System Status (automation service) - optimized with health=false for fast dashboard polling
  async getSystemStatus(): Promise<SystemStatusResponse> {
    const response = await this.automationClient.get('/api/status?health=false');
    return response.data;
  }

  // System Health - separate endpoint for health status only (slower, poll less frequently)
  async getSystemHealth(): Promise<Array<{ name: string; status: string; latency_ms?: number }>> {
    const response = await this.automationClient.get('/api/status?health=true');
    return response.data.service_health || [];
  }

  // Control history (recent on/off log per room)
  async getControlHistory(
    location: string,
    cluster: string,
    limit?: number
  ): Promise<ControlHistoryEntry[]> {
    const response = await this.automationClient.get('/api/control/history', {
      params: { location, cluster, limit: limit ?? 10 },
    });
    return response.data ?? [];
  }

  // Device Control (automation service)
  async controlDevice(
    location: string,
    cluster: string,
    device: string,
    state: number,
    reason?: string
  ): Promise<JsonObject> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/control`,
      {
        state,
        reason: reason || 'Manual override'
      }
    );
    return response.data;
  }

  async setDeviceMode(
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
  }

  // Room Modes (automation service)
  async getRoomModes(): Promise<RoomMode[]> {
    const response = await this.automationClient.get('/api/room-modes/modes');
    return response.data;
  }

  async getFlowerSubmodes(): Promise<FlowerSubmode[]> {
    const response = await this.automationClient.get('/api/room-modes/submodes');
    return response.data;
  }

  async getRoomModeWithParams(location: string, cluster: string): Promise<RoomModeWithParams> {
    const response = await this.automationClient.get(`/api/room-modes/room/${location}/${cluster}`);
    return response.data;
  }

  async setRoomMode(location: string, cluster: string, request: SetModeRequest): Promise<RoomModeWithParams> {
    const response = await this.automationClient.post(`/api/room-modes/room/${location}/${cluster}/mode`, request);
    return response.data;
  }

  async updateRoomParameters(location: string, cluster: string, params: UpdateParametersRequest): Promise<RoomModeWithParams> {
    const response = await this.automationClient.put(`/api/room-modes/room/${location}/${cluster}/parameters`, params);
    return response.data;
  }
}

export const apiClient = new ApiClient();

