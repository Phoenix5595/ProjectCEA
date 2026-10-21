/** Schedule-related API methods (automation service). */
import type { Schedule } from '../../types/schedule'
import type { ApiClientCore } from '../api'

/** Opaque JSON object returned by backend endpoints that don't have a typed contract yet. */
type JsonObject = Record<string, unknown>

/** Type contract for schedule-related API methods (for declaration merging with ApiClient). */
export interface ScheduleApi {
  getSchedules(location?: string, cluster?: string): Promise<Schedule[]>
  getClimatePeriods(
    location: string,
    cluster: string,
    modeId?: number | null,
    submodeId?: number | null
  ): Promise<JsonObject[]>
}

export const scheduleMethods = {
  // Schedules CRUD
  async getSchedules(
    this: ApiClientCore,
    location?: string,
    cluster?: string
  ): Promise<Schedule[]> {
    const params: Record<string, string> = {}
    if (location) params.location = location
    if (cluster) params.cluster = cluster
    const response = await this.automationClient.get('/api/schedules', { params })
    return response.data
  },

  async getClimatePeriods(
    this: ApiClientCore,
    location: string,
    cluster: string,
    modeId?: number | null,
    submodeId?: number | null
  ): Promise<JsonObject[]> {
    const params: Record<string, number> = {}
    if (modeId != null) {
      params.mode_id = modeId
      if (submodeId != null) {
        params.submode_id = submodeId
      }
    }
    const response = await this.automationClient.get(
      `/api/climate-periods/${location}/${cluster}`,
      {
        params: Object.keys(params).length > 0 ? params : undefined,
      }
    )
    return response.data
  },

}
