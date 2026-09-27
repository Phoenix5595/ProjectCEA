/**
 * Typed sensor-registry client over `apiClient.backendClient`.
 *
 * No fourth base URL is introduced: the registry lives on the backend
 * service behind Caddy's `/api/sensors*` route, so requests ride the
 * existing `backendClient` axios instance and every response body is
 * Zod-parsed once at this boundary. Errors keep the server's structured
 * payload (status, message, error_code) so panels can render it inline.
 */
import { isAxiosError } from 'axios'
import { z } from 'zod/v3'

import { apiClient } from '../../../services/api'
import {
  type AssignmentRequest,
  SensorRegistryList,
  SensorRegistryRecord,
  SoilHistoryResponse,
  SoilLiveResponse,
} from './contracts'

export interface ListRegistryParams {
  status?: 'all' | 'assigned' | 'unassigned'
  bus?: 'can' | 'rs485'
}

export interface SoilHistoryParams {
  start: Date
  end: Date
  maxPoints: number
}

/** Structured error shape from the backend APIError handler. */
export interface SoilApiErrorDetail {
  status: number
  message: string
  errorCode: string | null
}

const ApiErrorBody = z.object({
  error: z
    .object({
      status_code: z.number().optional(),
      message: z.string().optional(),
      error_code: z.string().nullish(),
    })
    .optional(),
})

export function extractSoilApiError(error: unknown): SoilApiErrorDetail {
  if (isAxiosError(error)) {
    const parsed = ApiErrorBody.safeParse(error.response?.data)
    const payload = parsed.success ? parsed.data.error : undefined
    return {
      status: error.response?.status ?? 0,
      message: payload?.message ?? error.message,
      errorCode: payload?.error_code ?? null,
    }
  }
  if (error instanceof Error) {
    return { status: 0, message: error.message, errorCode: null }
  }
  return { status: 0, message: String(error), errorCode: null }
}

export const soilApi = {
  async listRegistry(params: ListRegistryParams = {}): Promise<SensorRegistryList> {
    const search = new URLSearchParams()
    if (params.status !== undefined) search.set('status', params.status)
    if (params.bus !== undefined) search.set('bus', params.bus)
    const query = search.toString()
    const response = await apiClient.backendClient.get(
      `/api/sensors/registry${query ? `?${query}` : ''}`,
    )
    return SensorRegistryList.parse(response.data)
  },

  async assign(
    registryId: number,
    body: AssignmentRequest,
  ): Promise<SensorRegistryRecord> {
    const response = await apiClient.backendClient.put(
      `/api/sensors/registry/${registryId}/assignment`,
      body,
    )
    return SensorRegistryRecord.parse(response.data)
  },

  async soilLive(): Promise<SoilLiveResponse> {
    const response = await apiClient.backendClient.get('/api/sensors/soil/live')
    return SoilLiveResponse.parse(response.data)
  },

  async soilHistory(params: SoilHistoryParams): Promise<SoilHistoryResponse> {
    const response = await apiClient.backendClient.get('/api/sensors/soil/history', {
      params: {
        start: params.start.toISOString(),
        end: params.end.toISOString(),
        max_points: params.maxPoints,
      },
    })
    return SoilHistoryResponse.parse(response.data)
  },
}
