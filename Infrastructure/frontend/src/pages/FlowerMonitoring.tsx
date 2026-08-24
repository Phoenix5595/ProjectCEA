/**
 * Flower Room monitoring page (native, no Grafana iframe).
 *
 * Consumes the Flower manifest, a page-level `MonitoringStore`, and the
 * reusable chart/table primitives to render the canonical Flower dashboard:
 * a time-range toolbar, the Averages / Front / Back / Statistics tables, and
 * two uPlot chart regions (climate and device) each with an accessible
 * "View data as table" alternative. A scoped error boundary and a status panel
 * surface per-source health without clearing last-good sibling data.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import '../features/monitoring/styles/monitoring.css'
import { monitoringRequestContextFromSearchParams } from '../features/monitoring/api'
import { flowerManifest } from '../features/monitoring/config'
import { alignSeries } from '../features/monitoring/data'
import {
  beginMonitoringPerfTick,
  finishMonitoringPerfTick,
  measureMonitoringAlignment,
  PERFORMANCE_MARKS_ENABLED,
} from '../features/monitoring/perfMarks'
import { UPlotChart, type UPlotChartHandle } from '../features/monitoring/charts'
import {
  ChartDataTable,
  MonitoringErrorBoundary,
  MonitoringStatus,
  RoomAveragesTable,
  SensorValueTable,
  StatisticsTable,
  TimeRangeToolbar,
} from '../features/monitoring/components'
import type { MonitoringRange } from '../features/monitoring/state'
import { splitChartGroups } from '../features/monitoring/pages/chartGroups'
import { splitLiveByNode, tablePanel } from '../features/monitoring/pages/tablePanels'
import {
  FLOWER_DEFAULT_DURATION_MS,
  useMonitoringStore,
} from '../features/monitoring/pages/useMonitoringStore'

const ROOM = 'Flower Room'
const FLOWER_SERIES_SPECS = flowerManifest.panels.flatMap((panel) =>
  panel.kind === 'timeseries' ? panel.series : [],
)

function windowBounds(range: MonitoringRange, now: Date): { start: Date; end: Date } {
  if (range.kind === 'fixed') return { start: range.start, end: range.end }
  return { start: new Date(now.getTime() - range.duration), end: now }
}

const CHART_HEIGHT = { height: 640 }
const CLIMATE_CHART_HEIGHT = { height: 780 }

function FlowerMonitoringInner() {
  const [searchParams] = useSearchParams()
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(searchParams),
    [searchParams],
  )
  const { snapshot, store } = useMonitoringStore(ROOM, requestContext)
  const climateRef = useRef<UPlotChartHandle>(null)
  const deviceRef = useRef<UPlotChartHandle>(null)

  useEffect(() => {
    store.setLiveRange(FLOWER_DEFAULT_DURATION_MS)
  }, [store])

  const aligned = useMemo(() => {
    const now = new Date()
    if (PERFORMANCE_MARKS_ENABLED) {
      beginMonitoringPerfTick()
      return measureMonitoringAlignment(() =>
        alignSeries({ ...snapshot.data, range: snapshot.range, now, seriesSpecs: FLOWER_SERIES_SPECS }),
      )
    }
    return alignSeries({ ...snapshot.data, range: snapshot.range, now, seriesSpecs: FLOWER_SERIES_SPECS })
  }, [snapshot.data, snapshot.range])

  if (PERFORMANCE_MARKS_ENABLED) {
    useEffect(() => {
      finishMonitoringPerfTick(aligned.x[aligned.x.length - 1])
    }, [aligned])
  }

  const groups = useMemo(() => splitChartGroups(flowerManifest, aligned), [aligned])

  const averagesPanel = tablePanel(flowerManifest, 'flower-averages')
  const frontPanel = tablePanel(flowerManifest, 'flower-front')
  const backPanel = tablePanel(flowerManifest, 'flower-back')
  const statsPanel = tablePanel(flowerManifest, 'flower-statistics')
  const { front, back } = splitLiveByNode(snapshot.data.live)

  const now = new Date()
  const bounds = windowBounds(snapshot.range, now)

  const resetZoom = (): void => {
    climateRef.current?.resetZoom()
    deviceRef.current?.resetZoom()
  }

  const loading = snapshot.loading && snapshot.data.series.length === 0

  return (
    <div className="mon-page">
      <TimeRangeToolbar
        range={snapshot.range}
        isLive={snapshot.isLive}
        onLive={(duration) => store.setLiveRange(duration)}
        onFixedRange={(start, end) => store.setFixedRange(start, end)}
        onPause={() => store.pause()}
        onResume={() => store.resume()}
        onResetZoom={resetZoom}
        defaultDuration={FLOWER_DEFAULT_DURATION_MS}
      />

      <MonitoringStatus
        errors={snapshot.errors}
        tailLoading={snapshot.tailLoading}
        reconciling={snapshot.reconciling}
        anchorQuality={snapshot.data.anchorQuality}
        projectionRevision={snapshot.data.projectionRevision}
        runtimeSnapshotVersion={snapshot.data.runtimeSnapshotVersion}
        isLive={snapshot.isLive}
        onRetry={() => store.retry()}
        onPause={() => store.pause()}
        onResume={() => store.resume()}
      />
      {loading && (
        <div role="status" className="mon-banner">
          Loading monitoring data…
        </div>
      )}

      <div className="mon-layout">
        <aside className="mon-side">
        <section className="mon-card" aria-label="Averages">
          {averagesPanel && (
            <RoomAveragesTable
              title="Averages"
              rows={averagesPanel.rows}
              front={front}
              back={back}
            />
          )}
        </section>
        <section className="mon-card" aria-label="Front Cluster">
          {frontPanel && (
            <SensorValueTable
              title="Front Cluster"
              rows={frontPanel.rows}
              values={snapshot.data.live}
              nodeSuffix="f"
            />
          )}
        </section>
        <section className="mon-card" aria-label="Back Cluster">
          {backPanel && (
            <SensorValueTable
              title="Back Cluster"
              rows={backPanel.rows}
              values={snapshot.data.live}
              nodeSuffix="b"
            />
          )}
        </section>
        </aside>

        <div className="mon-main">
      <section className="mon-card" aria-label="Flower climate conditions">
        <h2 className="mon-card__title">Flower climate conditions</h2>
        {snapshot.data.series.length === 0 ? (
          <div style={CLIMATE_CHART_HEIGHT} />
        ) : (
          <div style={CLIMATE_CHART_HEIGHT}>
            <UPlotChart
              ref={climateRef}
              data={groups.climate}
              range={bounds}
              onZoom={(r) => store.setFixedRange(r.start, r.end)}
              title="Flower climate conditions"
              description="Temperature, relative humidity and VPD over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Flower climate conditions" data={groups.climate} />
      </section>

      <section className="mon-card" aria-label="Flower atmosphere & equipment">
        <h2 className="mon-card__title">Flower atmosphere &amp; equipment</h2>
        {snapshot.data.series.length === 0 ? (
          <div style={CHART_HEIGHT} />
        ) : (
          <div style={CHART_HEIGHT}>
            <UPlotChart
              ref={deviceRef}
              data={groups.device}
              range={bounds}
              onZoom={(r) => store.setFixedRange(r.start, r.end)}
              title="Flower atmosphere & equipment"
              description="CO2 and pressure over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Flower atmosphere & equipment" data={groups.device} />
      </section>

      <section className="mon-card" aria-label="Statistics - All Available Sensors">
        {statsPanel && (
          <StatisticsTable
            title="Statistics - All Available Sensors"
            rows={statsPanel.rows}
            statistics={snapshot.data.statistics}
          />
        )}
      </section>
        </div>
      </div>
    </div>
  )
}

export default function FlowerMonitoring() {
  return (
    <MonitoringErrorBoundary>
      <FlowerMonitoringInner />
    </MonitoringErrorBoundary>
  )
}
