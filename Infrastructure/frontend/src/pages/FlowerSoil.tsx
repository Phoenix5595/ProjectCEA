/**
 * Flower Room soil SCADA page (/flower/soil).
 *
 * Two top-down 4 ft x 4 ft raised-bed schematics (Front Bed first, Back Bed
 * second), zero to four assigned RS-485 probes per bed placed by the fixed
 * percentage layout, live readings polled every five seconds, a
 * new-sensor toast plus a persistent unassigned badge, and one multi-axis
 * historical uPlot fed by the soil history API.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import { UPlotChart, createMonitoringChartFeed } from '../features/monitoring/charts'
import type { MonitoringChartFeed, UPlotChartHandle } from '../features/monitoring/charts'
import {
  ChartDataTable,
  MonitoringFreshness,
  TimeRangeToolbar,
} from '../features/monitoring/components'
import { soilApi, type SensorRegistryRecord, type SoilLiveResponse, type SoilHistoryResponse } from '../features/soil/api'
import { emptyAlignedData } from '../features/soil/empty'
import { adaptSoilHistory } from '../features/soil/history'
import { groupByBed, PROBE_LAYOUT } from '../features/soil/layout'
import { logger } from '../utils/logger'
import '../features/monitoring/styles/monitoring.css'

const POLL_INTERVAL_MS = 5000
const LIVE_DURATION_MS = 3 * 3600 * 1000

type SoilRange = { kind: 'live'; duration: number } | { kind: 'fixed'; start: Date; end: Date }


const STALE_METRIC_MS = 20_000

function freshnessText(observedAtMs: number | null): string | null {
  if (observedAtMs === null) return 'No reading'
  return Date.now() - observedAtMs > STALE_METRIC_MS ? 'Stale' : null
}

function ProbeCard({ probe }: { probe: SoilLiveResponse['probes'][number] }) {
  const metrics: SoilLiveResponse['probes'][number]['metrics'] = probe.metrics
  const freshness = freshnessText(metrics.water_content?.observed_at.getTime() ?? null)
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
          <dt className="text-mon-text-secondary">Water content</dt>
          <dd className="font-mono text-mon-text">
            {metrics.water_content === null ? '—' : `${metrics.water_content.value.toFixed(1)} %`}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">EC</dt>
          <dd className="font-mono text-mon-text">
            {metrics.ec === null ? '—' : `${metrics.ec.value.toFixed(1)} µS/cm`}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">pH</dt>
          <dd className="font-mono text-mon-text">
            {metrics.ph === null ? '—' : metrics.ph.value.toFixed(2)}
          </dd>
        </div>
        <div className="flex justify-between gap-1">
          <dt className="text-mon-text-secondary">Temperature</dt>
          <dd className="font-mono text-mon-text">
            {metrics.temperature === null ? '—' : `${metrics.temperature.value.toFixed(1)} °C`}
          </dd>
        </div>
      </dl>
      {freshness !== null && (
        <span className="text-[9px] font-semibold uppercase tracking-wide text-status-danger">
          {freshness}
        </span>
      )}
    </div>
  )
}

function BedSchematic({
  label,
  probes,
  live,
  errorAt,
}: {
  label: string
  probes: SoilLiveResponse['probes']
  live: SoilLiveResponse | null
  errorAt: Date | null
}) {
  const slots = PROBE_LAYOUT[Math.min(probes.length, 4) as 0 | 1 | 2 | 3 | 4]
  return (
    <figure
      aria-label={`${label} bed schematic`}
      className="mon-card w-full"
      style={{ width: '100%' }}
    >
      <figcaption className="mon-card__title text-xs font-semibold uppercase tracking-wide text-mon-text-secondary">
        {label}
      </figcaption>
      <MonitoringFreshness lastGoodAt={live?.generated_at ?? null} errorAt={errorAt} />
      <div className="relative aspect-square w-full rounded border border-border-strong bg-surface-base">
        {probes.length === 0 ? (
          <p className="absolute inset-0 grid place-items-center text-sm text-mon-text-secondary">
            No probes assigned
          </p>
        ) : (
          probes.map((probe, index) => (
            <div
              key={probe.registry_id}
              className="absolute w-[46%] -translate-x-1/2 -translate-y-1/2"
              style={{ left: `${slots[index]?.x ?? 50}%`, top: `${slots[index]?.y ?? 50}%` }}
            >
              <ProbeCard probe={probe} />
            </div>
          ))
        )}
      </div>
    </figure>
  )
}

export default function FlowerSoil() {
  const navigate = useNavigate()
  const [registry, setRegistry] = useState<ReadonlyArray<SensorRegistryRecord> | null>(null)
  const [live, setLive] = useState<SoilLiveResponse | null>(null)
  const [history, setHistory] = useState<SoilHistoryResponse | null>(null)
  const [range, setRange] = useState<SoilRange>({ kind: 'live', duration: LIVE_DURATION_MS })
  const [errorAt, setErrorAt] = useState<Date | null>(null)
  const [nowMs, setNowMs] = useState(Date.now())
  void setNowMs
  const seenUnassignedRef = useRef<Set<number>>(new Set())
  const chartRef = useRef<UPlotChartHandle | null>(null)
  const feedRef = useRef<MonitoringChartFeed>(
    createMonitoringChartFeed(emptyAlignedData(), { kind: 'live', duration: LIVE_DURATION_MS }),
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
        (record) =>
          record.status === 'unassigned' && !seenUnassignedRef.current.has(record.registry_id),
      )
      // One Sonner toast per newly detected ID per mount; pre-existing
      // unassigned records surface only in the badge, never as a toast storm.
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

  const unassignedRecords = useMemo(() => {
    if (registry === null) return []
    return registry.filter((record) => record.status === 'unassigned')
  }, [registry])

  const isLive = range.kind === 'live'
  const effectiveWindow =
    range.kind === 'fixed'
      ? { start: range.start, end: range.end }
      : { start: new Date(nowMs - range.duration), end: new Date(nowMs) }

  useEffect(() => {
    let cancelled = false
    async function loadHistory() {
      try {
        const response = await soilApi.soilHistory({
          start: effectiveWindow.start,
          end: effectiveWindow.end,
          maxPoints: 1000,
        })
        if (!cancelled) setHistory(response)
      } catch (error) {
        logger.error('Soil history fetch failed', error)
      }
    }
    void loadHistory()
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range])

  const aligned = useMemo(
    () => (history === null ? emptyAlignedData() : adaptSoilHistory(history)),
    [history],
  )

  const feed = feedRef.current
  useEffect(() => {
    feed.publish(aligned, range)
  }, [aligned, feed, range])

  const beds = groupByBed(live?.probes ?? [])

  return (
    <div className="mon-page">
      <TimeRangeToolbar
        range={range}
        isLive={isLive}
        onLive={(duration) => setRange({ kind: 'live', duration })}
        onFixedRange={(start, end) => setRange({ kind: 'fixed', start, end })}
        onPause={() => undefined}
        onResume={() => undefined}
        onResetZoom={() => chartRef.current?.resetZoom()}
        defaultDuration={LIVE_DURATION_MS}
      />

      {errorAt !== null && (
        <div role="alert" className="mon-banner mon-banner--error">
          Soil data is unavailable right now. Values below may be stale.
        </div>
      )}

      {unassignedRecords.length > 0 && (
        <button
          type="button"
          onClick={goToSensorSettings}
          className="mon-banner mon-banner--error"
        >
          {unassignedRecords.length} unassigned sensor
          {unassignedRecords.length === 1 ? '' : 's'} — open Sensor Settings
        </button>
      )}

      <div className="mon-layout mon-layout--beds">
      <aside className="mon-side mon-side--beds">
        <BedSchematic label="Back Bed" probes={beds.backBed} live={live} errorAt={errorAt} />
        <BedSchematic label="Front Bed" probes={beds.frontBed} live={live} errorAt={errorAt} />
      </aside>
      <div className="mon-main">
      <section className="mon-card flex min-h-0 flex-col">
        <h2 className="mon-card__title text-lg">Soil history</h2>
        <div className="min-h-0 flex-1">
          <UPlotChart
            ref={chartRef}
            feed={feed}
            onZoom={(zoomed) => setRange({ kind: 'fixed', start: zoomed.start, end: zoomed.end })}
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
