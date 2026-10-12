/**
 * Bounded all-history overview API client.
 *
 * Mirrors the monitoring-service fixed-range routes with an 8-second read
 * budget and the shared request/abort/error conventions. Responses are parsed
 * through the request-bound overview contracts so a payload that disagrees
 * with the requested range/budget is rejected before any caller sees it, and
 * an invalid or unfit range/budget pair is rejected through the shared picker
 * before any request is issued.
 */
import { MonitoringClient, buildQuery } from '../monitoring/api'
import type {
  ControlReadScope,
  MonitoringRequestContext,
  MonitoringRequestOptions,
} from '../monitoring/api'

import { chooseHistoryBucket } from './buckets'
import { historyOverviewControlContract, historyOverviewSensorContract } from './contracts'
import type {
  HistoryOverviewControlResponse,
  HistoryOverviewRequest,
  HistoryOverviewSensorResponse,
} from './contracts'

/**
 * UI default request budget: the overview API cap (1000 points), about one
 * point per plot pixel on the lowest supported 1280px viewport (~990px plot).
 * The backend keeps its generic 600-point default for non-UI callers; every
 * UI call requests this explicit budget.
 */
export const HISTORY_OVERVIEW_MAX_POINTS = 1000

const OVERVIEW_TIMEOUT_MS = 8000

function overviewRequest(
  range: { start: Date; end: Date },
  maxPoints: number
): HistoryOverviewRequest {
  return { range: { start: range.start, end: range.end }, maxPoints }
}

export class HistoryOverviewApi {
  /**
   * `controlScope` selects the overview read scope: room Monitoring/Historical
   * loaders pass `lights` (query `scope=lights`, populated non-light groups
   * rejected) while the `all` default keeps the full automation read for
   * relay/PID pages.
   */
  constructor(
    requestContext?: MonitoringRequestContext,
    client?: MonitoringClient,
    defaultTimeoutMs = OVERVIEW_TIMEOUT_MS,
    controlScope: ControlReadScope = 'all'
  ) {
    this.client = client ?? new MonitoringClient(requestContext, defaultTimeoutMs)
    this.controlScope = controlScope
  }

  private readonly client: MonitoringClient
  private readonly controlScope: ControlReadScope

  async sensor(
    room: string,
    range: { start: Date; end: Date },
    maxPoints: number = HISTORY_OVERVIEW_MAX_POINTS,
    options?: MonitoringRequestOptions
  ): Promise<HistoryOverviewSensorResponse> {
    // Shared picker as the single query validator: an invalid range or a
    // range/budget pair that cannot fit the 12-hour cap rejects here with the
    // same monitoring 400 detail, before any doomed fetch is issued.
    chooseHistoryBucket(range.start, range.end, maxPoints)
    const path = `/api/sensors/monitoring/overview/${encodeURIComponent(room)}${buildQuery({
      start: range.start.toISOString(),
      end: range.end.toISOString(),
      max_points: maxPoints.toString(),
    })}`
    return this.client.get(
      path,
      historyOverviewSensorContract(overviewRequest(range, maxPoints)),
      options
    )
  }

  async control(
    room: string,
    range: { start: Date; end: Date },
    maxPoints: number = HISTORY_OVERVIEW_MAX_POINTS,
    options?: MonitoringRequestOptions
  ): Promise<HistoryOverviewControlResponse> {
    chooseHistoryBucket(range.start, range.end, maxPoints)
    const path = `/api/monitoring/control/${encodeURIComponent(room)}/overview${buildQuery({
      start: range.start.toISOString(),
      end: range.end.toISOString(),
      max_points: maxPoints.toString(),
      scope: this.controlScope === 'lights' ? 'lights' : undefined,
    })}`
    const request = overviewRequest(range, maxPoints)
    return this.client.get(
      path,
      historyOverviewControlContract(request, this.controlScope),
      options
    )
  }
}
