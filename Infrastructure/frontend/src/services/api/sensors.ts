/** Sensor-related API methods (backend service). */
import type { SensorDataResponse } from '../../types/sensor';
import type { ApiClientCore } from '../api';

export interface SensorApi {
  getLiveSensorData(location: string, cluster: string): Promise<SensorDataResponse>;
  getSensorDataBulk(keys: string[]): Promise<Record<string, number>>;
  getAllLiveSensorData(): Promise<Array<{ sensor: string; value: number; time: string; unit: string }>>;
}

export const sensorMethods = {
  async getLiveSensorData(this: ApiClientCore, location: string, cluster: string): Promise<SensorDataResponse> {
    const response = await this.backendClient.get(`/api/sensors/${location}/${cluster}/live`);
    return response.data;
  },

  async getSensorDataBulk(this: ApiClientCore, keys: string[]): Promise<Record<string, number>> {
    const response = await this.backendClient.post('/api/sensor-data', { keys });
    return response.data ?? {};
  },

  async getAllLiveSensorData(this: ApiClientCore): Promise<Array<{ sensor: string; value: number; time: string; unit: string }>> {
    const response = await this.backendClient.get('/api/sensors/live/all');
    return Array.isArray(response.data) ? response.data : [];
  },
};
