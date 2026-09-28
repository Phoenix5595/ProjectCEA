/** Main dashboard page component: dense desktop command view. */
import { FlaskConical, Flower2, Sprout, Sun } from 'lucide-react'
import { useEffect, useState, useMemo, useCallback, type ReactNode } from 'react'

import FlowerGrowWizard from '../components/calendar/FlowerGrowWizard'
import GrowCalendar from '../components/calendar/GrowCalendar'
import { AppRibbon } from '../components/chrome/AppRibbon'
import { RibbonMenuButton } from '../components/chrome/ribbonMenuButton'
import {
  DashboardAlarmButton,
  DashboardAlarmSummary,
} from '../components/dashboard/DashboardAlarmSummary'
import DashboardCalendarInspector from '../components/dashboard/DashboardCalendarInspector'
import DashboardOperationsRail from '../components/dashboard/DashboardOperationsRail'
import {
  deriveClusterSensorStatus,
  deriveRoomControlContext,
  deriveRoomDecisionSummary,
  type RoomSensorStatus,
} from '../components/dashboard/dashboardStatus'
import { DashboardZoneRow } from '../components/dashboard/DashboardZoneRow'
import { MothernodeRibbon } from '../components/dashboard/MothernodeRibbon'
import { DASHBOARD_ROW_ZONES } from '../config/zones'
import { useTheme } from '../contexts/ThemeContext'
import { EventLog } from '../features/event-log/components/EventLog'
import { useEventLog } from '../features/event-log/state/useEventLog'
import { useActiveAlarms } from '../hooks/useActiveAlarms'
import { useCalendarEvents } from '../hooks/useCalendarEvents'
import { useControlSnapshot } from '../hooks/useControlSnapshot'
import { useDashboardLiveData } from '../hooks/useDashboardLiveData'
import { getDashboardMode, useDashboardScheduleContext } from '../hooks/useDashboardScheduleContext'
import { useDashboardTrends } from '../hooks/useDashboardTrends'
import { useSystemStatus } from '../hooks/useSystemStatus'
import { apiClient } from '../services/api'
import type { CalendarEventDto } from '../types/calendar'
import { nextRoomTransition } from '../utils/dashboardSchedule'

interface WeatherData {
  temperature: number
  humidity: number
  pressure: number
  wind_speed: number
  wind_direction: number | null
  description: string
  location: string
  timestamp: string
}

/** Room icons are semantic, not decorative: the same marks identify room-origin events. */
const ROOM_ICONS: Record<string, ReactNode> = {
  'Veg Room': <Sprout aria-hidden="true" className="size-3.5 text-emerald-400" />,
  'Flower Room': <Flower2 aria-hidden="true" className="size-3.5 text-pink-400" />,
  Lab: <FlaskConical aria-hidden="true" className="size-3.5 text-cyan-400" />,
}

/** Lower-row room map: the Lab is a third horizontal zone, ending at the SCADA column. */
const ROOM_MAP_ZONES = DASHBOARD_ROW_ZONES

