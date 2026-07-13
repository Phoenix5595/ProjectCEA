/** Schedule-related API methods (automation service). */
import type { Schedule, ScheduleCreate, ScheduleUpdate } from '../../types/schedule';
import type { ApiClientCore } from '../api';

/** Opaque JSON object returned by backend endpoints that don't have a typed contract yet. */
type JsonObject = Record<string, unknown>;

/** Type contract for schedule-related API methods (for declaration merging with ApiClient). */
export interface ScheduleApi {
  getSchedules(location?: string, cluster?: string): Promise<Schedule[]>;
  getSchedulesForDevice(location: string, cluster: string, deviceName: string): Promise<Schedule[]>;
  createSchedule(schedule: ScheduleCreate): Promise<Schedule>;
  updateSchedule(scheduleId: number, schedule: ScheduleUpdate): Promise<Schedule>;
  deleteSchedule(scheduleId: number): Promise<void>;
  getClimateSchedule(location: string, cluster: string): Promise<JsonObject>;
  saveClimateSchedule(location: string, cluster: string, schedule: JsonObject): Promise<JsonObject>;
  getClimatePeriods(location: string, cluster: string, modeId?: number | null, submodeId?: number | null): Promise<JsonObject[]>;
  saveClimatePeriods(location: string, cluster: string, periods: JsonObject[], modeId?: number, submodeId?: number): Promise<JsonObject>;
  getRoomSchedule(location: string, cluster: string): Promise<JsonObject>;
  saveRoomSchedule(location: string, cluster: string, schedule: JsonObject): Promise<void>;
}

export const scheduleMethods = {
  // Schedules CRUD
  async getSchedules(this: ApiClientCore, location?: string, cluster?: string): Promise<Schedule[]> {
    const params: Record<string, string> = {};
    if (location) params.location = location;
    if (cluster) params.cluster = cluster;
    const response = await this.automationClient.get('/api/schedules', { params });
    return response.data;
  },

  async getSchedulesForDevice(this: ApiClientCore, location: string, cluster: string, deviceName: string): Promise<Schedule[]> {
    const params: Record<string, string> = { location, cluster, device_name: deviceName };
    const response = await this.automationClient.get('/api/schedules', { params });
    return response.data;
  },

  async createSchedule(this: ApiClientCore, schedule: ScheduleCreate): Promise<Schedule> {
    const response = await this.automationClient.post('/api/schedules', schedule);
    return response.data;
  },

  async updateSchedule(this: ApiClientCore, scheduleId: number, schedule: ScheduleUpdate): Promise<Schedule> {
    const response = await this.automationClient.put(`/api/schedules/${scheduleId}`, schedule);
    return response.data;
  },

  async deleteSchedule(this: ApiClientCore, scheduleId: number): Promise<void> {
    await this.automationClient.delete(`/api/schedules/${scheduleId}`);
  },

  // Climate schedule
  async getClimateSchedule(this: ApiClientCore, location: string, cluster: string): Promise<JsonObject> {
    const response = await this.automationClient.get(`/api/climate-schedule/${location}/${cluster}`);
    return response.data;
  },

  async saveClimateSchedule(this: ApiClientCore, location: string, cluster: string, schedule: JsonObject): Promise<JsonObject> {
    const response = await this.automationClient.post(`/api/climate-schedule/${location}/${cluster}`, schedule);
    return response.data;
  },

  async getClimatePeriods(
    this: ApiClientCore,
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
  },

  async saveClimatePeriods(this: ApiClientCore, location: string, cluster: string, periods: JsonObject[], modeId?: number, submodeId?: number): Promise<JsonObject> {
    const response = await this.automationClient.post(`/api/climate-periods/${location}/${cluster}`, { 
      periods,
      mode_id: modeId ?? null,
      submode_id: submodeId ?? null
    });
    return response.data;
  },

  // Room schedule (lights)
  async getRoomSchedule(this: ApiClientCore, location: string, cluster: string): Promise<JsonObject> {
    const response = await this.automationClient.get(`/api/room-schedule/${location}/${cluster}`);
    return response.data;
  },

  async saveRoomSchedule(this: ApiClientCore, location: string, cluster: string, schedule: JsonObject): Promise<void> {
    await this.automationClient.post(`/api/room-schedule/${location}/${cluster}`, schedule);
  },
};
