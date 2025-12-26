/** API client for backend communication. */
import axios, { AxiosInstance } from 'axios';
import type { Setpoint, SetpointUpdate } from '../types/setpoint';
import type { SensorDataResponse } from '../types/sensor';
import type { Device } from '../types/device';
import type { PIDParameters, PIDParameterUpdate } from '../types/pid';
import type { Schedule, ScheduleCreate, ScheduleUpdate } from '../types/schedule';
import type { LightStatus } from '../types/light';

function defaultApiUrl(port: number): string {
  // When accessed from another device, "localhost" points to the user's device,
  // so default to the current page hostname instead.
  if (typeof window === 'undefined') return `http://localhost:${port}`;
  const protocol = window.location.protocol === 'https:' ? 'https:' : 'http:';
  return `${protocol}//${window.location.hostname}:${port}`;
}

// Backend service (sensor data) - port 8000
const BACKEND_API_URL = import.meta.env.VITE_BACKEND_API_URL || defaultApiUrl(8000);
// Automation service (configuration) - port 8001
const AUTOMATION_API_URL = import.meta.env.VITE_AUTOMATION_API_URL || defaultApiUrl(8001);

class ApiClient {
  private backendClient: AxiosInstance;
  private automationClient: AxiosInstance;

  constructor() {
    this.backendClient = axios.create({
      baseURL: BACKEND_API_URL,
      headers: {
        'Content-Type': 'application/json',
      },
    });
    
    this.automationClient = axios.create({
      baseURL: AUTOMATION_API_URL,
      headers: {
        'Content-Type': 'application/json',
      },
      timeout: 30000, // 30 second timeout
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

  // Setpoints (automation service)
  async getSetpoints(location: string, cluster: string, mode?: string): Promise<Setpoint> {
    const params = mode ? { mode } : {};
    const response = await this.automationClient.get(`/api/setpoints/${location}/${cluster}`, { params });
    return response.data;
  }

  async getAllSetpointsForLocationCluster(location: string, cluster: string): Promise<Setpoint[]> {
    const response = await this.automationClient.get(`/api/setpoints/${location}/${cluster}/all-modes`);
    return response.data;
  }

  async updateSetpoints(location: string, cluster: string, setpoints: SetpointUpdate): Promise<Setpoint> {
    const response = await this.automationClient.post(`/api/setpoints/${location}/${cluster}`, setpoints);
    return response.data;
  }

  // Sensors (backend service)
  async getLiveSensorData(location: string, cluster: string): Promise<SensorDataResponse> {
    const response = await this.backendClient.get(`/api/sensors/${location}/${cluster}/live`);
    return response.data;
  }

  // Devices (automation service)
  async getAllDevices(): Promise<Device[]> {
    const response = await this.automationClient.get('/api/devices');
    return response.data;
  }

  async getDevicesForLocationCluster(location: string, cluster: string): Promise<{ location: string; cluster: string; devices: Record<string, any> }> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data;
  }

  async updateDeviceConfig(
    location: string,
    cluster: string,
    device: string,
    displayName?: string,
    deviceType?: string
  ): Promise<any> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/config`,
      {
        display_name: displayName,
        device_type: deviceType
      }
    );
    return response.data;
  }

  async getChannels(): Promise<{ channels: Record<string, any>; light_names: any[] }> {
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
  ): Promise<any> {
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

  // Schedules (automation service)
  async getSchedules(location?: string, cluster?: string): Promise<Schedule[]> {
    const params: Record<string, string> = {};
    if (location) params.location = location;
    if (cluster) params.cluster = cluster;
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

  async setLightIntensity(location: string, cluster: string, deviceName: string, intensity: number): Promise<LightStatus> {
    const response = await this.automationClient.post(`/api/lights/${location}/${cluster}/${deviceName}/intensity`, {
      intensity
    });
    return response.data;
  }

  async getDevicesForLocationClusterWithDetails(location: string, cluster: string): Promise<Record<string, any>> {
    const response = await this.automationClient.get(`/api/devices/${location}/${cluster}`);
    return response.data.devices || {};
  }

  // Room Schedule (automation service)
  async getRoomSchedule(location: string, cluster: string): Promise<any> {
    const response = await this.automationClient.get(`/api/room-schedule/${location}/${cluster}`);
    return response.data;
  }

  async saveRoomSchedule(location: string, cluster: string, schedule: any): Promise<void> {
    await this.automationClient.post(`/api/room-schedule/${location}/${cluster}`, schedule);
  }

  // Device Control (automation service)
  async controlDevice(
    location: string,
    cluster: string,
    device: string,
    state: number,
    reason?: string
  ): Promise<any> {
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
  ): Promise<any> {
    const response = await this.automationClient.post(
      `/api/devices/${location}/${cluster}/${device}/mode`,
      { mode }
    );
    return response.data;
  }

  // Helper to get all lights for a zone
  async getLightsForZone(location: string, cluster: string): Promise<Array<{ device_name: string; display_name?: string; dimming_enabled?: boolean }>> {
    const devices = await this.getDevicesForLocationClusterWithDetails(location, cluster);
    return Object.entries(devices)
      .filter(([_, device]: [string, any]) => device.device_type === 'light')
      .map(([deviceName, device]: [string, any]) => ({
        device_name: deviceName,
        display_name: device.display_name,
        dimming_enabled: device.dimming_enabled
      }));
  }
}

export const apiClient = new ApiClient();

