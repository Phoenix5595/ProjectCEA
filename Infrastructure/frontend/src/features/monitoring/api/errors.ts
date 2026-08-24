/**
 * Distinguishable monitoring API error variants.
 *
 * Every failure that crosses the monitoring feature boundary is a
 * `MonitoringApiError` with a `kind` discriminator, so callers can branch on
 * the failure class (network vs timeout vs backend/automation HTTP vs parse vs
 * validation vs topology vs caller abort) without inspecting message strings.
 */
export type MonitoringServiceName = 'monitoring'

export type MonitoringErrorKind =
  | 'network'
  | 'timeout'
  | 'http'
  | 'parse'
  | 'validation'
  | 'topology'
  | 'aborted'

export class MonitoringApiError extends Error {
  readonly kind: MonitoringErrorKind

  constructor(kind: MonitoringErrorKind, message: string) {
    super(message)
    this.name = 'MonitoringApiError'
    this.kind = kind
  }
}

export class MonitoringNetworkError extends MonitoringApiError {
  constructor(message: string) {
    super('network', message)
    this.name = 'MonitoringNetworkError'
  }
}

export class MonitoringTimeoutError extends MonitoringApiError {
  constructor(message: string) {
    super('timeout', message)
    this.name = 'MonitoringTimeoutError'
  }
}

export class MonitoringHttpError extends MonitoringApiError {
  readonly status: number
  readonly service: MonitoringServiceName

  constructor(service: MonitoringServiceName, status: number, message: string) {
    super('http', message)
    this.name = 'MonitoringHttpError'
    this.status = status
    this.service = service
  }
}

/** The response body was not valid JSON or failed its Zod contract. */
export class MonitoringParseError extends MonitoringApiError {
  constructor(message: string) {
    super('parse', message)
    this.name = 'MonitoringParseError'
  }
}

/** A request argument violated a monitoring contract (e.g. bad range). */
export class MonitoringValidationError extends MonitoringApiError {
  constructor(message: string) {
    super('validation', message)
    this.name = 'MonitoringValidationError'
  }
}

/** A room/node/cluster is outside the monitoring feature scope. */
export class MonitoringTopologyError extends MonitoringApiError {
  constructor(message: string) {
    super('topology', message)
    this.name = 'MonitoringTopologyError'
  }
}

/** The caller aborted the request through its AbortSignal. */
export class MonitoringAbortError extends MonitoringApiError {
  constructor(message: string) {
    super('aborted', message)
    this.name = 'MonitoringAbortError'
  }
}
