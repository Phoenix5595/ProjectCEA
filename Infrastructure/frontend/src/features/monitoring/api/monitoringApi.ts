import { z } from 'zod/v3'
import { buildQuery, MonitoringClient } from './client'
import type { MonitoringRequestContext, MonitoringRequestOptions } from './client'
import {
  ControlMonitoringResponse,
  LiveSensorValue,
  MonitoringResponse,
} from './contracts'

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
    options?: MonitoringRequestOptions,
  ): Promise<MonitoringResponse> {
    const path = `/api/sensors/monitoring/range/${encodeURIComponent(location)}${buildQuery({ start, end })}`
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
    options?: MonitoringRequestOptions,
  ): Promise<MonitoringResponse> {
    const path = `/api/sensors/monitoring/stats/${encodeURIComponent(location)}${buildQuery({ start, end })}`
    return this.client.get(path, MonitoringResponse, options)
  }

  controlRange(
    location: string,
    start?: string,
    end?: string,
    options?: MonitoringRequestOptions,
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/history${buildQuery({ start, end })}`
    return this.client.get(path, ControlMonitoringResponse, options)
  }

  controlProjection(
    location: string,
    start?: string,
    end?: string,
    options?: MonitoringRequestOptions,
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/projection${buildQuery({ start, end })}`
    return this.client.get(path, ControlMonitoringResponse, options)
  }
}
