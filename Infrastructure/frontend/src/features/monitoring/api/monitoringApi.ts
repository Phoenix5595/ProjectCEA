import { z } from 'zod/v3'
import { buildQuery, MonitoringClient } from './client'
import type { MonitoringRequestContext, MonitoringRequestOptions } from './client'
import {
  ControlMonitoringResponse,
  LiveSensorValue,
  MonitoringResponse,
  ProjectionPublicationResponse,
} from './contracts'

/** Interim shared chart budget; T19 will tune panel-specific budgets. */
export const SENSOR_RANGE_MAX_POINTS = 2000

/** Interim recorded-control-history budget; T19 will tune panel-specific budgets. */
export const CONTROL_HISTORY_MAX_POINTS = 1000

export class MonitoringApi {
  constructor(
    requestContext?: MonitoringRequestContext,
    client?: MonitoringClient,
  ) {
    this.client = client ?? new MonitoringClient(requestContext)
  }

  private readonly client: MonitoringClient

  sensorRange(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    options?: MonitoringRequestOptions,
  ): Promise<MonitoringResponse> {
    const path = `/api/sensors/monitoring/range/${encodeURIComponent(location)}${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
    })}`
    return this.client.get(path, MonitoringResponse, options)
  }

  sensorLive(
    location: string,
    node: string,
    options?: MonitoringRequestOptions,
  ): Promise<LiveSensorValue[]> {
    const path = `/api/sensors/monitoring/live/${encodeURIComponent(location)}/${encodeURIComponent(node)}`
    return this.client.get(path, z.array(LiveSensorValue), options)
  }

  sensorStats(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    options?: MonitoringRequestOptions,
  ): Promise<MonitoringResponse> {
    const path = `/api/sensors/monitoring/stats/${encodeURIComponent(location)}${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
    })}`
    return this.client.get(path, MonitoringResponse, options)
  }

  controlRange(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    options?: MonitoringRequestOptions,
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/history${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
    })}`
    return this.client.get(path, ControlMonitoringResponse, options)
  }

  controlTail(
    location: string,
    start?: string,
    end?: string,
    options?: MonitoringRequestOptions,
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/tail${buildQuery({
      start,
      end,
      max_points: CONTROL_HISTORY_MAX_POINTS.toString(),
    })}`
    return this.client.get(path, ControlMonitoringResponse, options)
  }

  controlProjection(
    location: string,
    options?: MonitoringRequestOptions,
  ): Promise<ProjectionPublicationResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/projection`
    return this.client.get(path, ProjectionPublicationResponse, options)
  }
}
