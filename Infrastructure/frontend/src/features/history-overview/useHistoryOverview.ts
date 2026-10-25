/**
 * Fixed-range history overview loader.
 *
 * One hook generation per (room, range, budget) triple: it fires the bounded
 * sensor/control overview GETs plus the recorded climate/photoperiod read
 * once, aborts them on change/unmount, ignores stale resolves, and never
 * polls or falls back to fabricated values. The triple is atomic — all three
 * either publish together or surface the first failure as an explicit error
 * string with a manual `retry`.
 *
 * The hook fires only after the view has measured a request budget (R1): the
 * first load waits for the placeholder/first-chart measurement.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type { ControlMonitoringResponse, MonitoringRequestContext } from '../monitoring/api'
import type { FixedRange } from '../monitoring/state'

import { HistoryOverviewApi } from './api'
import type { ControlOverviewScope } from '../monitoring/api'
import type { HistoryOverviewControlResponse, HistoryOverviewSensorResponse } from './contracts'

export interface UseHistoryOverviewOptions {
  readonly room: string
  readonly range: FixedRange
  readonly requestContext?: MonitoringRequestContext
  /** Integer request budget (one point per plot pixel); null defers loading. */
  readonly maxPoints?: number | null
  /** Load only the control overview (relay/PID-lane pages). */
  readonly controlOnly?: boolean
}

export interface UseHistoryOverviewResult {
  readonly sensor: HistoryOverviewSensorResponse | null
  readonly control: HistoryOverviewControlResponse | null
  readonly climate: ControlMonitoringResponse | null
  readonly loading: boolean
  readonly error: string | null
  readonly retry: () => void
}

interface OverviewGenerationState {
  readonly requestKey: string | null
  readonly sensor: HistoryOverviewSensorResponse | null
  readonly control: HistoryOverviewControlResponse | null
  readonly climate: ControlMonitoringResponse | null
  readonly loading: boolean
  readonly error: string | null
}

const EMPTY_GENERATION: OverviewGenerationState = {
  requestKey: null,
  sensor: null,
  control: null,
  climate: null,
  loading: false,
  error: null,
}

export function useHistoryOverview(options: UseHistoryOverviewOptions): UseHistoryOverviewResult {
  const { room, range, maxPoints, controlOnly } = options
  // Room Monitoring/Historical reads lights only; the automation relay/PID
  // page (`controlOnly`) keeps the full `all` overview read.
  const controlScope: ControlOverviewScope = controlOnly ? 'all' : 'lights'
  const scenario = options.requestContext?.scenario
  const fixtureSession = options.requestContext?.fixtureSession
  const rangeStartMs = range.start.getTime()
  const rangeEndMs = range.end.getTime()
  const budget = maxPoints ?? null

  const [generation, setGeneration] = useState(0)
  const [state, setState] = useState<OverviewGenerationState>(EMPTY_GENERATION)
  const generationRef = useRef(0)
  const requestKey = useMemo(
    () =>
      JSON.stringify([
        room,
        rangeStartMs,
        rangeEndMs,
        budget,
        !!controlOnly,
        scenario,
        fixtureSession,
        generation,
      ]),
    [room, rangeStartMs, rangeEndMs, budget, controlOnly, scenario, fixtureSession, generation]
  )

  const retry = useCallback(() => setGeneration(value => value + 1), [])

  useEffect(() => {
    const activeGeneration = generationRef.current + 1
    generationRef.current = activeGeneration
    if (budget === null) return () => {
      generationRef.current += 1
    }
    const controller = new AbortController()
    setState({ requestKey, sensor: null, control: null, climate: null, loading: true, error: null })

    const api = new HistoryOverviewApi(
      scenario === undefined && fixtureSession === undefined
        ? undefined
        : Object.freeze({ scenario, fixtureSession }),
      undefined,
      undefined,
      controlScope
    )
    const requestRange = { start: new Date(rangeStartMs), end: new Date(rangeEndMs) }
    const requestOptions = { signal: controller.signal }
    const sensorRequest: Promise<HistoryOverviewSensorResponse | null> = controlOnly
      ? Promise.resolve(null)
      : api.sensor(room, requestRange, budget, requestOptions)
    const controlRequest: Promise<HistoryOverviewControlResponse> = api.control(
      room,
      requestRange,
      budget,
      requestOptions
    )
    // The climate read is a third request but only after the overview pair
    // validated its range/budget, so an unfit request still issues no fetch.
    const overviewRequests = Promise.all([sensorRequest, controlRequest])
    const climateRequest: Promise<ControlMonitoringResponse> = overviewRequests.then(() => {
      if (controller.signal.aborted) throw new Error('overview generation aborted')
      return api.climate(room, requestRange, budget, requestOptions)
    })

    void Promise.all([overviewRequests, climateRequest]).then(
      ([[sensor, control], climate]) => {
        if (generationRef.current !== activeGeneration || controller.signal.aborted) return
        setState({ requestKey, sensor, control, climate, loading: false, error: null })
      },
      error => {
        if (generationRef.current !== activeGeneration) return
        if (controller.signal.aborted) return
        setState({
          requestKey,
          sensor: null,
          control: null,
          climate: null,
          loading: false,
          error: error instanceof Error ? error.message : String(error),
        })
      }
    )

    return () => {
      generationRef.current += 1
      controller.abort()
    }
  }, [
    requestKey,
    room,
    rangeStartMs,
    rangeEndMs,
    budget,
    controlOnly,
    scenario,
    fixtureSession,
    generation,
  ])

  // A changed range can render before passive-effect cleanup. Never expose
  // an earlier request's completed data during that intermediate render.
  if (state.requestKey !== requestKey) {
    return { sensor: null, control: null, climate: null, loading: true, error: null, retry }
  }
  return {
    sensor: state.sensor,
    control: state.control,
    climate: state.climate,
    loading: state.loading,
    error: state.error,
    retry,
  }
}