export default function Dashboard() {
  const { theme, setTheme, themes } = useTheme()
  const live = useDashboardLiveData()
  const control = useControlSnapshot()
  const schedule = useDashboardScheduleContext()
  const trends = useDashboardTrends()
  const alarms = useActiveAlarms()
  const { systemStats, statusDevices, degraded } = useSystemStatus()
  const { entries: eventLogEntries } = useEventLog()

  const [weatherData, setWeatherData] = useState<WeatherData | null>(null)
  const [wizardOpen, setWizardOpen] = useState(false)
  const [alarmOpen, setAlarmOpen] = useState(false)
  const [now, setNow] = useState(() => new Date())
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(undefined)
  const [selectedDateEvents, setSelectedDateEvents] = useState<CalendarEventDto[]>([])
  const {
    events: calendarEvents,
    loading: calendarLoading,
    refresh: refreshCalendar,
  } = useCalendarEvents()

  const handleDaySelect = useCallback((date: Date | undefined, events: CalendarEventDto[]) => {
    setSelectedDate(date)
    setSelectedDateEvents(events)
  }, [])

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(timer)
  }, [])

  const roomPresentations = useMemo(
    () =>
      ROOM_MAP_ZONES.map(zone => {
        const isGrowRoom = zone.location !== 'Lab'
        const sensorClusters = zone.location === 'Flower Room' ? ['front', 'back'] : [zone.cluster]
        const sensorStatuses: Record<string, RoomSensorStatus> = Object.fromEntries(
          sensorClusters.map(
            cluster =>
              [
                cluster,
                deriveClusterSensorStatus(zone.location, cluster, live.sensorMeta, now.getTime()),
              ] as const
          )
        )
        return {
          zone,
          sensorStatuses,
          decisionSummary: isGrowRoom
            ? deriveRoomDecisionSummary(
                zone.location,
                sensorClusters,
                live.sensorData,
                trends.byRoom[zone.location] ?? {},
                live.devices
              )
            : undefined,
          controlContext: isGrowRoom
            ? deriveRoomControlContext(
                zone.location,
                control.snapshot,
                live.devices,
                Boolean(degraded?.active) || live.transport === 'degraded'
              )
            : undefined,
          activeMode: isGrowRoom
            ? getDashboardMode(schedule.modes, zone.location, zone.cluster)
            : null,
          modeError: isGrowRoom
            ? (schedule.modeErrors[`${zone.location}:${zone.cluster}`] ?? null)
            : null,
          nextTransition: isGrowRoom
            ? nextRoomTransition(schedule.schedules, zone.location, zone.cluster, now)
            : null,
          trendData: trends.byRoom[zone.location] ?? {},
        }
      }),
    [
      control.snapshot,
      degraded?.active,
      live.devices,
      live.sensorData,
      live.sensorMeta,
      live.transport,
      now,
      schedule.modeErrors,
      schedule.modes,
      schedule.schedules,
      trends.byRoom,
    ]
  )

  // Weather refresh (15 minutes)
  useEffect(() => {
    const refreshWeather = async () => {
      try {
        const weatherResponse = await apiClient.getLatestWeather()
        if (weatherResponse?.data) {
          const d = weatherResponse.data
          const temp = d.temp?.value ?? d.temperature?.value
          const rh = d.rh?.value ?? d.humidity?.value
          if (temp != null && rh != null) {
            setWeatherData({
              temperature: Number(temp),
              humidity: Number(rh),
              pressure: Number(d.pressure?.value ?? 0),
              wind_speed: Number(d.wind_speed?.value ?? 0),
              wind_direction:
                d.wind_direction?.value != null ? Number(d.wind_direction.value) : null,
              description: d.description?.value ?? 'N/A',
              location: 'Quebec City',
              timestamp: weatherResponse.timestamp ?? '',
            })
          }
        }
      } catch {
        // Silently fail weather updates
      }
    }

    refreshWeather()
    const interval = setInterval(refreshWeather, 15 * 60 * 1000)
    return () => clearInterval(interval)
  }, [])

  if (live.loading) {
    return (
      <div className="main-dashboard min-h-dvh bg-surface-base flex items-center justify-center">
        <p className="text-sm text-text-secondary">Loading dashboard…</p>
      </div>
    )
  }

  return (
    <div className="main-dashboard bg-surface-base">
      <AppRibbon position="top" wrap className="dashboard-ribbon">
        <h1 className="text-base font-bold text-text-default whitespace-nowrap shrink-0">
          Siberian Jungle
        </h1>
        {weatherData && (
          <div
            className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-secondary min-w-0 flex-1 font-mono tabular-nums"
            title={
              weatherData.timestamp
                ? `Quebec City weather · ${new Date(weatherData.timestamp).toLocaleString()}`
                : 'Quebec City weather'
            }
          >
            <span className="text-text-muted font-medium shrink-0">Quebec City</span>
            <span className="shrink-0">🌤 {Number(weatherData.temperature).toFixed(1)}°C</span>
            <span className="shrink-0">{Number(weatherData.humidity).toFixed(0)}%</span>
            <span className="shrink-0">{Number(weatherData.pressure).toFixed(0)} hPa</span>
            <span className="shrink-0">{Number(weatherData.wind_speed).toFixed(1)} km/h</span>
            {weatherData.wind_direction != null && (
              <span className="shrink-0" title="Wind direction (degrees)">
                {Number(weatherData.wind_direction).toFixed(0)}°
              </span>
            )}
            {weatherData.description && weatherData.description !== 'N/A' && (
              <span className="text-text-muted truncate shrink">{weatherData.description}</span>
            )}
          </div>
        )}
        <DashboardAlarmButton alarms={alarms.alarms} onOpen={() => setAlarmOpen(true)} />
        <RibbonMenuButton
          onClick={() => {
            const currentIndex = themes.indexOf(theme)
            const nextIndex = (currentIndex + 1) % themes.length
            setTheme(themes[nextIndex])
          }}
          aria-label="Toggle theme"
        >
          <Sun className="size-5" />
        </RibbonMenuButton>
      </AppRibbon>
      <div className="min-w-0">
        <DashboardAlarmSummary
          alarms={alarms.alarms}
          open={alarmOpen}
          onOpenChange={setAlarmOpen}
          serviceError={alarms.error}
          acknowledgingKey={alarms.acknowledgingKey}
          acknowledgementErrors={alarms.acknowledgementErrors}
          acknowledge={alarms.acknowledge}
        />
      </div>

      <div className="dashboard-layout__body">
        {degraded?.active && (
          <div
            role="alert"
            className="rounded border border-status-warning-vivid/60 bg-status-warning-bg/30 px-3 py-2 text-sm text-status-warning-text shrink-0"
          >
            <strong>Control loop degraded:</strong> {degraded.reason || 'recovering'} · failures{' '}
            {degraded.failure_count ?? 0} · recovery ticks {degraded.success_count ?? 0}/10
          </div>
        )}

        <div className="dashboard-workspace">
          {/* Center workspace: calendar/inspector row above rooms/event-log row */}
          <div className="dashboard-center">
            {/* Upper row: calendar + inspector */}
            <div className="dashboard-upper">
              <div className="dashboard-calendar-slot">
                <GrowCalendar
                  variant="compact"
                  fillWidth
                  viewMode="unified"
                  events={calendarEvents}
                  loading={calendarLoading}
                  onRefresh={refreshCalendar}
                  density="dashboard"
                  showInlineDayDetail={false}
                  onDaySelect={handleDaySelect}
                />
              </div>
              <div className="dashboard-inspector-slot">
                <DashboardCalendarInspector
                  events={calendarEvents}
                  now={now}
                  selectedDate={selectedDate}
                  selectedDateEvents={selectedDateEvents}
                  onRefresh={refreshCalendar}
                  onOpenGrowPlan={() => setWizardOpen(true)}
                />
              </div>
            </div>
            {/* Lower row: three full-width horizontal room bars ending at the SCADA column */}
            <div className="dashboard-room-rows">
              {roomPresentations.map(({ zone, ...presentation }) => (
                <DashboardZoneRow
                  key={`${zone.location}_${zone.cluster}`}
                  location={zone.location}
                  cluster={zone.cluster}
                  devices={live.devices}
                  sensorData={live.sensorData}
                  statusDevices={statusDevices}
                  lightDisplayNames={live.lightDisplayNames}
                  icon={ROOM_ICONS[zone.location] || '📦'}
                  {...presentation}
                  now={now}
                />
              ))}
            </div>
          </div>

          {/* Full-height event log column with the SCADA tank below */}
          <div className="dashboard-rail">
            <div className="dashboard-event-log-panel bg-surface-primary rounded-lg border border-border-subtle p-3">
              <EventLog
                entries={eventLogEntries}
                now={now}
                compact
                primaryRooms={ROOM_MAP_ZONES.map(zone => zone.location)}
              />
            </div>
            <div className="dashboard-water-panel">
              <DashboardOperationsRail
                sensorData={live.sensorData}
                waterLevelPercent={null}
                sections="water"
              />
            </div>
          </div>
        </div>
      </div>

      <MothernodeRibbon systemStats={systemStats} />

      <FlowerGrowWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        onCreated={refreshCalendar}
      />
    </div>
  )
}
