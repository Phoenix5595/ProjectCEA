import type { components } from '../../generated/api'
import type { ApiClientCore } from '../api'

export type ActiveAlarmResponse = components['schemas']['ActiveAlarmResponse']
export type AlarmListResponse = components['schemas']['AlarmListResponse']
export type AlarmAcknowledgeResponse = components['schemas']['AlarmAcknowledgeResponse']

export interface AlarmApi {
  getActiveAlarms(): Promise<AlarmListResponse>
  acknowledgeAlarm(
    location: string,
    cluster: string,
    alarmName: string
  ): Promise<AlarmAcknowledgeResponse>
}

export const alarmMethods = {
  async getActiveAlarms(this: ApiClientCore): Promise<AlarmListResponse> {
    const response = await this.automationClient.get('/api/alarms')
    return response.data
  },

  async acknowledgeAlarm(
    this: ApiClientCore,
    location: string,
    cluster: string,
    alarmName: string
  ): Promise<AlarmAcknowledgeResponse> {
    const response = await this.automationClient.post(
      `/api/alarms/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}/${encodeURIComponent(alarmName)}/acknowledge`
    )
    return response.data
  },
}
