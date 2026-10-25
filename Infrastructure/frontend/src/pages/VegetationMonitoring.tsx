/**
 * Veg Room monitoring page (native monitoring).
 *
 * Monitoring and Historical are separate pages that share this component
 * (R2): the pathname decides the surface and the page is keyed by it, so a
 * route change always remounts readers and stores with the right mode.
 *
 * Monitoring (`/vegetation/monitoring`, live presets 1 h–7 d) owns the
 * operational surface: recorded + projected effective setpoints, a live tail,
 * and the future duration/9 area in live mode. It never renders the bounded
 * all-history overview. A fixed range longer than seven days requested here
 * navigates to Historical with that exact range (R3) instead of loading a
 * long window from the detailed endpoints. A drag-zoom always stays on this
 * page — a selection ending at/after now becomes a live range with duration
 * max(5 min, now − start), any other selection becomes a fixed range. A live
 * window or a fixed range of at most seven days mounts the canonical detailed
 * dashboard: the Sensor Values and Statistics tables and two uPlot chart
 * regions (climate and device), each with an accessible "View data as table"
 * alternative. Veg is an unsplit room, so `main` is the only sensor node. A
 * scoped error boundary and a status panel surface per-source health without
 * clearing last-good sibling data.
 *
 * Historical (`/vegetation/historical`) is analysis-only: fixed ranges only,
 * no projection and no future area, recorded setpoints only. A range of at
 * most seven days uses the same detailed dashboard with the historical store
 * mode; longer ranges use the bounded all-history overview. Zooming either
 * reader applies the exact selection as the new fixed range, floored to five
 * minutes by extending its end, and never leaves Historical. Reset Zoom
 * restores the range held before the latest zoom from a page-held stack. The
 * toolbar replaces the live presets with fixed history presets plus the
 * Calendar current-run action.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useLocation, useNavigate, useSearchParams } from 'react-router-dom'

import { ArchiveLog } from '../features/grow-archives/ArchiveLog'
import { ArchiveView } from '../features/grow-archives/ArchiveView'
import { ArchiveApi } from '../features/grow-archives/api'
import {
  createArchiveNavigationState,
  loadedSelectionFromNavigation,
  readArchiveNavigationState,
  releaseLoadedSelection,
} from '../features/grow-archives/selection'
import type { ArchiveSelection } from '../features/grow-archives/selection'
import '../features/monitoring/styles/monitoring.css'
import { HistoryOverviewView } from '../features/history-overview'
import { monitoringRequestContextFromSearchParams } from '../features/monitoring/api'
import type { MonitoringRequestContext } from '../features/monitoring/api'
import {
  UPlotChart,
  type MonitoringPanelChartFeed,
  type UPlotChartHandle,
} from '../features/monitoring/charts'
import { createMonitoringPanelChartFeed } from '../features/monitoring/charts/MonitoringChartFeed'
import {
  ChartDataTable,
  HISTORICAL_DEFAULT_DURATION_MS,
  MAX_RANGE_MS,
  MIN_RANGE_MS,
  MonitoringErrorBoundary,
  MonitoringFreshness,
  SensorValueTable,
  StatisticsTable,
  TimeRangeToolbar,
  parseUrlRange,
  withSerializedRange,
} from '../features/monitoring/components'
import type { HistoricalToolbarControl } from '../features/monitoring/components'
import { HistoricalViewSwitch } from '../features/monitoring/components/TimeRangeToolbar'
import { vegManifest } from '../features/monitoring/config'
import { createPanelAlignment } from '../features/monitoring/data'
import { timeseriesPanels } from '../features/monitoring/pages/chartGroups'
import { tablePanel } from '../features/monitoring/pages/tablePanels'
import { useCalendarCurrentRun } from '../features/monitoring/pages/useCalendarCurrentRun'
import { useMonitoringRangeBudget } from '../features/monitoring/pages/useMonitoringRangeBudget'
import {
  useMonitoringStore,
  VEG_DEFAULT_DURATION_MS,
} from '../features/monitoring/pages/useMonitoringStore'
import type { FixedRange, MonitoringRange } from '../features/monitoring/state'

const ROOM = 'Veg Room'
/** Historical route path; the regular Monitoring path keeps the live presets. */
const HISTORICAL_PATH = '/vegetation/historical'
const MONITORING_PATH = '/vegetation/monitoring'
const VEG_SERIES_SPECS = vegManifest.panels.flatMap(panel =>
  panel.kind === 'timeseries' ? panel.series : []
)

