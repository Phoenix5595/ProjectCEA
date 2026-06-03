/** Main dashboard page component. */
import { useEffect, useState, useMemo } from 'react';

import GrowCalendar from '../components/calendar/GrowCalendar';
import FlowerGrowWizard from '../components/calendar/FlowerGrowWizard';
import { useCalendarEvents } from '../hooks/useCalendarEvents';
import { apiClient } from '../services/api';
import { useTheme } from '../contexts/ThemeContext';
import { useWebSocket } from '../hooks/useWebSocket';
import { useSensorPolling } from '../hooks/useSensorPolling';
import { useSystemStatus } from '../hooks/useSystemStatus';
import { AppRibbon } from '../components/chrome/AppRibbon';
import { RibbonMenuButton } from '../components/chrome/ribbonMenuButton';
import { DashboardZoneRow } from '../components/dashboard/DashboardZoneRow';
import { MothernodeRibbon } from '../components/dashboard/MothernodeRibbon';
import { DASHBOARD_ROW_ZONES } from '../config/zones';

interface WeatherData {
  temperature: number;
  humidity: number;
  pressure: number;
  wind_speed: number;
  wind_direction: number | null;
  description: string;
  location: string;
  timestamp: string;
}

/** Room configuration with icons */
const ROOM_ICONS: Record<string, string> = {
  'Veg Room': '🌱',
  'Flower Room': '🌻',
  'Lab': '🧪'
};

export default function Dashboard() {
  const { theme, setTheme, themes } = useTheme();

  const { devices: wsDevices, sensorData: wsSensorData } = useWebSocket();
  const { devices, sensorData, controlHistoryByRoom, loading } = useSensorPolling();
  const { systemStats, statusDevices, degraded } = useSystemStatus();

  const [weatherData, setWeatherData] = useState<WeatherData | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const { events: calendarEvents, loading: calendarLoading, refresh: refreshCalendar } =
    useCalendarEvents();

  // Merge WebSocket data with polled data (WS takes precedence if it has data)
  const mergedDevices = useMemo(
    () => (wsDevices.length > 0 ? wsDevices : devices),
    [wsDevices, devices]
  );
  const mergedSensorData = useMemo(
    () => ({ ...sensorData, ...wsSensorData }),
    [sensorData, wsSensorData]
  );

  // Weather refresh (15 minutes)
  useEffect(() => {
    const refreshWeather = async () => {
      try {
        const weatherResponse = await apiClient.getLatestWeather();
        if (weatherResponse?.data) {
          const d = weatherResponse.data;
          const temp = d.temp?.value ?? d.temperature?.value;
          const rh = d.rh?.value ?? d.humidity?.value;
          if (temp != null && rh != null) {
            setWeatherData({
              temperature: Number(temp),
              humidity: Number(rh),
              pressure: Number(d.pressure?.value ?? 0),
              wind_speed: Number(d.wind_speed?.value ?? 0),
              wind_direction: d.wind_direction?.value != null ? Number(d.wind_direction.value) : null,
              description: d.description?.value ?? 'N/A',
              location: 'Quebec City',
              timestamp: weatherResponse.timestamp ?? ''
            });
          }
        }
      } catch {
        // Silently fail weather updates
      }
    };

    refreshWeather();
    const interval = setInterval(refreshWeather, 15 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="main-dashboard h-screen bg-surface-base flex items-center justify-center">
        <p className="text-sm text-text-secondary">Loading dashboard…</p>
      </div>
    );
  }

  return (
    <div className="main-dashboard flex flex-col h-screen min-h-0 bg-surface-base">
      <AppRibbon position="top" sticky>
        <h1 className="text-base font-bold text-text-default whitespace-nowrap shrink-0">
          Siberian Jungle
        </h1>
        {weatherData && (
          <div
            className="flex items-center gap-3 text-xs text-text-secondary min-w-0 flex-1 overflow-x-auto font-mono tabular-nums"
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
        <RibbonMenuButton
          onClick={() => {
            const currentIndex = themes.indexOf(theme);
            const nextIndex = (currentIndex + 1) % themes.length;
            setTheme(themes[nextIndex]);
          }}
          aria-label="Toggle theme"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z"
            />
          </svg>
        </RibbonMenuButton>
      </AppRibbon>

      <div className="flex-1 flex flex-col min-h-0 overflow-hidden p-2 gap-2">
        {degraded?.active && (
          <div
            role="alert"
            className="rounded border border-amber-500/60 bg-amber-500/15 px-3 py-2 text-sm text-amber-100"
          >
            <strong>Control loop degraded:</strong> {degraded.reason || 'recovering'} · failures{' '}
            {degraded.failure_count ?? 0} · recovery ticks {degraded.success_count ?? 0}/10
          </div>
        )}

        <div className="flex-1 flex flex-col lg:flex-row gap-2 min-h-0 overflow-hidden">
          <div className="w-full lg:w-1/2 lg:max-w-[50%] shrink-0 min-h-0 flex flex-col overflow-auto">
            <GrowCalendar
              variant="compact"
              fillWidth
              viewMode="unified"
              events={calendarEvents}
              loading={calendarLoading}
              onRefresh={refreshCalendar}
              showAddTask
              onAddTask={() => setWizardOpen(true)}
            />
          </div>
          <div className="flex-1 flex flex-col gap-2 min-h-0 overflow-y-auto min-w-0">
            {DASHBOARD_ROW_ZONES.map((zone) => (
              <DashboardZoneRow
                key={`${zone.location}_${zone.cluster}`}
                location={zone.location}
                cluster={zone.cluster}
                devices={mergedDevices}
                sensorData={mergedSensorData}
                statusDevices={statusDevices}
                controlHistory={controlHistoryByRoom[`${zone.location}_${zone.cluster}`] || []}
                icon={ROOM_ICONS[zone.location] || '📦'}
              />
            ))}
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
  );
}
