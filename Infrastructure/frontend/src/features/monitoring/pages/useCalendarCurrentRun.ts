/**
 * Calendar current-run control for the historical time-range toolbar.
 *
 * Fetches `GET /api/calendar/current-run` for one room only while a Historical
 * route asks for it (`location` non-null); the regular Monitoring routes pass
 * `null` and never touch the calendar. Each fetch owns an AbortController and
 * a generation counter, so an unmounted view or a superseded request can never
 * publish a stale run: the button stays disabled with the honest reason
 * instead of inventing a range.
 */
import { useEffect, useRef, useState } from 'react'
import { isAxiosError } from 'axios'

import { apiClient } from '../../../services/api'
import { torontoMidnightUtc } from '../../../utils/torontoWallTime'
import type { MonitoringRequestContext } from '../api'
import { MIN_RANGE_MS } from '../components/timeRangeToolbar.time'

export interface CalendarCurrentRunControl {
  /** True only when the calendar returned exactly one current run. */
  readonly ready: boolean
  /** Honest loading/absent/ambiguous/error explanation, or the ready action summary. */
  readonly title: string
  /** Toronto-midnight start of the unique run; defined only when ready. */
  readonly start?: Date
}

const IDLE_CONTROL: CalendarCurrentRunControl = Object.freeze({
  ready: false,
  title: 'Current run is available on the Historical pages only',
})

type CurrentRunReason = 'loading' | 'absent' | 'ambiguous' | 'error'

/** Honest disabled-button text for every state that cannot apply a range. */
function reasonControl(reason: CurrentRunReason, location: string): CalendarCurrentRunControl {
  switch (reason) {
    case 'loading':
      return { ready: false, title: `Loading the ${location} current run from the calendar…` }
    case 'absent':
      return { ready: false, title: `No active current run in the ${location} calendar` }
    case 'ambiguous':
      return {
        ready: false,
        title: `The ${location} calendar has more than one active current run`,
      }
    case 'error':
      return { ready: false, title: `The ${location} current run could not be loaded` }
  }
}

export function useCalendarCurrentRun(
  location: string | null,
  requestContext: MonitoringRequestContext | undefined
): CalendarCurrentRunControl {
  const [control, setControl] = useState<CalendarCurrentRunControl>(IDLE_CONTROL)
  const scenario = requestContext?.scenario
  const fixtureSession = requestContext?.fixtureSession
  const generationRef = useRef(0)

  useEffect(() => {
    if (location === null) return
    const generation = generationRef.current + 1
    generationRef.current = generation
    const controller = new AbortController()
    setControl(reasonControl('loading', location))
    apiClient
      .getCalendarCurrentRun(location, {
        signal: controller.signal,
        scenario,
        fixtureSession,
      })
      .then(response => {
        if (generation !== generationRef.current || controller.signal.aborted) return
        if (response === null) {
          setControl(reasonControl('absent', location))
          return
        }
        // Resolve the calendar start to Toronto midnight here; a body whose
        // start_date cannot be resolved is an honest load failure, never a
        // fabricated range.
        const start = torontoMidnightUtc(response.start_date)
        if (start === null) {
          setControl(reasonControl('error', location))
          return
        }
        if (Date.now() - start.getTime() < MIN_RANGE_MS) {
          setControl({
            ready: false,
            title: 'Current run has not yet reached the five-minute graph minimum',
          })
          return
        }
        setControl({
          ready: true,
          title: `Apply the ${location} current run range: calendar start to now`,
          start,
        })
      })
      .catch(error => {
        if (generation !== generationRef.current || controller.signal.aborted) return
        setControl(
          reasonControl(
            isAxiosError(error) && error.response?.status === 409 ? 'ambiguous' : 'error',
            location
          )
        )
      })
    return () => {
      controller.abort()
    }
  }, [location, scenario, fixtureSession])

  return control
}
