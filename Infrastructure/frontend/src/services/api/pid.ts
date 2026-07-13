/** PID-related API methods (automation service). */
import type { PIDParameters, PIDParameterUpdate, PIDModeInfo, PIDModeUpdate, AutotuneState, PIDHistoryEntry } from '../../types/pid';
import type { ApiClientCore } from '../api';

/** Type contract for PID-related API methods (for declaration merging with ApiClient). */
export interface PidApi {
  getAllPIDParameters(): Promise<Record<string, PIDParameters>>;
  getPIDParameters(deviceType: string): Promise<PIDParameters>;
  updatePIDParameters(deviceType: string, params: PIDParameterUpdate): Promise<PIDParameters>;
  resetPIDParameters(deviceType: string): Promise<PIDParameters>;
  getPIDParameterHistory(deviceType: string, limit?: number): Promise<PIDHistoryEntry[]>;
  getPIDMode(deviceType: string): Promise<PIDModeInfo>;
  setPIDMode(deviceType: string, update: PIDModeUpdate): Promise<PIDModeInfo>;
  getAutotuneStatus(deviceType: string): Promise<AutotuneState>;
  stopAutotune(deviceType: string): Promise<AutotuneState>;
  getPIDParametersForRoom(location: string, cluster: string, deviceType: string): Promise<PIDParameters>;
  updatePIDParametersForRoom(location: string, cluster: string, deviceType: string, params: PIDParameterUpdate): Promise<PIDParameters>;
  getPIDParameterHistoryForRoom(location: string, cluster: string, deviceType: string, limit?: number): Promise<PIDHistoryEntry[]>;
  getPIDModeForRoom(location: string, cluster: string, deviceType: string): Promise<PIDModeInfo>;
  setPIDModeForRoom(location: string, cluster: string, deviceType: string, update: PIDModeUpdate): Promise<PIDModeInfo>;
}

export const pidMethods = {
  async getAllPIDParameters(this: ApiClientCore): Promise<Record<string, PIDParameters>> {
    const response = await this.automationClient.get('/api/pid/parameters');
    return response.data;
  },

  async getPIDParameters(this: ApiClientCore, deviceType: string): Promise<PIDParameters> {
    const response = await this.automationClient.get(`/api/pid/parameters/${deviceType}`);
    return response.data;
  },

  async updatePIDParameters(this: ApiClientCore, deviceType: string, params: PIDParameterUpdate): Promise<PIDParameters> {
    const response = await this.automationClient.post(`/api/pid/parameters/${deviceType}`, params);
    return response.data;
  },

  async resetPIDParameters(this: ApiClientCore, deviceType: string): Promise<PIDParameters> {
    const response = await this.automationClient.post(`/api/pid/parameters/${deviceType}/reset`);
    return response.data;
  },

  async getPIDParameterHistory(this: ApiClientCore, deviceType: string, limit: number = 50): Promise<PIDHistoryEntry[]> {
    const response = await this.automationClient.get(`/api/pid/parameters/${deviceType}/history`, {
      params: { limit }
    });
    return response.data;
  },

  async getPIDMode(this: ApiClientCore, deviceType: string): Promise<PIDModeInfo> {
    const response = await this.automationClient.get(`/api/pid/mode/${deviceType}`);
    return response.data;
  },

  async setPIDMode(this: ApiClientCore, deviceType: string, update: PIDModeUpdate): Promise<PIDModeInfo> {
    const response = await this.automationClient.post(`/api/pid/mode/${deviceType}`, update);
    return response.data;
  },

  async getAutotuneStatus(this: ApiClientCore, deviceType: string): Promise<AutotuneState> {
    const response = await this.automationClient.get(`/api/pid/autotune/${deviceType}/status`);
    return response.data;
  },

  async stopAutotune(this: ApiClientCore, deviceType: string): Promise<AutotuneState> {
    const response = await this.automationClient.post(`/api/pid/autotune/${deviceType}/stop`);
    return response.data;
  },

  async getPIDParametersForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string
  ): Promise<PIDParameters> {
    const response = await this.automationClient.get(
      `/api/pid/parameters/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`
    );
    return response.data;
  },

  async updatePIDParametersForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string,
    params: PIDParameterUpdate
  ): Promise<PIDParameters> {
    const response = await this.automationClient.post(
      `/api/pid/parameters/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`,
      params
    );
    return response.data;
  },

  async getPIDParameterHistoryForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string,
    limit: number = 50
  ): Promise<PIDHistoryEntry[]> {
    const response = await this.automationClient.get(
      `/api/pid/parameters/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}/history`,
      { params: { limit } }
    );
    return response.data;
  },

  async getPIDModeForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string
  ): Promise<PIDModeInfo> {
    const response = await this.automationClient.get(
      `/api/pid/mode/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`
    );
    return response.data;
  },

  async setPIDModeForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string,
    update: PIDModeUpdate
  ): Promise<PIDModeInfo> {
    const response = await this.automationClient.post(
      `/api/pid/mode/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`,
      update
    );
    return response.data;
  },
};
