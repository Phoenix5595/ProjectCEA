/**
 * API client for backend communication.
 *
 * Core ApiClient class with axios clients and methods for domains not split
 * into separate modules (PID, modes, lights status, calendar, weather, etc.).
 * Device, sensor, and schedule methods are mixed in from services/api/*.
 */
import axios, { AxiosInstance } from 'axios';
import type { LightStatus, LightTargetSetResponse } from '../types/light';
import type { RoomMode, FlowerSubmode, RoomModeWithParams, SetModeRequest, UpdateParametersRequest } from '../types/modes';
import type {
  CalendarEventsResponse,
  CalendarEventDto,
  CalendarRoomProfile,
  FlowerGrowPlanRequest,
  ModeScheduleResponse,
} from '../types/calendar';
import type {
  SystemConfigResponse,
  ConfigUpdateRequest,
  ConfigUpdateResponse,
  RestartServiceResponse,
} from '../types/systemConfig';
import { AUTOMATION_API_URL, BACKEND_API_URL, CEA_API_KEY, WEATHER_API_URL } from '../config/env';

import { deviceMethods } from './api/devices';
import { sensorMethods } from './api/sensors';
import { scheduleMethods } from './api/schedules';
import { pidMethods } from './api/pid';
import type { DeviceApi } from './api/devices';
import type { SensorApi } from './api/sensors';
import type { ScheduleApi } from './api/schedules';
import type { PidApi } from './api/pid';

type JsonObject = Record<string, unknown>;

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

interface WeatherMeasurement {
  value?: number | string;
  unit?: string;
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
  degraded?: {
    active?: boolean;
    reason?: string;
    failure_count?: number;
    success_count?: number;
    updated_at?: string;
  };
}

/** Structural type for the axios clients — used by domain method modules. */
export interface ApiClientCore {
  backendClient: AxiosInstance;
  automationClient: AxiosInstance;
  weatherClient: AxiosInstance;
}

class ApiClient implements ApiClientCore {
  backendClient: AxiosInstance;
  automationClient: AxiosInstance;
  weatherClient: AxiosInstance;

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

    this.automationClient.interceptors.response.use(
      (response) => response,
      (error) => {
        if (error.code === 'ECONNABORTED' || error.message.includes('timeout')) {
          error.message = 'Request timed out. Please check your connection and try again.';
        } else if (error.code === 'ERR_NETWORK' || !error.response) {
          error.message = 'Network error: Unable to connect to the automation service. Please check if the service is running.';
        }
        return Promise.reject(error);
      }
    );
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

  async updateLightIntensity(
    deviceId: number,
    intensity: number
  ): Promise<{ success: boolean; device_id: number; target_intensity: number }> {
    const response = await this.automationClient.put(`/api/lights/${deviceId}/intensity`, {
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

  // Weather Service
  async getLatestWeather(): Promise<WeatherResponse> {
    const response = await this.weatherClient.get('/weather/latest');
    return response.data;
  }

  // System Status (automation service)
  async getSystemStatus(): Promise<SystemStatusResponse> {
    const response = await this.automationClient.get('/api/status?health=false');
    return response.data;
  }

  async getSystemHealth(): Promise<Array<{ name: string; status: string; latency_ms?: number }>> {
    const response = await this.automationClient.get('/api/status?health=true');
    return response.data.service_health || [];
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

  // Calendar (automation service)
  async getCalendarRooms(): Promise<CalendarRoomProfile[]> {
    const response = await this.automationClient.get('/api/calendar/rooms');
    return response.data;
  }

  async getCalendarEvents(
    from: string,
    to: string,
    location?: string,
    cursor?: string
  ): Promise<CalendarEventsResponse> {
    const params: Record<string, string> = { from, to };
    if (location) params.location = location;
    if (cursor) params.cursor = cursor;
    const response = await this.automationClient.get('/api/calendar/events', { params });
    return response.data;
  }

  async createFlowerGrowPlan(body: FlowerGrowPlanRequest): Promise<{
    grow_plan_id: string;
    crop_batch_id: number;
    events: CalendarEventDto[];
  }> {
    const response = await this.automationClient.post('/api/calendar/grow-plans/flower', body);
    return response.data;
  }

  async deleteGrowPlan(growPlanId: string): Promise<{ deleted: number }> {
    const response = await this.automationClient.delete(`/api/calendar/grow-plans/${growPlanId}`);
    return response.data;
  }

  async getModeSchedule(
    location: string,
    cluster: string,
    date?: string
  ): Promise<ModeScheduleResponse> {
    const params = date ? { date } : {};
    const response = await this.automationClient.get(
      `/api/calendar/mode-schedule/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}`,
      { params }
    );
    return response.data;
  }

  async getCalendarSyncConnection(): Promise<JsonObject | null> {
    const response = await this.automationClient.get('/api/calendar/sync/connections');
    return response.data;
  }

  async saveCalendarSyncConnection(body: {
    caldav_base_url: string;
    username: string;
    app_password: string;
    target_calendar_url: string;
    display_name?: string;
  }): Promise<JsonObject> {
    const response = await this.automationClient.post('/api/calendar/sync/connections', body);
    return response.data;
  }

  async testCalendarSyncConnection(body: {
    caldav_base_url: string;
    username: string;
    app_password: string;
  }): Promise<Array<{ name: string; url: string }>> {
    const response = await this.automationClient.post('/api/calendar/sync/connections/test', body);
    return response.data;
  }

  async runCalendarSync(): Promise<JsonObject> {
    const response = await this.automationClient.post('/api/calendar/sync/run');
    return response.data;
  }

  async deleteCalendarSyncConnection(): Promise<void> {
    await this.automationClient.delete('/api/calendar/sync/connections');
  }

  // System Config (automation service)
  async getConfig(): Promise<SystemConfigResponse> {
    const response = await this.automationClient.get('/api/config');
    return response.data;
  }

  async putConfig(update: ConfigUpdateRequest): Promise<ConfigUpdateResponse> {
    const response = await this.automationClient.put('/api/config', update);
    return response.data;
  }

  async restartService(): Promise<RestartServiceResponse> {
    const response = await this.automationClient.post('/api/config/restart');
    return response.data;
  }
}

// Attach domain methods (devices, sensors, schedules, pid) to the prototype.
Object.assign(ApiClient.prototype, deviceMethods, sensorMethods, scheduleMethods, pidMethods);

export type { DeviceApi, SensorApi, ScheduleApi, PidApi };

export const apiClient = new ApiClient() as ApiClient & DeviceApi & SensorApi & ScheduleApi & PidApi;
