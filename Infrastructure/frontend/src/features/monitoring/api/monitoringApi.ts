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
import type { ControlReadScope, ControlMonitoringResponse } from './contracts'

/** Interim shared chart budget; T19 will tune panel-specific budgets. */
export const SENSOR_RANGE_MAX_POINTS = 2000

/** Interim recorded-control-history budget; T19 will tune panel-specific budgets. */
export const CONTROL_HISTORY_MAX_POINTS = 1000

export class MonitoringApi {
  /**
   * `controlScope` selects the backend read scope for recorded control
   * history/tail: room Monitoring/Historical readers pass `lights` (query
   * `scope=lights`, non-light families rejected) while the `all` default —
   * used by Automation and generic callers — keeps the full-history request
   * and contract unchanged.
   */
  constructor(
    requestContext?: MonitoringRequestContext,
    client?: MonitoringClient,
    controlScope: ControlReadScope = 'all'
  ) {
    this.client = client ?? new MonitoringClient(requestContext)
    this.controlScope = controlScope
  }

  private readonly client: MonitoringClient
  private readonly controlScope: ControlReadScope

  sensorRange(
    location: string,
    start?: string,
    end?: string,
    maxPoints?: number,
    options?: MonitoringRequestOptions
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
    options?: MonitoringRequestOptions
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/history${buildQuery({
      start,
      end,
      max_points: maxPoints?.toString(),
      // Scope selection stays explicitly typed; `all` relies on the backend default.
      scope: this.controlScope === 'lights' ? 'lights' : undefined,
    })}`
    return this.client.get(path, controlMonitoringContract(this.controlScope), options)
  }

  controlTail(
    location: string,
    start?: string,
    end?: string,
    options?: MonitoringRequestOptions
  ): Promise<ControlMonitoringResponse> {
    const path = `/api/monitoring/control/${encodeURIComponent(location)}/tail${buildQuery({
      start,
      end,
      max_points: CONTROL_HISTORY_MAX_POINTS.toString(),
      scope: this.controlScope === 'lights' ? 'lights' : undefined,
    })}`
    return this.client.get(path, controlMonitoringContract(this.controlScope), options)
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
