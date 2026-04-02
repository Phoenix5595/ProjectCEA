/** Dashboard room card component for single room display. */
import { memo } from 'react';
import { Link } from 'react-router-dom';
import type { Device } from '../types/device';
import type { ControlHistoryEntry } from '../types/device';
import { getLocationDisplayName } from '../config/zones';

export interface DashboardRoomCardProps {
  location: string;
  cluster: string;
  devices: Device[];
  sensorData: Record<string, number>;
  statusDevices?: Record<string, Record<string, { intensity?: number; load_percent?: number }>> | null;
  controlHistory: ControlHistoryEntry[];
  icon: string;
  cardWidth?: string;
}

const MAX_REASON_LENGTH = 40;

function formatControlHistoryLine(entry: ControlHistoryEntry): string {
  const timeStr = (() => {
    try {
      const d = new Date(entry.timestamp);
      return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    } catch {
      return '--:--';
    }
  })();
  const onOff = entry.new_state === 1 ? 'ON' : 'OFF';
  const reason = entry.reason?.trim() || '';
  const load = entry.load_percent != null ? Number(entry.load_percent) : null;
  let suffix = '';
  if (entry.new_state === 1) {
    if (load != null && reason) suffix = ` (Load ${Math.round(load)}%, ${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`;
    else if (load != null) suffix = ` (Load ${Math.round(load)}%)`;
    else if (reason) suffix = ` (${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`;
  } else {
    if (reason) suffix = ` (${reason.length > MAX_REASON_LENGTH ? reason.slice(0, MAX_REASON_LENGTH) + '…' : reason})`;
  }
  return `${timeStr} ${entry.device_name} ${onOff}${suffix}`;
}

function getRoomLightState(location: string, devices: Device[]): string {
  const roomLights = devices.filter(d => d.location === location && d.cluster === 'main' && d.device_name?.startsWith('light_'));
  const hasLightsOn = roomLights.some(light => light.state === 1);
  return hasLightsOn ? '☀️' : '🌙';
}

function getSetpointColor(): string {
  return 'text-accent-setpoint';
}

function getSensorDisplay(sensorData: Record<string, number>, key: string, unit: string, precision = 2): string {
  if (sensorData[key] == null) return `--${unit}`;
  return `${Number(sensorData[key]).toFixed(precision)}${unit}`;
}

/** Get sensor key with fallback for different naming conventions */
function getSensorKey(sensorData: Record<string, number>, location: string, cluster: string, ...candidates: string[]): { key: string; value: number | undefined } {
  for (const candidate of candidates) {
    const key = `${location}_${cluster}_${candidate}`;
    if (key in sensorData) {
      return { key, value: sensorData[key] };
    }
  }
  return { key: candidates[0], value: undefined };
}

/** Render temperature with F->C conversion if needed */
function renderTemperature(location: string, cluster: string, sensorData: Record<string, number>): string {
  const candidates = ['dry_bulb_f', 'temperature_sensor', 'lab_temp'];
  const { value } = getSensorKey(sensorData, location, cluster, ...candidates);
  if (value == null) return '--°C';
  // If value seems to be in Fahrenheit ( > 100), convert
  if (value > 100) {
    return `${((value - 32) * 5/9).toFixed(2)}°C`;
  }
  return `${Number(value).toFixed(2)}°C`;
}

