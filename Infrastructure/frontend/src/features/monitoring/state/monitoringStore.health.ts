const MONITORING_RANGE_SOURCES = ['sensor-history', 'control-history', 'projection'] as const

export type MonitoringRangeSource = (typeof MONITORING_RANGE_SOURCES)[number]

type MonitoringRangeOutcomeFor<Source extends MonitoringRangeSource> =
  | {
      readonly source: Source
      readonly status: 'idle'
      readonly lastGoodAt: null
    }
  | {
      readonly source: Source
      readonly status: 'healthy'
      readonly lastGoodAt: Date
    }
  | {
      readonly source: Source
      readonly status: 'failed'
      readonly lastGoodAt: Date | null
      readonly message: string
      readonly errorAt: Date
    }

export type MonitoringRangeOutcome = MonitoringRangeOutcomeFor<MonitoringRangeSource>

export type MonitoringSourceOutcomes = {
  readonly [source in MonitoringRangeSource]: MonitoringRangeOutcomeFor<source>
}

export type MonitoringSourceSuccess = {
  readonly source: MonitoringRangeSource
  readonly lastGoodAt: Date
}

export type MonitoringSourceFailure = {
  readonly source: MonitoringRangeSource
  readonly message: string
  readonly errorAt: Date
}

export type MonitoringSourceError = {
  readonly source: MonitoringRangeSource
  readonly message: string
  readonly errorAt: Date
}

export type MonitoringRangeFreshness = {
  readonly lastGoodRangeAt: Date | null
  readonly rangeErrorAt: Date | null
}

function idleOutcome<Source extends MonitoringRangeSource>(source: Source): MonitoringRangeOutcomeFor<Source> {
  return Object.freeze({ source, status: 'idle', lastGoodAt: null })
}

export function createIdleSourceOutcomes(): MonitoringSourceOutcomes {
  return Object.freeze({
    'sensor-history': idleOutcome('sensor-history'),
    'control-history': idleOutcome('control-history'),
    projection: idleOutcome('projection'),
  })
}

export function applySourceSuccess(
  outcomes: MonitoringSourceOutcomes,
  success: MonitoringSourceSuccess,
): MonitoringSourceOutcomes {
  return Object.freeze({
    ...outcomes,
    [success.source]: Object.freeze({
      source: success.source,
      status: 'healthy',
      lastGoodAt: success.lastGoodAt,
    }),
  })
}

export function applySourceFailure(
  outcomes: MonitoringSourceOutcomes,
  failure: MonitoringSourceFailure,
): MonitoringSourceOutcomes {
  return Object.freeze({
    ...outcomes,
    [failure.source]: Object.freeze({
      source: failure.source,
      status: 'failed',
      lastGoodAt: outcomes[failure.source].lastGoodAt,
      message: failure.message,
      errorAt: failure.errorAt,
    }),
  })
}

export function deriveActiveSourceErrors(
  outcomes: MonitoringSourceOutcomes,
): readonly MonitoringSourceError[] {
  const errors: MonitoringSourceError[] = []
  for (const source of MONITORING_RANGE_SOURCES) {
    const outcome = outcomes[source]
    switch (outcome.status) {
      case 'idle':
      case 'healthy':
        break
      case 'failed':
        errors.push(Object.freeze({ source, message: outcome.message, errorAt: outcome.errorAt }))
        break
      default: {
        const exhaustiveOutcome: never = outcome
        return exhaustiveOutcome
      }
    }
  }
  return Object.freeze(errors)
}

export function deriveRangeFreshness(
  outcomes: MonitoringSourceOutcomes,
): MonitoringRangeFreshness {
  const sensorLastGoodAt = outcomes['sensor-history'].lastGoodAt
  const controlLastGoodAt = outcomes['control-history'].lastGoodAt
  const lastGoodRangeAt = sensorLastGoodAt === null || controlLastGoodAt === null
    ? null
    : new Date(Math.min(sensorLastGoodAt.getTime(), controlLastGoodAt.getTime()))

  const historicalErrors = deriveActiveSourceErrors(outcomes).filter(
    ({ source }) => source === 'sensor-history' || source === 'control-history',
  )
  const rangeErrorAt = historicalErrors.length === 0
    ? null
    : new Date(Math.max(...historicalErrors.map(({ errorAt }) => errorAt.getTime())))

  return Object.freeze({ lastGoodRangeAt, rangeErrorAt })
}
