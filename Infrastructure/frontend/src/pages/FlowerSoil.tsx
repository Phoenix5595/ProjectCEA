/**
 * Flower Room soil SCADA page (/flower/soil).
 *
 * Side column: a heatmap metric toggle (Off / Moisture / Temperature / EC /
 * pH, default Moisture) and two top-down bed figures (Back Bed above Front
 * Bed). Each figure draws placed RS-485 probes as solid markers at their
 * free position, placements closed within 72 h as hollow markers, and a
 * heatmap that fades past positions linearly to zero over 72 h (R9). Probe
 * cards with live readings (polled every five seconds) sit under each bed.
 * A new-probe toast fires only for RS-485 probes first seen after the page
 * opened; a persistent badge lists unassigned and pending sensors. One
 * multi-axis historical uPlot is fed by the soil history API.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'

import { UPlotChart, createMonitoringChartFeed } from '../features/monitoring/charts'
import type { MonitoringChartFeed, UPlotChartHandle } from '../features/monitoring/charts'
import { MonitoringApi, monitoringRequestContextFromSearchParams } from '../features/monitoring/api'
import { ChartDataTable, TimeRangeToolbar } from '../features/monitoring/components'
import { alignPhotoperiod } from '../features/monitoring/data'
import type { PhotoperiodInterval } from '../features/monitoring/data'
import SoilBedFigure, {
  type ActiveMarker,
  type ClosedMarker,
  type HeatmapMetric,
} from '../features/soil/SoilBedFigure'
import {
  soilApi,
  type SensorRegistryRecord,
  type SoilHeatmapResponse,
  type SoilLiveResponse,
  type SoilHistoryResponse,
} from '../features/soil/api'
import { emptyAlignedData } from '../features/soil/empty'
import { BED_DISPLAY_ORDER } from '../features/soil/geometry'
import { colorDomain, freshness } from '../features/soil/heatmap'
import { adaptSoilHistory } from '../features/soil/history'
import { logger } from '../utils/logger'
import '../features/monitoring/styles/monitoring.css'

const POLL_INTERVAL_MS = 5000
const HEATMAP_INTERVAL_MS = 30_000

const HEATMAP_BUTTONS: { metric: HeatmapMetric; label: string }[] = [
  { metric: 'off', label: 'Off' },
  { metric: 'water_content', label: 'Moisture' },
  { metric: 'temperature', label: 'Temperature' },
  { metric: 'ec', label: 'EC' },
  { metric: 'ph', label: 'pH' },
]
const LIVE_DURATION_MS = 3 * 3600 * 1000

type SoilRange = { kind: 'live'; duration: number } | { kind: 'fixed'; start: Date; end: Date }

const STALE_METRIC_MS = 20_000

function freshnessText(observedAtMs: number | null): string | null {
  if (observedAtMs === null) return 'No reading'
  return Date.now() - observedAtMs > STALE_METRIC_MS ? 'Stale' : null
}

function MetricFreshness({ observedAtMs }: { observedAtMs: number | null }) {
  const status = freshnessText(observedAtMs)
  if (status === null) return null
  return (
    <span
      className="ml-1 text-[9px] font-semibold uppercase tracking-wide"
      style={{ color: 'var(--mon-stale)' }}
    >
      {' '}
      {status}
    </span>
  )
}

function ProbeCard({ probe }: { probe: SoilLiveResponse['probes'][number] }) {
  const metrics: SoilLiveResponse['probes'][number]['metrics'] = probe.metrics
  return (
    <div
      className="rounded border border-border-default bg-surface-secondary px-1 py-1"
      data-testid="soil-probe-card"
    >
      <div className="truncate text-[10px] font-semibold uppercase tracking-wide text-mon-text-secondary">
        Soil probe #{probe.hardware_address}
      </div>
      <dl className="mt-0.5 space-y-px text-[10px] leading-tight">
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">
            Water content
            <MetricFreshness observedAtMs={metrics.water_content?.observed_at.getTime() ?? null} />
          </dt>
          <dd className="font-mono text-text-default">
            {metrics.water_content === null ? '—' : `${metrics.water_content.value.toFixed(1)} %`}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">
            EC
            <MetricFreshness observedAtMs={metrics.ec?.observed_at.getTime() ?? null} />
          </dt>
          <dd className="font-mono text-text-default">
            {metrics.ec === null ? '—' : `${metrics.ec.value.toFixed(1)} µS/cm`}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">
            pH
            <MetricFreshness observedAtMs={metrics.ph?.observed_at.getTime() ?? null} />
          </dt>
          <dd className="font-mono text-text-default">
            {metrics.ph === null ? '—' : metrics.ph.value.toFixed(2)}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">
            Temperature
            <MetricFreshness observedAtMs={metrics.temperature?.observed_at.getTime() ?? null} />
          </dt>
          <dd className="font-mono text-text-default">
            {metrics.temperature === null ? '—' : `${metrics.temperature.value.toFixed(1)} °C`}
          </dd>
        </div>
      </dl>
    </div>
  )
}

export default function FlowerSoil() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(new URLSearchParams(searchParams.toString())),
    [searchParams]
  )
  const monitoringApi = useMemo(() => new MonitoringApi(requestContext), [requestContext])
  const [registry, setRegistry] = useState<ReadonlyArray<SensorRegistryRecord> | null>(null)
  const [live, setLive] = useState<SoilLiveResponse | null>(null)
  const [history, setHistory] = useState<SoilHistoryResponse | null>(null)
  const [range, setRange] = useState<SoilRange>({ kind: 'live', duration: LIVE_DURATION_MS })
  const [historyPaused, setHistoryPaused] = useState(false)
  const pausedAtRef = useRef<Date | null>(null)
  const [errorAt, setErrorAt] = useState<Date | null>(null)
  const [maxPoints, setMaxPoints] = useState<number | null>(null)
  const [photoperiod, setPhotoperiod] = useState<PhotoperiodInterval[]>([])
  const seenUnassignedRef = useRef<Set<number>>(new Set())
  const mountedAtRef = useRef<number>(Date.now())
  const [metric, setMetric] = useState<HeatmapMetric>('water_content')
  const [heatmap, setHeatmap] = useState<SoilHeatmapResponse | null>(null)
  const chartRef = useRef<UPlotChartHandle | null>(null)
  const feedRef = useRef<MonitoringChartFeed>(
    createMonitoringChartFeed(emptyAlignedData(), { kind: 'live', duration: LIVE_DURATION_MS })
  )

  const goToSensorSettings = useCallback(() => {
    navigate('/devices?tab=sensors&status=unassigned')
  }, [navigate])

  const poll = useCallback(async () => {
    try {
      const [registryList, liveResponse] = await Promise.all([
        soilApi.listRegistry(),
        soilApi.soilLive(),
      ])
      setRegistry(registryList.records)
      setLive(liveResponse)
      setErrorAt(null)
      const freshUnassigned = registryList.records.filter(
        record =>
          record.bus === 'rs485' &&
          record.status === 'unassigned' &&
          record.first_seen.getTime() > mountedAtRef.current &&
          !seenUnassignedRef.current.has(record.registry_id)
      )
      // One Sonner toast per probe first seen after mount; pre-existing and
      // disconnected probes surface only in the badge, never as a toast.
      for (const record of freshUnassigned) {
        seenUnassignedRef.current.add(record.registry_id)
        toast(`New soil probe detected: Soil probe #${record.hardware_address}`, {
          action: { label: 'Sensor Settings', onClick: goToSensorSettings },
        })
      }
    } catch (error) {
      logger.error('Soil page poll failed', error)
      setErrorAt(new Date())
    }
  }, [goToSensorSettings, seenUnassignedRef])

  useEffect(() => {
    void poll()
    const intervalId = window.setInterval(() => void poll(), POLL_INTERVAL_MS)
    return () => window.clearInterval(intervalId)
  }, [poll, seenUnassignedRef])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const response = await soilApi.heatmap(metric === 'off' ? 'water_content' : metric)
        if (!cancelled) setHeatmap(response)
      } catch (error) {
        logger.error('Soil heatmap fetch failed', error)
      }
    }
    void load()
    const intervalId = window.setInterval(() => void load(), HEATMAP_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [metric])

  const pendingRecords = useMemo(() => {
    if (registry === null) return []
    return registry.filter(record => record.identity_status === 'pending')
  }, [registry])

  const unassignedRecords = useMemo(() => {
    if (registry === null) return []
    return registry.filter(
      record => record.identity_status !== 'pending' && record.status === 'unassigned'
    )
  }, [registry])

  const isLive = range.kind === 'live'

  useEffect(() => {
    let cancelled = false
    let inFlight = false
    if (maxPoints === null) return
    const budget = maxPoints
    async function loadHistory() {
      if (cancelled || inFlight) return
      inFlight = true
      const end = range.kind === 'fixed' ? range.end : new Date()
      const start = range.kind === 'fixed' ? range.start : new Date(end.getTime() - range.duration)
      try {
        const response = await soilApi.soilHistory({
          start,
          end,
          maxPoints: budget,
        })
        if (!cancelled) setHistory(response)
      } catch (error) {
        logger.error('Soil history fetch failed', error)
      } finally {
        inFlight = false
      }
    }

    if (range.kind === 'live' && historyPaused) {
      return () => {
        cancelled = true
      }
    }
    void loadHistory()
    if (range.kind !== 'live') {
      return () => {
        cancelled = true
      }
    }

    const intervalId = window.setInterval(() => void loadHistory(), POLL_INTERVAL_MS)
    return () => {
      cancelled = true
      window.clearInterval(intervalId)
    }
  }, [range, historyPaused, maxPoints])

  useEffect(() => {
    if (range.kind === 'live' && historyPaused) return
    if (maxPoints === null) return
    const controller = new AbortController()
    const end = range.kind === 'fixed' ? range.end : new Date()
    const start = range.kind === 'fixed' ? range.start : new Date(end.getTime() - range.duration)
    void monitoringApi
      .controlRange(
        'Flower Room',
        start.toISOString(),
        end.toISOString(),
        maxPoints,
        'photoperiod',
        { signal: controller.signal }
      )
      .then(response => {
        if (controller.signal.aborted) return
        setPhotoperiod(
          alignPhotoperiod(response.photoperiod, start.getTime(), end.getTime())
        )
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        logger.error('Soil photoperiod fetch failed', error)
      })
    return () => controller.abort()
  }, [range, historyPaused, maxPoints, monitoringApi])

  const aligned = useMemo(
    () => (history === null ? emptyAlignedData() : adaptSoilHistory(history, photoperiod)),
    [history, photoperiod]
  )

  const feed = feedRef.current
  useEffect(() => {
    feed.publish(aligned, range)
  }, [aligned, feed, range])

  const nowMs = Date.now()
  const valueMetric = metric === 'off' ? 'water_content' : metric
  const heatDomain = useMemo(() => {
    const now = Date.now()
    const values = (heatmap?.beds ?? []).flatMap(bed =>
      bed.points.filter(point => freshness(point, now) > 0).map(point => point.value)
    )
    return colorDomain(values)
  }, [heatmap])

  const bedFigures = BED_DISPLAY_ORDER.map(name => {
    const heatBed = heatmap?.beds.find(bed => bed.name === name) ?? null
    const activeMarkers: ActiveMarker[] = (registry ?? [])
      .filter(
        record =>
          record.bus === 'rs485' &&
          record.hardware_address !== null &&
          record.assignment?.kind === 'rs485' &&
          record.assignment.bed === name
      )
      .sort((a, b) => (a.hardware_address ?? 0) - (b.hardware_address ?? 0))
      .map(record => {
        const assignment = record.assignment?.kind === 'rs485' ? record.assignment : null
        const liveProbe = live?.probes.find(probe => probe.registry_id === record.registry_id)
        return {
          address: record.hardware_address ?? 0,
          x_frac: assignment?.x_frac ?? 0.5,
          y_frac: assignment?.y_frac ?? 0.5,
          value: liveProbe?.metrics[valueMetric]?.value ?? null,
        }
      })
    const closedMarkers: ClosedMarker[] = (heatBed?.points ?? []).flatMap(point =>
      !point.active && point.removed_at !== null && freshness(point, nowMs) > 0
        ? [
            {
              address: point.hardware_address,
              x_frac: point.x_frac,
              y_frac: point.y_frac,
              removed_at: point.removed_at,
            },
          ]
        : []
    )
    const probes = (live?.probes ?? [])
      .filter(probe => probe.bed === name)
      .sort((a, b) => a.hardware_address - b.hardware_address)
    return { name, heatBed, activeMarkers, closedMarkers, probes }
  })

  return (
    <div className="mon-page flex min-h-screen flex-col gap-1">
      <TimeRangeToolbar
        range={range}
        isLive={isLive}
        now={() => pausedAtRef.current ?? new Date()}
        onLive={duration => {
          pausedAtRef.current = null
          setHistoryPaused(false)
          setRange({ kind: 'live', duration })
        }}
        onFixedRange={(start, end) => {
          pausedAtRef.current = null
          setHistoryPaused(false)
          setRange({ kind: 'fixed', start, end })
        }}
        onPause={() => {
          pausedAtRef.current = new Date()
          setHistoryPaused(true)
        }}
        onResume={() => {
          pausedAtRef.current = null
          setHistoryPaused(false)
        }}
        onResetZoom={() => chartRef.current?.resetZoom()}
        defaultDuration={LIVE_DURATION_MS}
      />

      {errorAt !== null && (
        <div role="alert" className="mon-banner mon-banner--error">
          Soil data is unavailable right now. Values below may be stale.
        </div>
      )}

      {unassignedRecords.length > 0 && (
        <button type="button" onClick={goToSensorSettings} className="mon-banner mon-banner--error">
          {unassignedRecords.length} unassigned sensor
          {unassignedRecords.length === 1 ? '' : 's'} — open Sensor Settings
        </button>
      )}

      {pendingRecords.length > 0 && (
        <button
          type="button"
          onClick={goToSensorSettings}
          className="mon-banner mon-banner--error"
        >
          {pendingRecords.length} sensor {pendingRecords.length === 1 ? 'identity needs' : 'identities need'}{' '}
          confirmation — open Sensor Settings
        </button>
      )}

      <div className="mon-layout mon-layout--beds min-h-0 flex-1">
        <aside className="mon-side mon-side--beds">
          <div role="group" aria-label="Heatmap" className="mon-card flex flex-wrap gap-1">
            {HEATMAP_BUTTONS.map(button => (
              <button
                key={button.metric}
                type="button"
                aria-pressed={metric === button.metric}
                onClick={() => setMetric(button.metric)}
                className="rounded border border-border-default px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-mon-text-secondary aria-pressed:border-border-emphasis aria-pressed:bg-surface-tertiary aria-pressed:text-text-default"
              >
                {button.label}
              </button>
            ))}
          </div>
          {bedFigures.map(figure => (
            <SoilBedFigure
              key={figure.name}
              name={figure.name}
              testId={figure.name === 'Back Bed' ? 'back' : 'front'}
              geometry={figure.heatBed}
              metric={metric}
              heatPoints={figure.heatBed?.points ?? []}
              domain={heatDomain}
              activeMarkers={figure.activeMarkers}
              closedMarkers={figure.closedMarkers}
              nowMs={nowMs}
              lastGoodAt={live?.generated_at ?? null}
              errorAt={errorAt}
            >
              {figure.probes.length > 0 && (
                <div className="mt-1 grid grid-cols-2 gap-1">
                  {figure.probes.map(probe => (
                    <ProbeCard key={probe.registry_id} probe={probe} />
                  ))}
                </div>
              )}
            </SoilBedFigure>
          ))}
        </aside>
        <div className="mon-main flex min-h-0 flex-col">
          <section className="mon-card flex min-h-0 flex-1 flex-col">
            <h2 className="mon-card__title text-lg">Soil history</h2>
            <div className="min-h-[420px] w-full flex-1">
              <UPlotChart
                ref={chartRef}
                feed={feed}
                onZoom={zoomed => {
                  pausedAtRef.current = null
                  setHistoryPaused(false)
                  setRange({ kind: 'fixed', start: zoomed.start, end: zoomed.end })
                }}
                onRequestBudgetChange={setMaxPoints}
                bucketIntervalSeconds={history?.bucket_seconds ?? null}
                title="Soil history"
              />
            </div>
            <ChartDataTable title="Soil history" data={aligned} />
          </section>
        </div>
      </div>
    </div>
  )
}
