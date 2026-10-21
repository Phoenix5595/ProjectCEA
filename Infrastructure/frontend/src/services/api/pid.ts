/** PID-related API methods (automation service). */
import type {
  PIDParameters,
  PIDParameterUpdate,
  PIDModeInfo,
  PIDModeUpdate,
  PIDHistoryEntry,
} from '../../types/pid'
import type { ApiClientCore } from '../api'

/** Type contract for PID-related API methods (for declaration merging with ApiClient). */
export interface PidApi {
  getPIDParametersForRoom(
    location: string,
    cluster: string,
    deviceType: string
  ): Promise<PIDParameters>
  updatePIDParametersForRoom(
    location: string,
    cluster: string,
    deviceType: string,
    params: PIDParameterUpdate
  ): Promise<PIDParameters>
  getPIDParameterHistoryForRoom(
    location: string,
    cluster: string,
    deviceType: string,
    limit?: number
  ): Promise<PIDHistoryEntry[]>
  getPIDModeForRoom(location: string, cluster: string, deviceType: string): Promise<PIDModeInfo>
  setPIDModeForRoom(
    location: string,
    cluster: string,
    deviceType: string,
    update: PIDModeUpdate
  ): Promise<PIDModeInfo>
}

export const pidMethods = {
  async getPIDParametersForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string
  ): Promise<PIDParameters> {
    const response = await this.automationClient.get(
      `/api/pid/parameters/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`
    )
    return response.data
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
    )
    return response.data
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
    )
    return response.data
  },

  async getPIDModeForRoom(
    this: ApiClientCore,
    location: string,
    cluster: string,
    deviceType: string
  ): Promise<PIDModeInfo> {
    const response = await this.automationClient.get(
      `/api/pid/mode/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(deviceType)}`
    )
    return response.data
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
    )
    return response.data
  },
}
