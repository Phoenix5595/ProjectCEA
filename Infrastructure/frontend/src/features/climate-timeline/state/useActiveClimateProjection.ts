import { useCallback, useEffect, useRef, useState } from 'react'

import {
  monitoringRequestContextFromSearchParams,
  type CurrentPublicationResponse,
  type CurrentSeriesPoint,
  type CurrentSnapshot,
  type FutureProjection,
  type MonitoringApi,
  type ProjectionPublicationResponse,
  type RichTrajectoryEnvelope,
} from '../../monitoring/api'
import { currentProfileSeriesId, currentSetpointSeriesId } from '../charts/timelineSources'

export type ActiveProfileKey = { readonly modeId: number; readonly submodeId: number | null }
export type ActiveClimateProjectionOptions = {
  location: string
  cluster: string
  registryVersion: number | null
  api: MonitoringApi
}
export type ActiveClimateProjectionState = {
  current: CurrentSnapshot | null
  future: readonly FutureProjection[]
  trajectory: RichTrajectoryEnvelope | null
  currentError: string | null
  projectionError: string | null
  refresh(): Promise<void>
}

export function currentMatchesRegistryVersion(registryVersion: number | null, revision: string): boolean {
  return typeof registryVersion === 'number' && Number.isSafeInteger(registryVersion) &&
    registryVersion > 0 && revision === registryVersion.toString(16).padStart(7, '0')
}

export function usableCurrentForVersion(
  registryVersion: number | null, snapshot: CurrentSnapshot | null
): CurrentSnapshot | null {
  const now = Date.now()
  return snapshot != null && snapshot.version.contract_version === 1 &&
    currentMatchesRegistryVersion(registryVersion, snapshot.version.revision) &&
    snapshot.observed_at.getTime() <= now && now < snapshot.valid_until.getTime()
    ? snapshot : null
}

export function usableCurrentPoint(point: CurrentSeriesPoint, now = Date.now()): boolean {
  return point.quality !== 'unavailable' && point.observed_at.getTime() <= now &&
    now < point.valid_until.getTime() && (point.value === null || Number.isFinite(point.value))
}

export function compatibleFutureForCurrent(
  current: CurrentSnapshot, projection: ProjectionPublicationResponse | null
): readonly FutureProjection[] {
  if (projection == null || projection.quality === 'unavailable') return []
  if (!projection.value.every(interval => interval.version.contract_version === current.version.contract_version &&
    interval.version.config_version === current.version.config_version &&
    interval.version.revision === current.version.revision)) return []
  const now = Date.now()
  return projection.value.filter(interval => interval.generated_at.getTime() <= now &&
    interval.valid_from.getTime() < interval.valid_until.getTime() && now < interval.valid_until.getTime() &&
    interval.series.some(point => point.valid_from.getTime() < point.valid_until.getTime() &&
      point.valid_from.getTime() >= interval.valid_from.getTime() &&
      point.valid_until.getTime() <= interval.valid_until.getTime() && now < point.valid_until.getTime() &&
      point.quality !== 'unavailable' && point.value !== null && Number.isFinite(point.value)))
}

export function currentActiveProfileKey(
  registryVersion: number | null, snapshot: CurrentSnapshot | null, location: string, cluster: string
): ActiveProfileKey | null {
  const usable = usableCurrentForVersion(registryVersion, snapshot)
  if (usable == null) return null
  const mode = usable.series.find(point => point.series_id.value === currentProfileSeriesId(location, cluster, 'mode'))
  const submode = usable.series.find(point => point.series_id.value === currentProfileSeriesId(location, cluster, 'submode'))
  if (mode == null || !usableCurrentPoint(mode) || mode.value == null ||
    !Number.isSafeInteger(mode.value) || mode.value <= 0 ||
    (submode != null && (!usableCurrentPoint(submode) ||
      (submode.value !== null && (!Number.isSafeInteger(submode.value) || submode.value <= 0))))) return null
  // The completed-tick observer omits submode for an exact NULL/base profile.
  return { modeId: mode.value, submodeId: submode?.value ?? null }
}

const empty = {
  current: null, future: [], trajectory: null, currentError: null, projectionError: null,
} satisfies Omit<ActiveClimateProjectionState, 'refresh'>