const CHART_HEIGHT = { height: 320 }
const CLIMATE_CHART_HEIGHT = { height: 780 }
const VEG_TIMESERIES_PANELS = timeseriesPanels(vegManifest)
/** Distinct value families of the first panel drive its placeholder axis count. */
const PRIMARY_Y_AXIS_COUNT = Math.max(
  1,
  new Set(VEG_TIMESERIES_PANELS[0]?.series.map(series => series.unit) ?? []).size
)

interface VegetationChartFeeds {
  readonly climate: MonitoringPanelChartFeed
  readonly device: MonitoringPanelChartFeed
}

interface MonitoringPageRangeProps {
  readonly range: MonitoringRange
  readonly onRangeChange: (next: MonitoringRange) => void
  /** Chart zoom/drag selections, resolved per surface (R3). */
  readonly onChartSelection: (start: Date, end: Date) => void
  readonly requestContext: MonitoringRequestContext | undefined
  /** Historical-route toolbar bundle; undefined on the regular Monitoring route. */
  readonly historical: HistoricalToolbarControl | undefined
  /** Store mode: Monitoring follows live data and projections, Historical does not. */
  readonly mode: 'monitoring' | 'historical'
  /** Historical range-owner reset; undefined on Monitoring, which resets chart scales. */
  readonly onResetZoom?: () => void
  readonly resetZoomDisabled?: boolean
}

interface MonitoringOverviewRangeProps {
  readonly range: FixedRange
  readonly onRangeChange: (next: MonitoringRange) => void
  /** Overview zoom/click selections: apply the selection in place (R3). */
  readonly onChartSelection: (start: Date, end: Date) => void
  readonly requestContext: MonitoringRequestContext | undefined
  /** Historical-route toolbar bundle; always defined on the overview surface. */
  readonly historical: HistoricalToolbarControl | undefined
  readonly onResetZoom: () => void
  readonly resetZoomDisabled: boolean
}

/**
 * The all-history overview is a Historical reader only (R2). Monitoring never
 * renders it: a longer range is routed to Historical instead.
 */
function isOverviewRange(range: MonitoringRange): range is FixedRange {
  return (
    range.kind === 'fixed' && range.end.getTime() - range.start.getTime() > MAX_RANGE_MS
  )
}

/** Last-31-days fallback for a Historical entry with no usable fixed range. */
function defaultHistoricalRange(): FixedRange {
  const end = new Date()
  return { kind: 'fixed', start: new Date(end.getTime() - HISTORICAL_DEFAULT_DURATION_MS), end }
}

/** Parse the Monitoring URL before any reader mounts; 'none' falls back to live. */
function resolveMonitoringPageRange(searchParams: URLSearchParams): MonitoringRange {
  const parsed = parseUrlRange(searchParams, null)
  return parsed.kind === 'live'
    ? { kind: 'live', duration: parsed.duration }
    : parsed.kind === 'fixed'
      ? { kind: 'fixed', start: parsed.start, end: parsed.end }
      : { kind: 'live', duration: VEG_DEFAULT_DURATION_MS }
}

/** Historical is fixed-only: every non-fixed URL resolves to the survey default. */
function resolveHistoricalPageRange(searchParams: URLSearchParams): FixedRange {
  const parsed = parseUrlRange(searchParams, null)
  return parsed.kind === 'fixed'
    ? { kind: 'fixed', start: parsed.start, end: parsed.end }
    : defaultHistoricalRange()
}

