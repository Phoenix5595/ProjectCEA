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
import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import '../features/monitoring/styles/monitoring.css'
import { monitoringRequestContextFromSearchParams } from '../features/monitoring/api'
import { flowerManifest } from '../features/monitoring/config'
import { UPlotChart, type MonitoringPanelChartFeed, type UPlotChartHandle } from '../features/monitoring/charts'
import { createMonitoringPanelChartFeed } from '../features/monitoring/charts/MonitoringChartFeed'
import {
  ChartDataTable,
  MonitoringErrorBoundary,
  MonitoringFreshness,
  MonitoringStatus,
  RoomAveragesTable,
  SensorValueTable,
  StatisticsTable,
  TimeRangeToolbar,
} from '../features/monitoring/components'
import { timeseriesPanels } from '../features/monitoring/pages/chartGroups'
import { splitLiveByNode, tablePanel } from '../features/monitoring/pages/tablePanels'
import { createPanelAlignment } from '../features/monitoring/data'
import {
  FLOWER_DEFAULT_DURATION_MS,
  useMonitoringStore,
} from '../features/monitoring/pages/useMonitoringStore'
import { useMonitoringRangeBudget } from '../features/monitoring/pages/useMonitoringRangeBudget'

const ROOM = 'Flower Room'
const FLOWER_SERIES_SPECS = flowerManifest.panels.flatMap((panel) =>
  panel.kind === 'timeseries' ? panel.series : [],
)

const CHART_HEIGHT = { height: 640 }
const CLIMATE_CHART_HEIGHT = { height: 780 }
const FLOWER_TIMESERIES_PANELS = timeseriesPanels(flowerManifest)

interface FlowerChartFeeds {
  readonly climate: MonitoringPanelChartFeed
  readonly device: MonitoringPanelChartFeed
}

function FlowerMonitoringInner() {
  const [searchParams] = useSearchParams()
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(searchParams),
    [searchParams],
  )
  const { snapshot, store } = useMonitoringStore(ROOM, requestContext)
  const { reportBudget } = useMonitoringRangeBudget(store)
  const climateRef = useRef<UPlotChartHandle>(null)
  const deviceRef = useRef<UPlotChartHandle>(null)
  const chartFeedsRef = useRef<FlowerChartFeeds | null>(null)
  if (chartFeedsRef.current === null) {
    chartFeedsRef.current = {
      climate: createMonitoringPanelChartFeed({
        alignment: createPanelAlignment(),
        panel: FLOWER_TIMESERIES_PANELS[0],
        seriesSpecs: FLOWER_SERIES_SPECS,
      }),
      device: createMonitoringPanelChartFeed({
        alignment: createPanelAlignment(),
        panel: FLOWER_TIMESERIES_PANELS[1],
        seriesSpecs: FLOWER_SERIES_SPECS,
      }),
    }
  }
  const chartFeeds = chartFeedsRef.current

  useEffect(() => {
    store.setLiveRange(FLOWER_DEFAULT_DURATION_MS)
  }, [store])

  useEffect(() => {
    const unsubscribeClimate = chartFeeds.climate.connect(store, snapshot)
    const unsubscribeDevice = chartFeeds.device.connect(store, snapshot)
    return () => {
      unsubscribeClimate()
      unsubscribeDevice()
    }
  }, [chartFeeds, store])

  const groups = { climate: chartFeeds.climate.getData(), device: chartFeeds.device.getData() }

  const averagesPanel = tablePanel(flowerManifest, 'flower-averages')
  const frontPanel = tablePanel(flowerManifest, 'flower-front')
  const backPanel = tablePanel(flowerManifest, 'flower-back')
  const statsPanel = tablePanel(flowerManifest, 'flower-statistics')
  const { front, back } = splitLiveByNode(snapshot.data.live)

  const resetZoom = useCallback((): void => {
    climateRef.current?.resetZoom()
    deviceRef.current?.resetZoom()
  }, [])
  const zoomToRange = useCallback((range: { start: Date; end: Date }): void => {
    store.setFixedRange(range.start, range.end)
  }, [store])

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
        lastGoodRangeAt={snapshot.lastGoodRangeAt}
        rangeErrorAt={snapshot.rangeErrorAt}
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
          <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
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
          <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
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
          <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
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
        <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
        {snapshot.data.series.length === 0 ? (
          <div style={CLIMATE_CHART_HEIGHT} />
        ) : (
          <div style={CLIMATE_CHART_HEIGHT}>
            <UPlotChart
              ref={climateRef}
              feed={chartFeeds.climate}
              onZoom={zoomToRange}
              onRequestBudgetChange={(budget) => reportBudget('climate', budget)}
              title="Flower climate conditions"
              description="Temperature, relative humidity and VPD over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Flower climate conditions" data={groups.climate} />
      </section>

      <section className="mon-card" aria-label="Flower atmosphere & equipment">
        <h2 className="mon-card__title">Flower atmosphere &amp; equipment</h2>
        <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
        {snapshot.data.series.length === 0 ? (
          <div style={CHART_HEIGHT} />
        ) : (
          <div style={CHART_HEIGHT}>
            <UPlotChart
              ref={deviceRef}
              feed={chartFeeds.device}
              onZoom={zoomToRange}
              onRequestBudgetChange={(budget) => reportBudget('device', budget)}
              title="Flower atmosphere & equipment"
              description="CO2 and pressure over the selected time range. Toggle series with the legend, change the range with the toolbar, and open the table below for the underlying data."
            />
          </div>
        )}
        <ChartDataTable title="Flower atmosphere & equipment" data={groups.device} />
      </section>

      <section className="mon-card" aria-label="Statistics - All Available Sensors">
        <MonitoringFreshness lastGoodAt={snapshot.lastGoodRangeAt} errorAt={snapshot.rangeErrorAt} />
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
