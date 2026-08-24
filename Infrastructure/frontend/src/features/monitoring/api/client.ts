import { z } from 'zod/v3'
import { CEA_API_KEY, MONITORING_API_URL } from '../../../config/env'
import {
  MonitoringAbortError,
  MonitoringHttpError,
  MonitoringNetworkError,
  MonitoringParseError,
  MonitoringTimeoutError,
} from './errors'
import {
  finishMonitoringRequest,
  PERFORMANCE_MARKS_ENABLED,
  startMonitoringRequest,
} from '../perfMarks'

export interface MonitoringRequestContext {
  readonly scenario?: string
  readonly fixtureSession?: string
}

export function monitoringRequestContextFromSearchParams(
  searchParams: URLSearchParams,
): MonitoringRequestContext | undefined {
  const scenario = searchParams.get('scenario') ?? undefined
  const fixtureSession = searchParams.get('fixtureSession') ?? undefined
  if (scenario === undefined && fixtureSession === undefined) return undefined
  return Object.freeze({ scenario, fixtureSession })
}

export interface MonitoringRequestOptions {
  signal?: AbortSignal
  timeoutMs?: number
}

export function buildQuery(params: Record<string, string | undefined>): string {
  const parts: string[] = []
  for (const key of Object.keys(params)) {
    const value = params[key]
    if (value === undefined) continue
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
  }
  return parts.length === 0 ? '' : `?${parts.join('&')}`
}

const SERVICE_NAME = 'monitoring'

export class MonitoringClient {
  private readonly defaultTimeoutMs: number

  constructor(
    private readonly requestContext?: MonitoringRequestContext,
    defaultTimeoutMs = 45000,
  ) {
    this.defaultTimeoutMs = defaultTimeoutMs
  }

  async get<TSchema extends z.ZodTypeAny>(
    path: string,
    schema: TSchema,
    options: MonitoringRequestOptions = {},
  ): Promise<z.infer<TSchema>> {
    const base = `${MONITORING_API_URL}${path}`
    const url = this.requestContext
      ? `${base}${base.includes('?') ? '&' : '?'}${buildQuery({
          scenario: this.requestContext.scenario,
          fixtureSession: this.requestContext.fixtureSession,
        }).slice(1)}`
      : base
    const timeoutMs = options.timeoutMs ?? this.defaultTimeoutMs

    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)
    const onExternalAbort = (): void => controller.abort()
    options.signal?.addEventListener('abort', onExternalAbort, { once: true })

    let response: Response
    const requestStartedAt = PERFORMANCE_MARKS_ENABLED ? startMonitoringRequest() : undefined
    try {
      response = await fetch(url, {
        method: 'GET',
        signal: controller.signal,
        headers: this.headers(),
      })
    } catch (error) {
      if (options.signal?.aborted) {
        throw new MonitoringAbortError('request aborted by caller')
      }
      if (controller.signal.aborted) {
        throw new MonitoringTimeoutError(
          `request to ${SERVICE_NAME} timed out after ${timeoutMs}ms`,
        )
      }
      throw new MonitoringNetworkError(
        `network error reaching ${SERVICE_NAME}: ${String(error)}`,
      )
    } finally {
      if (requestStartedAt !== undefined) finishMonitoringRequest(requestStartedAt)
      clearTimeout(timeoutId)
      options.signal?.removeEventListener('abort', onExternalAbort)
    }

    if (!response.ok) {
      throw new MonitoringHttpError(
        SERVICE_NAME,
        response.status,
        `HTTP ${response.status} from ${SERVICE_NAME} for ${path}`,
      )
    }

    let json: unknown
    try {
      json = await response.json()
    } catch (error) {
      throw new MonitoringParseError(
        `response from ${SERVICE_NAME} was not valid JSON: ${String(error)}`,
      )
    }

    const result = schema.safeParse(json)
    if (!result.success) {
      throw new MonitoringParseError(
        `response from ${SERVICE_NAME} failed contract validation: ${result.error.message}`,
      )
    }
    return result.data
  }

  private headers(): Record<string, string> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (CEA_API_KEY) headers['X-API-Key'] = CEA_API_KEY
    return headers
  }
}