function VegetationMonitoringDetailed({
  range,
  onRangeChange,
  onChartSelection,
  requestContext,
  historical,
  mode,
  onResetZoom,
  resetZoomDisabled,
}: MonitoringPageRangeProps) {
  const { snapshot, store } = useMonitoringStore(
    ROOM,
    requestContext,
    mode,
    VEG_DEFAULT_DURATION_MS
  )
  const { reportBudget, placeholderRef } = useMonitoringRangeBudget(store, {
    yAxisCount: PRIMARY_Y_AXIS_COUNT,
  })
  const climateRef = useRef<UPlotChartHandle>(null)
  const deviceRef = useRef<UPlotChartHandle>(null)
  const chartFeedsRef = useRef<VegetationChartFeeds | null>(null)
  if (chartFeedsRef.current === null) {
    chartFeedsRef.current = {
      climate: createMonitoringPanelChartFeed({
        alignment: createPanelAlignment(),
        panel: VEG_TIMESERIES_PANELS[0],
        seriesSpecs: VEG_SERIES_SPECS,
      }),
      device: createMonitoringPanelChartFeed({
        alignment: createPanelAlignment(),
        panel: VEG_TIMESERIES_PANELS[1],
        seriesSpecs: VEG_SERIES_SPECS,
      }),
    }
  }
  const chartFeeds = chartFeedsRef.current

  // Mirror the outer-owned range into the store. This branch only mounts for
  // a live window or a fixed range of at most seven days, so the mirrored
  // range can never overload the detailed endpoints.
  useEffect(() => {
    if (range.kind === 'fixed') store.setFixedRange(range.start, range.end)
    else store.setLiveRange(range.duration)
  }, [store, range])

  useEffect(() => {
    const initialSnapshot = store.getSnapshot()
    const unsubscribeClimate = chartFeeds.climate.connect(store, initialSnapshot)
    const unsubscribeDevice = chartFeeds.device.connect(store, initialSnapshot)
    return () => {
      unsubscribeClimate()
      unsubscribeDevice()
    }
  }, [chartFeeds, store])

  const groups = { climate: chartFeeds.climate.getData(), device: chartFeeds.device.getData() }

  const valuesPanel = tablePanel(vegManifest, 'veg-values')
  const statsPanel = tablePanel(vegManifest, 'veg-statistics')

  const zoomToRange = useCallback(
    (range: { start: Date; end: Date }): void => {
      onChartSelection(range.start, range.end)
    },
    [onChartSelection]
  )

  const loading = snapshot.loading && snapshot.data.series.length === 0

  return (
    <div className="mon-page">
      <TimeRangeToolbar
        maxRangeMs={null}
        suppressInitialUrlParse={true}
        range={snapshot.range}
        isLive={snapshot.isLive}
        onLive={duration => onRangeChange({ kind: 'live', duration })}
        onFixedRange={(start, end) => onRangeChange({ kind: 'fixed', start, end })}
        onPause={() => store.pause()}
        onResume={() => store.resume()}
        onResetZoom={
          onResetZoom ??
          (() => {
            climateRef.current?.resetZoom()
            deviceRef.current?.resetZoom()
          })
        }
        resetZoomDisabled={resetZoomDisabled}
        defaultDuration={VEG_DEFAULT_DURATION_MS}
        liveDuration={store.lastLiveDurationMs()}
        historical={historical}
        monitoring={
          mode === 'monitoring'
            ? {
                errors: snapshot.errors,
                tailLoading: snapshot.tailLoading,
                reconciling: snapshot.reconciling,
                anchorQuality: snapshot.data.anchorQuality,
                projectionRevision: snapshot.data.projectionRevision,
                runtimeSnapshotVersion: snapshot.data.runtimeSnapshotVersion,
                lastGoodRangeAt: snapshot.lastGoodRangeAt,
                rangeErrorAt: snapshot.rangeErrorAt,
                onRetry: () => store.retry(),
              }
            : undefined
        }
      />

      {loading && (
        <div role="status" className="mon-banner">
          Loading monitoring data…
        </div>
      )}

      <div className="mon-layout">
        <aside className="mon-side">
          <section className="mon-card" aria-label="Sensor Values">
            <MonitoringFreshness
              lastGoodAt={snapshot.lastGoodRangeAt}
              errorAt={snapshot.rangeErrorAt}
            />
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
            <MonitoringFreshness
              lastGoodAt={snapshot.lastGoodRangeAt}
              errorAt={snapshot.rangeErrorAt}
            />
            {snapshot.data.series.length === 0 ? (
              <div style={CLIMATE_CHART_HEIGHT}>
                <div className="mon-chart">
                  <div ref={placeholderRef} className="mon-chart__frame" />
                </div>
              </div>
            ) : (
              <div style={CLIMATE_CHART_HEIGHT}>
                <UPlotChart
                  ref={climateRef}
                  feed={chartFeeds.climate}
                  onZoom={zoomToRange}
                  onRequestBudgetChange={budget => reportBudget('climate', budget)}
                  bucketIntervalSeconds={snapshot.data.sensorIntervalSeconds}
                  title="Veg climate conditions"
                  description="Temperature, relative humidity and VPD over the selected time range. Toggle series with the sensor boxes, change the range with the toolbar, and open the table below for the underlying data."
                />
              </div>
            )}
            <ChartDataTable title="Veg climate conditions" data={groups.climate} />
          </section>

          <section className="mon-card" aria-label="Veg atmosphere & equipment">
            <h2 className="mon-card__title">Veg atmosphere &amp; equipment</h2>
            <MonitoringFreshness
              lastGoodAt={snapshot.lastGoodRangeAt}
              errorAt={snapshot.rangeErrorAt}
            />
            {snapshot.data.series.length === 0 ? (
              <div style={CHART_HEIGHT}>
                <div className="mon-chart">
                  <div className="mon-chart__frame" />
                </div>
              </div>
            ) : (
              <div style={CHART_HEIGHT}>
                <UPlotChart
                  ref={deviceRef}
                  feed={chartFeeds.device}
                  onZoom={zoomToRange}
                  onRequestBudgetChange={budget => reportBudget('device', budget)}
                  bucketIntervalSeconds={snapshot.data.sensorIntervalSeconds}
                  title="Veg atmosphere & equipment"
                  description="Pressure and device output over the selected time range. Toggle series with the sensor boxes, change the range with the toolbar, and open the table below for the underlying data."
                />
              </div>
            )}
            <ChartDataTable title="Veg atmosphere & equipment" data={groups.device} />
          </section>

          <section className="mon-card" aria-label="Statistics - All Available Sensors">
            <MonitoringFreshness
              lastGoodAt={snapshot.lastGoodRangeAt}
              errorAt={snapshot.rangeErrorAt}
            />
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

function VegetationMonitoringOverview({
  range,
  requestContext,
  onRangeChange,
  onChartSelection,
  historical,
  onResetZoom,
  resetZoomDisabled,
}: MonitoringOverviewRangeProps) {
  return (
    <div className="mon-page">
      <TimeRangeToolbar
        maxRangeMs={null}
        suppressInitialUrlParse={true}
        range={range}
        isLive={false}
        onLive={duration => onRangeChange({ kind: 'live', duration })}
        onFixedRange={(start, end) => onRangeChange({ kind: 'fixed', start, end })}
        onPause={() => undefined}
        onResume={() => undefined}
        onResetZoom={onResetZoom}
        resetZoomDisabled={resetZoomDisabled}
        defaultDuration={VEG_DEFAULT_DURATION_MS}
        historical={historical}
      />
      <HistoryOverviewView
        room={ROOM}
        range={range}
        requestContext={requestContext}
        manifest={vegManifest}
        historicalDrillDown={true}
        onDetail={onChartSelection}
        climateHeight={CLIMATE_CHART_HEIGHT.height}
        deviceHeight={CHART_HEIGHT.height}
      />
    </div>
  )
}

function VegetationMonitoringRangeOwner() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const location = useLocation()
  const { pathname } = location
  const historicalRoute = pathname === HISTORICAL_PATH
  const navigationState = readArchiveNavigationState(location.state)
  const activeArchive = loadedSelectionFromNavigation(location.state, ROOM)
  const [archiveMode, setArchiveMode] = useState<'range' | 'archives'>(() =>
    navigationState === null ? 'range' : 'archives'
  )
  const requestContext = useMemo(
    () => monitoringRequestContextFromSearchParams(searchParams),
    [searchParams]
  )
  const archiveApi = useMemo(() => new ArchiveApi(), [])
  // Calendar state is not fetched while the archive log/graph owns this page.
  const currentRun = useCalendarCurrentRun(
    historicalRoute && archiveMode === 'range' && navigationState === null ? ROOM : null,
    requestContext
  )
  // Parse the URL before mounting any native reader. Archive ranges are owned
  // by ArchiveView instead and never enter this reader branch. Monitoring
  // resolves live/short-fixed ranges; Historical always resolves a fixed one.
  const [range, setRange] = useState<MonitoringRange>(() =>
    historicalRoute ? resolveHistoricalPageRange(searchParams) : resolveMonitoringPageRange(searchParams)
  )
  const rangeRef = useRef(range)
  rangeRef.current = range
  // Ranges to restore on Historical Reset Zoom; presets and absolute inputs
  // clear the stack, chart zooms push the range they replaced.
  const [zoomStack, setZoomStack] = useState<readonly FixedRange[]>([])
  const archiveInitialRange = useMemo(() => {
    if (historicalRoute) return undefined
    const parsed = parseUrlRange(searchParams, null)
    return parsed.kind === 'fixed'
      ? { kind: 'fixed' as const, start: parsed.start, end: parsed.end }
      : undefined
  }, [historicalRoute, searchParams])

  const routeToHistorical = useCallback(
    (next: FixedRange): void => {
      const query = withSerializedRange(searchParams, next)
      navigate({ pathname: HISTORICAL_PATH, search: `?${query.toString()}` })
    },
    [navigate, searchParams]
  )

  const applyRange = useCallback(
    (next: MonitoringRange): void => {
      if (historicalRoute) {
        // Historical is fixed-only; the toolbar never offers live presets, so
        // a live callback can only come from a stale live URL and is ignored.
        if (next.kind !== 'fixed') return
        setZoomStack([])
        setRange(next)
        return
      }
      // R3: the only cross-page navigation is a Monitoring request longer
      // than seven days, which opens Historical with the exact range.
      if (next.kind === 'fixed' && next.end.getTime() - next.start.getTime() > MAX_RANGE_MS) {
        routeToHistorical(next)
        return
      }
      setRange(next)
    },
    [historicalRoute, routeToHistorical]
  )

  const applyChartSelection = useCallback(
    (start: Date, end: Date): void => {
      if (historicalRoute) {
        const previous = rangeRef.current
        // Exact in-place zoom, floored to five minutes by extending the end.
        const startMs = start.getTime()
        const endMs = Math.max(end.getTime(), startMs + MIN_RANGE_MS)
        const next: FixedRange = {
          kind: 'fixed',
          start: new Date(startMs),
          end: new Date(endMs),
        }
        if (previous.kind === 'fixed') setZoomStack(stack => [...stack, previous])
        setRange(next)
        return
      }
      // Monitoring: a selection ending at/after now keeps following live with
      // the selected past duration; every other selection is a fixed range
      // (including a projection-only window entirely after now).
      const nowMs = Date.now()
      const startMs = start.getTime()
      const endMs = end.getTime()
      if (startMs < nowMs && endMs >= nowMs) {
        const duration = Math.min(
          MAX_RANGE_MS,
          Math.max(MIN_RANGE_MS, nowMs - startMs)
        )
        setRange({ kind: 'live', duration })
        return
      }
      setRange({ kind: 'fixed', start, end })
    },
    [historicalRoute]
  )

  const resetZoom = useCallback((): void => {
    if (zoomStack.length === 0) return
    const next = zoomStack[zoomStack.length - 1]
    setZoomStack(zoomStack.slice(0, -1))
    setRange(next)
  }, [zoomStack])

  const applyCurrentRun = useCallback((): void => {
    if (currentRun.start === undefined) return
    applyRange({ kind: 'fixed', start: currentRun.start, end: new Date() })
  }, [applyRange, currentRun.start])

  const onArchiveLoaded = useCallback(async (selection: ArchiveSelection): Promise<void> => {
    if (activeArchive !== undefined && activeArchive.load_id !== selection.load_id) {
      await archiveApi.releaseLoad(activeArchive.load_id)
      releaseLoadedSelection(activeArchive.load_id)
    }
    setArchiveMode('archives')
    navigate(
      { pathname: location.pathname, search: location.search },
      { replace: true, state: createArchiveNavigationState(selection, 'view') }
    )
  }, [activeArchive, archiveApi, location.pathname, location.search, navigate])

  const leaveArchiveSurface = useCallback((): void => {
    if (activeArchive !== undefined) {
      navigate(
        { pathname: location.pathname, search: location.search },
        { replace: true, state: createArchiveNavigationState(activeArchive, 'view') }
      )
      return
    }
    if (navigationState !== null) {
      setArchiveMode('range')
      setRange({ kind: 'live', duration: VEG_DEFAULT_DURATION_MS })
      navigate({ pathname: MONITORING_PATH, search: '' }, { replace: true, state: null })
      return
    }
    setArchiveMode('range')
  }, [activeArchive, location.pathname, location.search, navigate, navigationState])

  const returnToArchiveLog = useCallback((archiveRange: FixedRange): void => {
    if (activeArchive === undefined) return
    if (historicalRoute) {
      navigate(
        { pathname: HISTORICAL_PATH, search: location.search },
        { replace: true, state: createArchiveNavigationState(activeArchive, 'log') }
      )
      return
    }
    const query = withSerializedRange(searchParams, archiveRange)
    navigate(
      { pathname: HISTORICAL_PATH, search: `?${query.toString()}` },
      { state: createArchiveNavigationState(activeArchive, 'log') }
    )
  }, [activeArchive, historicalRoute, location.search, navigate, searchParams])

  const handoffArchiveRange = useCallback((archiveRange: FixedRange): void => {
    if (activeArchive === undefined) return
    const query = withSerializedRange(searchParams, archiveRange)
    navigate(
      { pathname: MONITORING_PATH, search: `?${query.toString()}` },
      { state: createArchiveNavigationState(activeArchive, 'view') }
    )
  }, [activeArchive, navigate, searchParams])

  const exitToLiveMonitoring = useCallback((): void => {
    setArchiveMode('range')
    setRange({ kind: 'live', duration: VEG_DEFAULT_DURATION_MS })
    navigate({ pathname: MONITORING_PATH, search: '' }, { state: null })
  }, [navigate])

  const historicalToolbar: HistoricalToolbarControl | undefined =
    historicalRoute && navigationState === null && archiveMode === 'range'
      ? {
          currentRun: { ready: currentRun.ready, title: currentRun.title, onSelect: applyCurrentRun },
          view: archiveMode,
          onViewChange: setArchiveMode,
        }
      : undefined

  const archiveSurface = navigationState !== null || (historicalRoute && archiveMode === 'archives')
  if (archiveSurface) {
    const showLog = navigationState === null ||
      navigationState.surface === 'log' ||
      activeArchive === undefined
    return (
      <div className="mon-page">
        {showLog ? (
          <>
            <HistoricalViewSwitch
              view="archives"
              standalone={true}
              onChange={view => { if (view === 'range') leaveArchiveSurface() }}
            />
            <ArchiveLog
              room={ROOM}
              focusDatasetId={navigationState?.selection.dataset_id}
              onLoaded={onArchiveLoaded}
            />
          </>
        ) : activeArchive !== undefined ? (
          <ArchiveView
            selection={activeArchive}
            room={ROOM}
            manifest={vegManifest}
            initialRange={archiveInitialRange}
            onBackToLog={returnToArchiveLog}
            onExit={exitToLiveMonitoring}
            onHandoffToMonitoring={historicalRoute ? handoffArchiveRange : undefined}
          />
        ) : null}
      </div>
    )
  }

  // R3: a Monitoring fixed request longer than seven days opens Historical
  // with the exact range instead of rendering a long window on this page.
  if (
    !historicalRoute &&
    navigationState === null &&
    range.kind === 'fixed' &&
    range.end.getTime() - range.start.getTime() > MAX_RANGE_MS
  ) {
    return <Navigate to={{ pathname: HISTORICAL_PATH, search: location.search }} replace />
  }

  if (historicalRoute) {
    // Historical is fixed-only. Archive exits replace this instance through a
    // keyed remount, so a non-fixed range here is an unreachable state.
    if (range.kind !== 'fixed') return null
    if (isOverviewRange(range)) {
      return (
        <VegetationMonitoringOverview
          range={range}
          requestContext={requestContext}
          onRangeChange={applyRange}
          onChartSelection={applyChartSelection}
          historical={historicalToolbar}
          onResetZoom={resetZoom}
          resetZoomDisabled={zoomStack.length === 0}
        />
      )
    }
    return (
      <VegetationMonitoringDetailed
        range={range}
        onRangeChange={applyRange}
        onChartSelection={applyChartSelection}
        requestContext={requestContext}
        historical={historicalToolbar}
        mode="historical"
        onResetZoom={resetZoom}
        resetZoomDisabled={zoomStack.length === 0}
      />
    )
  }
  return (
    <VegetationMonitoringDetailed
      range={range}
      onRangeChange={applyRange}
      onChartSelection={applyChartSelection}
      requestContext={requestContext}
      historical={historicalToolbar}
      mode="monitoring"
    />
  )
}

export default function VegetationMonitoring() {
  const { pathname } = useLocation()
  return (
    <MonitoringErrorBoundary>
      <VegetationMonitoringRangeOwner
        key={pathname === HISTORICAL_PATH ? 'historical' : 'monitoring'}
      />
    </MonitoringErrorBoundary>
  )
}
