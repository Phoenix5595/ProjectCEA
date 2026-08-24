/**
 * Veg Room monitoring page (native, no Grafana iframe).
 *
 * Consumes the Veg manifest, a page-level `MonitoringStore`, and the reusable
 * chart/table primitives to render the canonical Veg dashboard: a time-range
 * toolbar, the Sensor Values and Statistics tables, and two uPlot chart regions
 * (climate and device) each with an accessible "View data as table"
 * alternative. Veg is an unsplit room, so `main` is the only sensor node. A
 * scoped error boundary and a status panel surface per-source health without
 * clearing last-good sibling data.
 */
import { useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import '../features/monitoring/styles/monitoring.css'
import { monitoringRequestContextFromSearchParams } from '../features/monitoring/api'
import { vegManifest } from '../features/monitoring/config'
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
  SensorValueTable,
  StatisticsTable,
  TimeRangeToolbar,
} from '../features/monitoring/components'
import type { MonitoringRange } from '../features/monitoring/state'
import { splitChartGroups } from '../features/monitoring/pages/chartGroups'
import { tablePanel } from '../features/monitoring/pages/tablePanels'
import {
  useMonitoringStore,
  VEG_DEFAULT_DURATION_MS,
} from '../features/monitoring/pages/useMonitoringStore'

const ROOM = 'Veg Room'
const VEG_SERIES_SPECS = vegManifest.panels.flatMap((panel) =>
  panel.kind === 'timeseries' ? panel.series : [],
)

function windowBounds(range: MonitoringRange, now: Date): { start: Date; end: Date } {
  if (range.kind === 'fixed') return { start: range.start, end: range.end }
  return { start: new Date(now.getTime() - range.duration), end: now }
}

const CHART_HEIGHT = { height: 640 }
const CLIMATE_CHART_HEIGHT = { height: 780 }

function VegetationMonitoringInner() {
  const [searchParams] = useSearchParams()
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(searchParams),
    [searchParams],
  )
  const { snapshot, store } = useMonitoringStore(ROOM, requestContext)
  const climateRef = useRef<UPlotChartHandle>(null)
  const deviceRef = useRef<UPlotChartHandle>(null)

  useEffect(() => {
    store.setLiveRange(VEG_DEFAULT_DURATION_MS)
  }, [store])

  const aligned = useMemo(() => {
    const now = new Date()
    if (PERFORMANCE_MARKS_ENABLED) {
      beginMonitoringPerfTick()
      return measureMonitoringAlignment(() =>
        alignSeries({ ...snapshot.data, range: snapshot.range, now, seriesSpecs: VEG_SERIES_SPECS }),
      )
    }
    return alignSeries({ ...snapshot.data, range: snapshot.range, now, seriesSpecs: VEG_SERIES_SPECS })
  }, [snapshot.data, snapshot.range])

  if (PERFORMANCE_MARKS_ENABLED) {
    useEffect(() => {
      finishMonitoringPerfTick(aligned.x[aligned.x.length - 1])
    }, [aligned])
  }

  const groups = useMemo(() => splitChartGroups(vegManifest, aligned), [aligned])

  const valuesPanel = tablePanel(vegManifest, 'veg-values')
  const statsPanel = tablePanel(vegManifest, 'veg-statistics')

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
        defaultDuration={VEG_DEFAULT_DURATION_MS}
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
        <section className="mon-card" aria-label="Sensor Values">
          {valuesPanel && (
            <SensorValueTable
              title="Sensor Values"
              rows={valuesPanel.rows}
              values={snapshot.data.live}
              nodeSuffix="v"
            />
          )}
        </section>
        </aside>

        <div className="mon-main">
      <section className="mon-card" aria-label="Veg climate conditions">
        <h2 className="mon-card__title">Veg climate conditions</h2>
        {snapshot.data.series.length === 0 ? (
          <div style={CLIMATE_CHART_HEIGHT} />
        ) : (
          <div style={CLIMATE_CHART_HEIGHT}>
            <UPlotChart
              ref={climateRef}
              data={groups.climate}
              range={bounds}
              onZoom={(r) => store.setFixedRange(r.start, r.end)}
              title="Veg climate conditions"
              description="Temperature, relative humidity and VPD over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Veg climate conditions" data={groups.climate} />
      </section>

      <section className="mon-card" aria-label="Veg atmosphere & equipment">
        <h2 className="mon-card__title">Veg atmosphere &amp; equipment</h2>
        {snapshot.data.series.length === 0 ? (
          <div style={CHART_HEIGHT} />
        ) : (
          <div style={CHART_HEIGHT}>
            <UPlotChart
              ref={deviceRef}
              data={groups.device}
              range={bounds}
              onZoom={(r) => store.setFixedRange(r.start, r.end)}
              title="Veg atmosphere & equipment"
              description="Pressure and device output over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Veg atmosphere & equipment" data={groups.device} />
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

export default function VegetationMonitoring() {
  return (
    <MonitoringErrorBoundary>
      <VegetationMonitoringInner />
    </MonitoringErrorBoundary>
  )
}