export const DashboardRoomCard = memo(function DashboardRoomCard({
  location,
  cluster,
  devices,
  sensorData,
  statusDevices,
  controlHistory,
  icon,
  cardWidth = 'lg:w-[37%]'
}: DashboardRoomCardProps) {
  const roomDevices = devices.filter(d => d.location === location && d.cluster === cluster);
  const lightDevices = roomDevices.filter(d => d.device_name?.startsWith('light_'));
  const nonLightDevices = roomDevices.filter(d => !d.device_name?.startsWith('light_'));
  const lightState = getRoomLightState(location, devices);
  
  // Get display name from config
  const displayName = getLocationDisplayName(location);

  // Light display names mapping
  const lightDisplayNames: Record<string, Record<string, string>> = {
    'Veg Room': { 'light_1': 'Eyefinity Top', 'light_2': 'Ridgetop Bottom Right', 'light_3': 'Ridgetop Bottom Left' },
    'Flower Room': { 'light_1': 'Chilled Front', 'light_2': 'Apache', 'light_3': 'Chilled Back' },
    'Lab': {}
  };
  const displayNames = lightDisplayNames[location] || {};

  return (
    <div className={`bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col ${cardWidth}`}>
      <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
        <span className="flex items-center gap-2">
          <span>{icon}</span> {displayName}
        </span>
        <div className="flex items-center gap-1">
          <span
            className="text-[10px] px-1.5 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 cursor-help"
            title={`Status: ${lightState === '☀️' ? 'Day' : 'Night'}`}
          >
            {lightState}
          </span>
        </div>
      </div>
      
      <div className="flex-1 overflow-y-auto">
        <Link to={`/zone/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}`} className="block h-full">
          <div className="space-y-2">
            {/* Current Conditions */}
            <div className="bg-surface-secondary rounded-sm p-2">
              <div className="text-xs text-text-muted mb-1">Current Conditions</div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-text-subtle">Temperature</div>
                  <div className="text-text-default font-mono tabular-nums">
                    {renderTemperature(location, cluster, sensorData)}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">Humidity</div>
                  <div className="text-text-default font-mono tabular-nums">
                    {getSensorDisplay(sensorData, `${location}_${cluster}_relative_humidity`, '%')}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">CO2</div>
                  <div className="text-text-default font-mono tabular-nums">
                    {getSensorDisplay(sensorData, `${location}_${cluster}_co2`, ' ppm')}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">VPD</div>
                  <div className="text-text-default font-mono tabular-nums">
                    {getSensorDisplay(sensorData, `${location}_${cluster}_vpd`, ' kPa')}
                  </div>
                </div>
              </div>
            </div>

            {/* Effective Setpoints */}
            <div className="bg-surface-secondary rounded-sm p-2">
              <div className="text-xs text-text-muted mb-1" title="From Redis (effective_setpoint:*): heating, cooling, CO2, VPD">
                Effective Setpoints
              </div>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <div>
                  <div className="text-text-subtle">Heating</div>
                  <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
                    {getSensorDisplay(sensorData, `${location}_${cluster}_heating_setpoint`, '°C')}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">Cooling</div>
                  <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
                    {getSensorDisplay(sensorData, `${location}_${cluster}_cooling_setpoint`, '°C')}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">CO2</div>
                  <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
                    {getSensorDisplay(sensorData, `${location}_${cluster}_co2_setpoint`, ' ppm')}
                  </div>
                </div>
                <div>
                  <div className="text-text-subtle">VPD</div>
                  <div className={`font-mono tabular-nums ${getSetpointColor()}`}>
                    {getSensorDisplay(sensorData, `${location}_${cluster}_vpd_setpoint`, ' kPa')}
                  </div>
                </div>
              </div>
            </div>

            {/* Light Status - only show if room has lights */}
            {lightDevices.length > 0 && (
              <div className="bg-surface-secondary rounded-sm p-2">
                <div className="text-xs text-text-muted mb-1">Light Status</div>
                <div className="space-y-1">
                  {lightDevices.map((device, index) => {
                    const deviceName = device.device_name || '';
                    const displayName = displayNames[deviceName] || deviceName;
                    const nameParts = displayName.split(' ');
                    const firstName = nameParts[0];
                    const restName = nameParts.slice(1).join(' ');
                    const intensityKey = `${location}_${cluster}_${deviceName}_intensity`;
                    const intensity = sensorData[intensityKey] ?? (statusDevices as any)?.[location]?.[cluster]?.[deviceName]?.intensity;
                    
                    return (
                      <div key={index} className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary flex-1 min-w-0 leading-tight">
                          <span className="block">{firstName}</span>
                          {restName && <span className="block text-[10px] text-text-muted">{restName}</span>}
                        </span>
                        <div className="flex items-center gap-1 shrink-0">
                          <span className="text-accent-data font-mono tabular-nums">
                            {intensity != null ? `${Number(intensity).toFixed(2)}%` : '--%'}
                          </span>
                          <span
                            className={`text-[14px] px-1.5 py-0.5 rounded cursor-help ${
                              device.state === 1
                                ? 'bg-btn-primary-dim/50 text-btn-primary-data border border-btn-primary-active/50'
                                : 'bg-surface-tertiary text-text-subtle border border-border-emphasis'
                            }`}
                            title={`${displayName}: ${device.state === 1 ? 'Sun' : 'Moon'}`}
                          >
                            {device.state === 1 ? '☀️' : '🌙'}
                          </span>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Device Status */}
            {nonLightDevices.length > 0 && (
              <div className="bg-surface-secondary rounded-sm p-2">
                <div className="text-xs text-text-muted mb-2">Device Status</div>
                <div className="space-y-1">
                  {nonLightDevices.map((device, index) => {
                    const loadPct = (statusDevices as any)?.[location]?.[cluster]?.[device.device_name]?.load_percent;
                    return (
                      <div key={index} className="flex items-center justify-between text-xs">
                        <span className="text-text-secondary truncate flex-1">{device.device_name}</span>
                        <span className={`px-1.5 py-0.5 rounded text-[8px] font-medium ${
                          device.state === 1 ? 'bg-status-success-bg text-status-success-text' : 'bg-surface-tertiary text-text-muted'
                        }`}>
                          {device.state === 1 ? 'ON' : 'OFF'}
                          {loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Recent Control History */}
            <div className="bg-surface-secondary rounded-sm p-2">
              <div className="text-xs text-text-muted mb-1">Recent on/off</div>
              {controlHistory.length ? (
                <div className="space-y-0.5 text-[10px] text-text-secondary font-mono tabular-nums">
                  {controlHistory.slice(0, 10).map((entry, i) => (
                    <div key={i} title={entry.reason ?? undefined}>
                      {formatControlHistoryLine(entry)}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-[10px] text-text-subtle">No recent changes</div>
              )}
            </div>
          </div>
        </Link>
      </div>
    </div>
  );
});
