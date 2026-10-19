/**
 * Veg Room monitoring page (native monitoring).
 *
 * The page owns its time range: URL parameters are parsed with the fixed-range
 * cap opt-out (`maxRangeMs: null`) before any data surface mounts, so a long
 * fixed range renders the bounded all-history overview and never reaches the
 * detailed `MonitoringStore`. A live window or a fixed range of at most seven
 * days mounts the canonical detailed dashboard: the Sensor Values and
 * Statistics tables and two uPlot chart regions (climate and device), each
 * with an accessible "View data as table" alternative. Veg is an unsplit room,
 * so `main` is the only sensor node. A scoped error boundary and a status
 * panel surface per-source health without clearing last-good sibling data.
 *
 * On the Historical route the toolbar replaces the live presets with fixed
 * history presets plus the Calendar current-run action, and chart
 * zoom/click/drag follows the origin-aware two-stage rule: a sub-seven-day
 * selection on the overview snaps to an exactly-seven-day window, while the
 * detailed reader hands sub-seven-day selections to the regular Monitoring
 * page with the exact range.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'

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
import { HistoryOverviewView, resolveHistoricalSelection } from '../features/history-overview'
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
  MAX_RANGE_MS,
  MIN_HISTORY_MS,
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

interface VegetationChartFeeds {
  readonly climate: MonitoringPanelChartFeed
  readonly device: MonitoringPanelChartFeed
}

interface MonitoringPageRangeProps {
  readonly range: MonitoringRange
  readonly onRangeChange: (next: MonitoringRange) => void
  /** Chart zoom/drag selections: origin-aware on Historical, plain fixed apply otherwise. */
  readonly onChartSelection: (start: Date, end: Date) => void
  readonly requestContext: MonitoringRequestContext | undefined
  /** Historical-route toolbar bundle; undefined on the regular Monitoring route. */
  readonly historical: HistoricalToolbarControl | undefined
}

interface MonitoringOverviewRangeProps {
  readonly range: FixedRange
  readonly onRangeChange: (next: MonitoringRange) => void
  /** Chart click/drag selections: origin-aware on Historical, plain fixed apply otherwise. */
  readonly onChartSelection: (start: Date, end: Date) => void
  readonly requestContext: MonitoringRequestContext | undefined
  /** Historical-route toolbar bundle; undefined on the regular Monitoring route. */
  readonly historical: HistoricalToolbarControl | undefined
}

/**
 * A fixed range beyond the detailed seven-day limit renders the bounded
 * all-history overview; live windows and short fixed ranges stay detailed.
 */
function isOverviewRange(range: MonitoringRange): range is FixedRange {
  return (
    range.kind === 'fixed' && range.end.getTime() - range.start.getTime() > MAX_RANGE_MS
  )
}

/** Parse the page URL before any reader mounts; 'none' falls back to live. */
function resolvePageRange(searchParams: URLSearchParams): MonitoringRange {
  const parsed = parseUrlRange(searchParams, null)
  return parsed.kind === 'live'
    ? { kind: 'live', duration: parsed.duration }
    : parsed.kind === 'fixed'
      ? { kind: 'fixed', start: parsed.start, end: parsed.end }
      : { kind: 'live', duration: VEG_DEFAULT_DURATION_MS }
}

