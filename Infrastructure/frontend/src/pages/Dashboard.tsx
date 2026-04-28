/** Main dashboard page component. */
import { useEffect, useState, useMemo } from 'react';

import { apiClient } from '../services/api';
import { useTheme } from '../contexts/ThemeContext';
import { useWebSocket } from '../hooks/useWebSocket';
import { useSensorPolling } from '../hooks/useSensorPolling';
import { useSystemStatus } from '../hooks/useSystemStatus';
import { DashboardRoomCard } from '../components/DashboardRoomCard';
import { SystemStatusPanel } from '../components/SystemStatusPanel';
import { ZONES } from '../config/zones';

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
      <div className="main-dashboard min-h-screen bg-surface-base p-4">
        <div className="max-w-full mx-auto">
          <h1 className="text-3xl font-bold mb-8 text-text-default">Siberian Jungle</h1>
          <p className="text-text-secondary">Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="main-dashboard min-h-screen bg-surface-base p-2">
      <div className="max-w-full mx-auto h-[calc(100vh-1rem)] flex flex-col">
        {/* Header */}
        <div className="sticky top-0 z-10 bg-surface-base p-1 mb-2">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-text-default flex items-center gap-2">
                <img src="/favicon.ico" alt="" className="w-6 h-6 rounded-sm" aria-hidden />
                Siberian Jungle
              </h1>
            </div>
            <div className="flex items-center gap-4">
              {weatherData && (
                <div
                  className="flex items-center gap-3 text-sm text-text-secondary"
                  title={weatherData.timestamp ? `Quebec City weather · ${new Date(weatherData.timestamp).toLocaleString()}` : 'Quebec City weather'}
                >
                  <span className="text-text-muted font-medium">Quebec City</span>
                  <span className="flex items-center gap-1">
                    <span>🌤</span> {Number(weatherData.temperature).toFixed(2)}°C
                  </span>
                  <span>{Number(weatherData.humidity).toFixed(2)}%</span>
                  <span>{Number(weatherData.pressure).toFixed(2)} hPa</span>
                  <span>{Number(weatherData.wind_speed).toFixed(2)} km/h</span>
                  {weatherData.wind_direction != null && (
                    <span title="Wind direction (degrees)">{Number(weatherData.wind_direction).toFixed(2)}°</span>
                  )}
                  {weatherData.description && weatherData.description !== 'N/A' && (
                    <span className="text-text-muted">{weatherData.description}</span>
                  )}
                </div>
              )}
              <button
                onClick={() => {
                  const currentIndex = themes.indexOf(theme);
                  const nextIndex = (currentIndex + 1) % themes.length;
                  setTheme(themes[nextIndex]);
                }}
                className="p-2 rounded-lg bg-surface-secondary text-text-secondary hover:bg-surface-tertiary transition-colors"
                aria-label="Toggle theme"
              >
                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
                </svg>
              </button>
            </div>
          </div>
        </div>
        {degraded?.active && (
          <div
            role="alert"
            className="mb-2 rounded-lg border border-amber-500/60 bg-amber-500/15 px-3 py-2 text-sm text-amber-100"
          >
            <strong>Control loop degraded:</strong> {degraded.reason || 'recovering'} · failures{' '}
            {degraded.failure_count ?? 0} · recovery ticks {degraded.success_count ?? 0}/10
          </div>
        )}

        {/* Main Content */}
        <div className="flex-1 flex flex-col lg:flex-row gap-2 min-h-0">
          {/* Room Cards - Left Side */}
          <div className="flex-1 flex flex-col lg:flex-row gap-2 min-h-0">
            {ZONES.map((zone, index) => {
              const isFlowerRoom = zone.location === 'Flower Room';
              const cardWidth = isFlowerRoom ? 'lg:w-[37%]' : index === 2 ? 'flex-1' : 'lg:w-[37%]';
              
              return (
                <DashboardRoomCard
                  key={`${zone.location}_${zone.cluster}`}
                  location={zone.location}
                  cluster={zone.cluster}
                  devices={mergedDevices}
                  sensorData={mergedSensorData}
                  statusDevices={statusDevices}
                  controlHistory={controlHistoryByRoom[`${zone.location}_${zone.cluster}`] || []}
                  icon={ROOM_ICONS[zone.location] || '📦'}
                  cardWidth={cardWidth}
                />
              );
            })}
          </div>

          {/* System Status Panel - Right Side */}
          <SystemStatusPanel systemStats={systemStats} />
        </div>
      </div>
    </div>
  );
}