/** Single room/cluster generation and single flight own both publication reads. */
export function useActiveClimateProjection(options: ActiveClimateProjectionOptions): ActiveClimateProjectionState {
  const { location, cluster, registryVersion, api } = options
  const [requestContext] = useState(() => typeof window === 'undefined' ? undefined :
    monitoringRequestContextFromSearchParams(new URLSearchParams(window.location.search)))
  const scope = `${location}\u0000${cluster}\u0000${registryVersion}`
  const [state, setState] = useState({ scope, ...empty } as Omit<ActiveClimateProjectionState, 'refresh'> & { scope: string })
  const refreshRef = useRef<(() => Promise<void>) | null>(null)
  const refresh = useCallback(() => refreshRef.current?.() ?? Promise.resolve(), [])

  useEffect(() => {
    let cancelled = false
    let flight: Promise<void> | null = null
    let forced = false
    let current: CurrentSnapshot | null = null
    let projection: ProjectionPublicationResponse | null = null
    let fetchedProjection: ProjectionPublicationResponse | null | undefined
    let lastFetch = -Infinity
    let projectionVersion: string | null = null
    let expiryKey = ''
    let currentError: string | null = null
    let projectionError: string | null = null
    let expiryTimer: number | undefined
    const abort = new AbortController()
    const versionKey = (value: CurrentSnapshot | null) => value == null ? null :
      `${value.version.contract_version}:${value.version.config_version}:${value.version.revision}`
    const expiredKey = () => projection?.value.flatMap(interval => [interval.valid_until, ...interval.series.map(point => point.valid_until)])
      .filter(date => date.getTime() <= Date.now()).map(date => date.getTime()).join(',') ?? ''

    const publish = () => {
      if (cancelled) return
      current = usableCurrentForVersion(registryVersion, current)
      if (current == null) {
        projection = null
        projectionVersion = null
        expiryKey = ''
      }
      const future = current == null ? [] : compatibleFutureForCurrent(current, projection)
      const rich = future.length > 0 && projection?.trajectory?.room === location &&
        projection.trajectory.revision_scope === 'saved' &&
        projection.trajectory.window.start.getTime() <= Date.now() && Date.now() < projection.trajectory.window.end.getTime() &&
        projection.trajectory.base_config_revision === current?.version.config_version.toString(16).padStart(7, '0')
        ? projection.trajectory : null
      setState({ scope, current, future, trajectory: rich,
        currentError: current == null ? currentError ?? 'Current effective unavailable' : null,
        projectionError: future.length === 0 ? projectionError ?? 'Running projection unavailable' : null })
      clearTimeout(expiryTimer)
      const expiries = [current?.valid_until, ...current?.series.map(point => point.valid_until) ?? [],
        ...future.flatMap(interval => [interval.valid_until, ...interval.series.map(point => point.valid_until)])]
        .filter((date): date is Date => date != null && date.getTime() > Date.now())
      if (expiries.length > 0) expiryTimer = window.setTimeout(() => {
        publish()
        void run(false)
      }, Math.max(1, Math.min(...expiries.map(date => date.getTime())) - Date.now()))
    }
    const acceptCurrent = (read: CurrentPublicationResponse) => {
      current = read.quality === 'unavailable' ? null : usableCurrentForVersion(registryVersion, read.value)
      const ids = [currentProfileSeriesId(location, cluster, 'mode'),
        ...(['heating_setpoint', 'cooling_setpoint', 'vpd_setpoint', 'co2_setpoint'] as const).map(metric => currentSetpointSeriesId(location, cluster, metric))]
      if (!current?.series.some(point => ids.includes(point.series_id.value) && usableCurrentPoint(point))) current = null
      currentError = current == null ? 'Current publication unavailable, expired or registry mismatch' : null
      if (current == null) projection = null
      publish()
    }
    const fetchProjection = async () => {
      lastFetch = Date.now()
      try {
        const read = await api.controlProjection(location, { ...requestContext, signal: abort.signal, timeoutMs: 5_000 })
        if (cancelled) return
        fetchedProjection = read
        projectionError = null
      } catch (error) {
        if (cancelled) return
        projection = null
        fetchedProjection = null
        projectionError = error instanceof Error ? error.message : String(error)
        expiryKey = ''
        publish()
      }
    }
    const execute = async (force: boolean) => {
      fetchedProjection = undefined
      if (!location || !cluster || !currentMatchesRegistryVersion(registryVersion, registryVersion?.toString(16).padStart(7, '0') ?? '')) {
        current = null
        projection = null
        publish()
        return
      }
      const needsProjection = force || lastFetch === -Infinity || Date.now() - lastFetch >= 30_000 ||
        (current != null && versionKey(current) !== projectionVersion) || expiredKey() !== expiryKey
      const readCurrent = async () => {
        try {
          const read = await api.controlCurrent(location, { ...requestContext, signal: abort.signal, timeoutMs: 5_000 })
          if (!cancelled) acceptCurrent(read)
        } catch (error) {
          if (cancelled) return
          current = null
          projection = null
          currentError = error instanceof Error ? error.message : String(error)
          publish()
        }
      }
      if (needsProjection) await Promise.all([readCurrent(), fetchProjection()])
      else {
        await readCurrent()
        if (!cancelled && current != null && versionKey(current) !== projectionVersion) await fetchProjection()
      }
      if (fetchedProjection !== undefined) {
        projection = fetchedProjection
        expiryKey = expiredKey()
      }
      // Record the current version reconciled by this flight, including failed
      // or mismatched futures; do not turn every 1s current tick into a retry.
      projectionVersion = versionKey(current)
      publish()
    }
    const run = (force: boolean): Promise<void> => {
      if (cancelled) return Promise.resolve()
      if (flight != null) {
        if (!force) return flight
        forced = true
        return flight.then(() => forced && !cancelled ? run(true) : undefined)
      }
      forced = false
      flight = (async () => {
        await execute(force)
        while (forced && !cancelled) {
          forced = false
          await execute(true)
        }
      })().finally(() => { flight = null })
      return flight
    }
    refreshRef.current = () => run(true)
    setState({ scope, ...empty })
    void run(false)
    const timer = setInterval(() => { publish(); void run(false) }, 1_000)
    return () => {
      cancelled = true
      refreshRef.current = null
      abort.abort()
      clearInterval(timer)
      clearTimeout(expiryTimer)
    }
  }, [api, location, cluster, registryVersion, requestContext, scope])

  return { ...(state.scope === scope ? state : empty), refresh }
}
