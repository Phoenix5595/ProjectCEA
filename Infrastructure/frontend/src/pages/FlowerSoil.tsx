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
import { TimeRangeToolbar } from '../features/monitoring/components'
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

function ProbeCard({
  probe,
}: {
  probe: SoilLiveResponse['probes'][number]
}) {
  const metrics: SoilLiveResponse['probes'][number]['metrics'] = probe.metrics
  const freshness = freshnessText(metrics.water_content?.observed_at.getTime() ?? null)
  return (
    <div className="soil-probe-card" data-testid="soil-probe-card">
      <div className="soil-probe-card__name">{probe.display_name}</div>
      <dl className="soil-probe-card__metrics">
        <div>
          <dt>Water content</dt>
          <dd>{metrics.water_content === null ? '—' : `${metrics.water_content.value.toFixed(1)} %`}</dd>
        </div>
        <div>
          <dt>EC</dt>
          <dd>{metrics.ec === null ? '—' : `${metrics.ec.value.toFixed(1)} µS/cm`}</dd>
        </div>
        <div>
          <dt>pH</dt>
          <dd>{metrics.ph === null ? '—' : metrics.ph.value.toFixed(2)}</dd>
        </div>
        <div>
          <dt>Temperature</dt>
          <dd>{metrics.temperature === null ? '—' : `${metrics.temperature.value.toFixed(1)} °C`}</dd>
        </div>
      </dl>
      {freshness !== null && (
        <span className="text-[10px] font-semibold uppercase tracking-wide text-status-warn-text">
          {freshness}
        </span>
      )}
    </div>
  )
}

function BedSchematic({
  label,
  probes,
}: {
  label: string
  probes: SoilLiveResponse['probes']
}) {
  const slots = PROBE_LAYOUT[Math.min(probes.length, 4) as 0 | 1 | 2 | 3 | 4]
  return (
    <figure
      aria-label={`${label} schematic`}
      className="relative mx-auto aspect-square w-full max-w-[560px] rounded border-2 border-border-strong bg-bg-subtle"
    >
      <figcaption className="mb-1 flex items-baseline justify-between px-1 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        <span>{label}</span>
        <span className="font-mono normal-case">4 ft x 4 ft</span>
      </figcaption>
      {probes.length === 0 ? (
        <p className="absolute inset-0 grid place-items-center text-sm text-text-muted">
          No probes assigned
        </p>
      ) : (
        probes.map((probe, index) => (
          <div
            key={probe.registry_id}
            className="absolute w-[40%] -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${slots[index]?.x ?? 50}%`, top: `${slots[index]?.y ?? 50}%` }}
          >
            <ProbeCard probe={probe} />
          </div>
        ))
      )}
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
        toast(`New soil probe detected: ${record.display_name}`, {
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
    <div className="mon-page space-y-6 p-4">
      <header className="space-y-1">
        <h1 className="text-2xl font-bold text-text-input">Flower soil</h1>
        <p className="text-sm text-text-muted">
          Raised-bed probe schematics, live readings, and multi-axis history.
        </p>
      </header>

      {errorAt !== null && (
        <div
          role="alert"
          className="rounded border border-status-danger-border/60 bg-status-danger-bg/30 px-3 py-2 text-sm text-status-danger-text"
        >
          Soil data is unavailable right now. Values below may be stale.
        </div>
      )}

      <section className="space-y-4">
        {unassignedRecords.length > 0 && (
          <button
            type="button"
            onClick={goToSensorSettings}
            className="rounded border border-status-warn-border/70 bg-status-warn-bg/30 px-3 py-1.5 text-xs font-semibold text-status-warn-text"
          >
            {unassignedRecords.length} unassigned sensor
            {unassignedRecords.length === 1 ? '' : 's'} — open Sensor Settings
          </button>
        )}
        <div className="mx-auto grid max-w-[640px] grid-cols-1 gap-8">
          <BedSchematic label="Front Bed" probes={beds.frontBed} />
          <BedSchematic label="Back Bed" probes={beds.backBed} />
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="mon-card__title text-lg font-bold text-text-input">Soil history</h2>
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
        <UPlotChart
          ref={chartRef}
          feed={feed}
          onZoom={(zoomed) => setRange({ kind: 'fixed', start: zoomed.start, end: zoomed.end })}
          title="Soil history"
          className="mon-card"
        />
      </section>
    </div>
  )
}
