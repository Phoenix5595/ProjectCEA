import { memo } from 'react';
import { Link } from 'react-router-dom';

import type { Device } from '../../types/device';
import type { ControlHistoryEntry } from '../../types/device';
import { getFlowerDualClimateLayers, getLocationDisplayName } from '../../config/zones';
import {
  formatControlHistoryLine,
  getRoomLightState,
  getSensorDisplay,
  getSetpointColor,
  hasClimateData,
  renderTemperature,
} from './dashboardDisplay';

export interface DashboardZoneRowProps {
  location: string;
  cluster: string;
  devices: Device[];
  sensorData: Record<string, number>;
  statusDevices?: Record<string, Record<string, { intensity?: number; load_percent?: number }>> | null;
  controlHistory: ControlHistoryEntry[];
  icon: string;
}

const LIGHT_DISPLAY_NAMES: Record<string, Record<string, string>> = {
  'Veg Room': { light_1: 'Eyefinity Top', light_2: 'Ridgetop Bottom Right', light_3: 'Ridgetop Bottom Left' },
  'Flower Room': { light_1: 'Chilled Front', light_2: 'Apache', light_3: 'Chilled Back' },
  Lab: {},
};

function ClimateMini({
  label,
  location,
  cluster,
  sensorData,
}: {
  label: string;
  location: string;
  cluster: string;
  sensorData: Record<string, number>;
}) {
  const unplugged = !hasClimateData(location, cluster, sensorData);
  const prefix = `${location}_${cluster}_`;

  return (
    <div
      className="bg-surface-secondary rounded-sm p-1.5 min-w-[9.5rem]"
      title={unplugged ? 'No live sensor data for this cluster' : undefined}
    >
      <div className="text-[10px] text-text-muted mb-0.5 flex items-center gap-1">
        <span>{label}</span>
        {unplugged && (
          <span className="text-status-danger" title="Sensor offline or missing">
            ⚠
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-[10px]">
        <div>
          <span className="text-text-subtle">T </span>
          <span className="text-text-default font-mono tabular-nums">{renderTemperature(location, cluster, sensorData)}</span>
        </div>
        <div>
          <span className="text-text-subtle">RH </span>
          <span className="text-text-default font-mono tabular-nums">
            {getSensorDisplay(sensorData, `${prefix}relative_humidity`, '%')}
          </span>
        </div>
        <div>
          <span className="text-text-subtle">CO₂ </span>
          <span className="text-text-default font-mono tabular-nums">
            {getSensorDisplay(sensorData, `${prefix}co2`, ' ppm', 0)}
          </span>
        </div>
        <div>
          <span className="text-text-subtle">VPD </span>
          <span className="text-text-default font-mono tabular-nums">
            {getSensorDisplay(sensorData, `${prefix}vpd`, ' kPa')}
          </span>
        </div>
      </div>
    </div>
  );
}

export const DashboardZoneRow = memo(function DashboardZoneRow({
  location,
  cluster,
  devices,
  sensorData,
  statusDevices,
  controlHistory,
  icon,
}: DashboardZoneRowProps) {
  const roomDevices = devices.filter((d) => d.location === location && d.cluster === cluster);
  const lightDevices = roomDevices.filter((d) => d.device_name?.startsWith('light_'));
  const nonLightDevices = roomDevices.filter((d) => !d.device_name?.startsWith('light_'));
  const lightState = getRoomLightState(location, devices);
  const displayName = getLocationDisplayName(location);
  const displayNames = LIGHT_DISPLAY_NAMES[location] || {};
  const setpointPrefix = `${location}_${cluster}_`;
  const isFlower = location === 'Flower Room';

  return (
    <Link
      to={`/zone/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}`}
      className="block w-full bg-surface-primary rounded-lg border border-border-subtle p-2 hover:border-border-emphasis transition-colors"
    >
      <div className="flex flex-row items-stretch gap-2 overflow-x-auto min-h-[4.5rem]">
        <div className="shrink-0 flex flex-col justify-center min-w-[6.5rem] pr-1 border-r border-border-subtle">
          <div className="text-xs text-text-muted uppercase font-bold tracking-wide flex items-center gap-1">
            <span>{icon}</span>
            <span className="truncate">{displayName}</span>
          </div>
          <span
            className="mt-1 text-[10px] px-1 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 w-fit"
            title={lightState === '☀️' ? 'Day' : 'Night'}
          >
            {lightState}
          </span>
        </div>

        <div className={`shrink-0 flex ${isFlower ? 'flex-col gap-1' : 'flex-row'}`}>
          {isFlower ? (
            getFlowerDualClimateLayers().map((layer) =>
              layer.cluster ? (
                <ClimateMini
                  key={layer.label}
                  label={layer.label}
                  location={location}
                  cluster={layer.cluster}
                  sensorData={sensorData}
                />
              ) : (
                <ClimateMini key={layer.label} label={layer.label} location={location} cluster="back" sensorData={{}} />
              )
            )
          ) : (
            <ClimateMini label="Climate" location={location} cluster={cluster} sensorData={sensorData} />
          )}
        </div>

        <div className="shrink-0 bg-surface-secondary rounded-sm p-1.5 min-w-[10rem]">
          <div className="text-[10px] text-text-muted mb-0.5">Setpoints</div>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] font-mono tabular-nums">
            <span className={getSetpointColor()}>
              H {getSensorDisplay(sensorData, `${setpointPrefix}heating_setpoint`, '°')}
            </span>
            <span className={getSetpointColor()}>
              C {getSensorDisplay(sensorData, `${setpointPrefix}cooling_setpoint`, '°')}
            </span>
            <span className={getSetpointColor()}>
              CO₂ {getSensorDisplay(sensorData, `${setpointPrefix}co2_setpoint`, '', 0)}
            </span>
            <span className={getSetpointColor()}>
              VPD {getSensorDisplay(sensorData, `${setpointPrefix}vpd_setpoint`, '')}
            </span>
          </div>
        </div>

        {lightDevices.length > 0 && (
          <div className="shrink-0 bg-surface-secondary rounded-sm p-1.5 min-w-[8rem] max-w-[14rem]">
            <div className="text-[10px] text-text-muted mb-0.5">Lights</div>
            <div className="flex flex-col gap-0.5">
              {lightDevices.map((device) => {
                const deviceName = device.device_name || '';
                const name = displayNames[deviceName] || deviceName;
                const intensityKey = `${location}_${cluster}_${deviceName}_intensity`;
                const intensity =
                  sensorData[intensityKey] ??
                  (statusDevices as Record<string, Record<string, Record<string, { intensity?: number }>>>)?.[
                    location
                  ]?.[cluster]?.[deviceName]?.intensity;
                return (
                  <div key={deviceName} className="flex items-center justify-between gap-1 text-[10px]">
                    <span className="text-text-secondary truncate max-w-[5rem]" title={name}>
                      {name.split(' ')[0]}
                    </span>
                    <span className="text-accent-data font-mono tabular-nums shrink-0">
                      {intensity != null ? `${Number(intensity).toFixed(0)}%` : '--'}
                    </span>
                    <span className="shrink-0">{device.state === 1 ? '☀️' : '🌙'}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {nonLightDevices.length > 0 && (
          <div className="shrink-0 bg-surface-secondary rounded-sm p-1.5 min-w-[7rem] max-w-[10rem]">
            <div className="text-[10px] text-text-muted mb-0.5">Devices</div>
            <div className="flex flex-col gap-0.5">
              {nonLightDevices.slice(0, 4).map((device) => {
                const loadPct = (statusDevices as Record<string, Record<string, Record<string, { load_percent?: number }>>>)?.[
                  location
                ]?.[cluster]?.[device.device_name ?? '']?.load_percent;
                return (
                  <div key={device.device_name} className="flex justify-between gap-1 text-[10px]">
                    <span className="text-text-secondary truncate">{device.device_name}</span>
                    <span
                      className={`shrink-0 px-1 rounded text-[8px] ${
                        device.state === 1
                          ? 'bg-status-success-bg text-status-success-text'
                          : 'bg-surface-tertiary text-text-muted'
                      }`}
                    >
                      {device.state === 1 ? 'ON' : 'OFF'}
                      {loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="flex-1 min-w-[10rem] bg-surface-secondary rounded-sm p-1.5">
          <div className="text-[10px] text-text-muted mb-0.5">Recent on/off</div>
          {controlHistory.length ? (
            <div className="space-y-0.5 text-[9px] text-text-secondary font-mono tabular-nums max-h-[4rem] overflow-y-auto">
              {controlHistory.slice(0, 6).map((entry, i) => (
                <div key={i} className="truncate" title={entry.reason ?? undefined}>
                  {formatControlHistoryLine(entry)}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-[9px] text-text-subtle">No recent changes</div>
          )}
        </div>
      </div>
    </Link>
  );
});
