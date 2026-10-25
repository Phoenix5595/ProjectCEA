import { z } from 'zod'

import { RelayTimelineResponse } from '../../relay-timeline/contracts'

import { buildQuery, MonitoringClient } from './client'
import type { MonitoringRequestContext, MonitoringRequestOptions } from './client'
import {
  controlMonitoringContract,
  CurrentPublicationResponse,
  LiveSensorValue,
  MonitoringResponse,
  ProjectionPublicationResponse,
} from './contracts'
import type { ControlHistoryScope, ControlMonitoringResponse } from './contracts'

/** Extra range options: an explicit bucket interval for incremental reads. */
export interface SensorRangeOptions extends MonitoringRequestOptions {
  /** Must be one of the backend's nice intervals; only sent with `maxPoints`. */
  readonly intervalSeconds?: number
}

export class MonitoringApi {
  /**
   * `controlScope` selects the backend read scope for recorded control
   * history/tail when a call does not pass one explicitly: room
   * Monitoring/Historical readers pass `room` while the `all` default — used
   * by Automation and generic callers — keeps the full-history request and
   * contract unchanged. Overview endpoints keep their own `all | lights`
   * scope and do not go through this class's history/tail methods.
   */
  constructor(
    requestContext?: MonitoringRequestContext,
    client?: MonitoringClient,
    controlScope: ControlHistoryScope = 'all'
  ) {
    this.client = client ?? new MonitoringClient(requestContext)
    this.controlScope = controlScope
  }

  private readonly client: MonitoringClient
  private readonly controlScope: ControlHistoryScope

  sensorRange(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    options?: SensorRangeOptions
  ): Promise<MonitoringResponse> {
    const path = `/api/sensors/monitoring/range/${encodeURIComponent(location)}${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
      interval_seconds:
        maxPoints === undefined ? undefined : options?.intervalSeconds?.toString(),
    })}`
    const requestOptions: MonitoringRequestOptions | undefined =
      options === undefined
        ? undefined
        : { signal: options.signal, timeoutMs: options.timeoutMs }
    return this.client.get(path, MonitoringResponse, requestOptions)
  }

  sensorLive(
    location: string,
    node: string,
    options?: MonitoringRequestOptions
  ): Promise<LiveSensorValue[]> {
    const path = `/api/sensors/monitoring/live/${encodeURIComponent(location)}/${encodeURIComponent(node)}`
    return this.client.get(path, z.array(LiveSensorValue), options)
  }

  controlRange(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    scope: ControlHistoryScope = this.controlScope,
    options?: MonitoringRequestOptions
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/history${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
      // `all` relies on the backend default; every other scope is explicit.
      scope: scope === 'all' ? undefined : scope,
    })}`
    return this.client.get(path, controlMonitoringContract(scope), options)
  }

  controlTail(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    scope: ControlHistoryScope = this.controlScope,
    options?: MonitoringRequestOptions
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/tail${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
      scope: scope === 'all' ? undefined : scope,
    })}`
    return this.client.get(path, controlMonitoringContract(scope), options)
  }

  controlProjection(
    location: string,
    options?: MonitoringRequestOptions
  ): Promise<ProjectionPublicationResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/projection`
    return this.client.get(path, ProjectionPublicationResponse, options)
  }

  /** Fresh current publication for one room; read-only paired-version route. */
  controlCurrent(
    location: string,
    options?: MonitoringRequestOptions
  ): Promise<CurrentPublicationResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/current`
    return this.client.get(path, CurrentPublicationResponse, options)
  }

  relayTimeline(
    location: string,
    start: string,
    end: string,
    limit = 2_000,
    cursor?: string | null,
    options?: MonitoringRequestOptions
  ): Promise<RelayTimelineResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/relay-timeline${buildQuery(
      {
        start,
        end,
        limit: limit.toString(),
        cursor: cursor ?? undefined,
      }
    )}`
    return this.client.get(path, RelayTimelineResponse, options)
  }
}