function VegetationMonitoringDetailed({
  range,
  onRangeChange,
  onChartSelection,
  requestContext,
  historical,
}: MonitoringPageRangeProps) {
  const { snapshot, store } = useMonitoringStore(ROOM, requestContext)
  const { reportBudget } = useMonitoringRangeBudget(store)
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
        onResetZoom={() => {
          climateRef.current?.resetZoom()
          deviceRef.current?.resetZoom()
        }}
        defaultDuration={VEG_DEFAULT_DURATION_MS}
        historical={historical}
        monitoring={{
          errors: snapshot.errors,
          tailLoading: snapshot.tailLoading,
          reconciling: snapshot.reconciling,
          anchorQuality: snapshot.data.anchorQuality,
          projectionRevision: snapshot.data.projectionRevision,
          runtimeSnapshotVersion: snapshot.data.runtimeSnapshotVersion,
          lastGoodRangeAt: snapshot.lastGoodRangeAt,
          rangeErrorAt: snapshot.rangeErrorAt,
          onRetry: () => store.retry(),
        }}
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
              <div style={CLIMATE_CHART_HEIGHT} />
            ) : (
              <div style={CLIMATE_CHART_HEIGHT}>
                <UPlotChart
                  ref={climateRef}
                  feed={chartFeeds.climate}
                  onZoom={zoomToRange}
                  onRequestBudgetChange={budget => reportBudget('climate', budget)}
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
              <div style={CHART_HEIGHT} />
            ) : (
              <div style={CHART_HEIGHT}>
                <UPlotChart
                  ref={deviceRef}
                  feed={chartFeeds.device}
                  onZoom={zoomToRange}
                  onRequestBudgetChange={budget => reportBudget('device', budget)}
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
        defaultDuration={VEG_DEFAULT_DURATION_MS}
        historical={historical}
      />
      <HistoryOverviewView
        room={ROOM}
        range={range}
        requestContext={requestContext}
        manifest={vegManifest}
        historicalDrillDown={historical !== undefined}
        onDetail={onChartSelection}
        climateHeight={CLIMATE_CHART_HEIGHT.height}
        deviceHeight={CHART_HEIGHT.height}
      />
    </div>
  )
}

function VegetationMonitoringRangeOwner() {
  const [searchParams, setSearchParams] = useSearchParams()
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
  // by ArchiveView instead and never enter this reader branch.
  const [range, setRange] = useState<MonitoringRange>(() => resolvePageRange(searchParams))
  const rangeRef = useRef(range)
  rangeRef.current = range
  const archiveInitialRange = useMemo(() => {
    if (historicalRoute) return undefined
    const parsed = parseUrlRange(searchParams, null)
    return parsed.kind === 'fixed'
      ? { kind: 'fixed' as const, start: parsed.start, end: parsed.end }
      : undefined
  }, [historicalRoute, searchParams])

  const routeToMonitoring = useCallback(
    (start: Date, end: Date): void => {
      const query = `?${withSerializedRange(searchParams, { kind: 'fixed', start, end }).toString()}`
      navigate({ pathname: MONITORING_PATH, search: query })
    },
    [navigate, searchParams]
  )

  const setRangeWithBoundaryUrl = useCallback(
    (next: MonitoringRange): void => {
      if (isOverviewRange(next) || isOverviewRange(rangeRef.current)) {
        const merged = withSerializedRange(searchParams, next)
        if (merged.toString() !== searchParams.toString()) setSearchParams(merged)
      }
      setRange(next)
    },
    [setSearchParams, searchParams]
  )

  const applyRange = useCallback(
    (next: MonitoringRange): void => {
      if (
        historicalRoute &&
        next.kind === 'fixed' &&
        next.end.getTime() - next.start.getTime() < MIN_HISTORY_MS
      ) {
        routeToMonitoring(next.start, next.end)
        return
      }
      setRangeWithBoundaryUrl(next)
    },
    [historicalRoute, routeToMonitoring, setRangeWithBoundaryUrl]
  )

  const applyChartSelection = useCallback(
    (start: Date, end: Date): void => {
      if (!historicalRoute) {
        setRangeWithBoundaryUrl({ kind: 'fixed', start, end })
        return
      }
      const outcome = resolveHistoricalSelection(rangeRef.current, start, end)
      if (outcome.surface === 'monitoring') routeToMonitoring(outcome.start, outcome.end)
      else setRangeWithBoundaryUrl({ kind: 'fixed', start: outcome.start, end: outcome.end })
    },
    [historicalRoute, routeToMonitoring, setRangeWithBoundaryUrl]
  )

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
          onMonitoringRoute: routeToMonitoring,
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

  if (isOverviewRange(range)) {
    return (
      <VegetationMonitoringOverview
        range={range}
        requestContext={requestContext}
        onRangeChange={applyRange}
        onChartSelection={applyChartSelection}
        historical={historicalToolbar}
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
    />
  )
}

export default function VegetationMonitoring() {
  return (
    <MonitoringErrorBoundary>
      <VegetationMonitoringRangeOwner />
    </MonitoringErrorBoundary>
  )
}
