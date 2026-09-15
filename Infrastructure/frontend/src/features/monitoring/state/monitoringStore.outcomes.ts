import { MonitoringAbortError } from '../api'
import type { MonitoringRangeSource, MonitoringSourceFailure, MonitoringSourceSuccess } from './monitoringStore.health'
import { applySourceFailure, applySourceSuccess, deriveActiveSourceErrors, deriveRangeFreshness } from './monitoringStore.health'
import type { StoreState } from './monitoringStore.types'

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : String(reason)
}

export function applySourceSuccessToState(
  state: StoreState,
  success: MonitoringSourceSuccess,
): StoreState {
  const sourceOutcomes = applySourceSuccess(state.sourceOutcomes, success)
  return {
    ...state,
    sourceOutcomes,
    errors: deriveActiveSourceErrors(sourceOutcomes).map(({ message }) => message),
    ...deriveRangeFreshness(sourceOutcomes),
  }
}

export function applySourceFailureToState(
  state: StoreState,
  failure: MonitoringSourceFailure,
): StoreState {
  const sourceOutcomes = applySourceFailure(state.sourceOutcomes, failure)
  return {
    ...state,
    sourceOutcomes,
    errors: deriveActiveSourceErrors(sourceOutcomes).map(({ message }) => message),
    ...deriveRangeFreshness(sourceOutcomes),
  }
}

export function applySettledSource<T>(
  state: StoreState,
  source: MonitoringRangeSource,
  result: PromiseSettledResult<T>,
  completedAt: Date,
): { readonly state: StoreState; readonly value: T | null } {
  if (result.status === 'fulfilled') {
    return {
      state: applySourceSuccessToState(state, { source, lastGoodAt: completedAt }),
      value: result.value,
    }
  }
  if (result.reason instanceof MonitoringAbortError) return { state, value: null }
  return {
    state: applySourceFailureToState(state, {
      source,
      message: errorMessage(result.reason),
      errorAt: completedAt,
    }),
    value: null,
  }
}
