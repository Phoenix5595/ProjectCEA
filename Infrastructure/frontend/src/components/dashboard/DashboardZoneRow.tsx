import { memo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

import type { Device } from '../../types/device'
import { getFlowerDualClimateLayers, getLocationDisplayName } from '../../config/zones'
import type {
  RoomControlContext,
  RoomDecisionSummary,
  RoomSensorStatus,
  TrendData,
} from './dashboardStatus'
import { DashboardMiniTrend } from './DashboardMiniTrend'
import type { RoomTransition } from '../../utils/dashboardSchedule'
import { formatTransitionCountdown } from '../../utils/dashboardSchedule'
import {
  getClimateDisplay,
  getRoomLightState,
  getSensorDisplay,
  getSetpointColor,
  hasClimateData,
  renderTemperature,
} from './dashboardDisplay'

export interface DashboardZoneRowProps {
  location: string
  cluster: string
  devices: Device[]
  sensorData: Record<string, number>
  statusDevices?: Record<
    string,
    Record<string, { intensity?: number; load_percent?: number }>
  > | null
  icon: ReactNode
  lightDisplayNames?: Record<string, string>
  sensorStatus?: RoomSensorStatus
  decisionSummary?: RoomDecisionSummary
  controlContext?: RoomControlContext
  activeMode?: string | null
  nextTransition?: RoomTransition | null
  trendData?: TrendData
  now?: Date
}

const LIGHT_DISPLAY_NAMES: Record<string, Record<string, string>> = {
  'Veg Room': {
    light_1: 'Eyefinity Top',
    light_2: 'Ridgetop Bottom Right',
    light_3: 'Ridgetop Bottom Left',
  },
  'Flower Room': { light_1: 'Chilled Front', light_2: 'Apache', light_3: 'Chilled Back' },
  Lab: {},
}

function ClimateMini({
  label,
  location,
  cluster,
  sensorData,
}: {
  label: string
  location: string
  cluster: string
  sensorData: Record<string, number>
}) {
  const unplugged = !hasClimateData(location, cluster, sensorData)

  return (
    <div
      className="bg-surface-secondary rounded-sm p-1.5 min-w-[9.5rem] cursor-help"
      title={
        unplugged
          ? `No live climate data for ${label.toLowerCase()} (${cluster}).`
          : `${label} current sensor readings: temperature, relative humidity, CO₂ concentration, and calculated vapor pressure deficit (VPD).`
      }
    >
      <div className="text-10 text-text-muted mb-0.5 flex items-center gap-1">
        <span>{label}</span>
        {unplugged && (
          <span className="text-status-danger" title="Sensor offline or missing">
            ⚠
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 gap-x-2 gap-y-0.5 text-10">
        <div>
          <span className="text-text-subtle">T </span>
          <span className="text-text-default font-mono tabular-nums">
            {renderTemperature(location, cluster, sensorData)}
          </span>
        </div>
        <div>
          <span className="text-text-subtle">RH </span>
          <span className="text-text-default font-mono tabular-nums">
            {getClimateDisplay(sensorData, location, cluster, 'rh', '%')}
          </span>
        </div>
        <div>
          <span className="text-text-subtle">CO₂ </span>
          <span className="text-text-default font-mono tabular-nums">
            {getClimateDisplay(sensorData, location, cluster, 'co2', ' ppm', 0)}
          </span>
        </div>
        <div>
          <span className="text-text-subtle">VPD </span>
          <span className="text-text-default font-mono tabular-nums">
            {getClimateDisplay(sensorData, location, cluster, 'vpd', ' kPa')}
          </span>
        </div>
      </div>
    </div>
  )
}

function qualityLabel(status: RoomSensorStatus): string {
  if (status.quality === 'bad') return 'BAD VALUE'
  if (status.quality === 'stale') return 'STALE'
  if (status.quality === 'missing') return 'NO DATA'
  return 'LIVE'
}
function qualityTooltip(status: RoomSensorStatus): string {
  const cluster = status.cluster ?? 'room'
  const source =
    status.source === 'websocket'
      ? 'WebSocket stream'
      : status.source === 'poll'
        ? 'live sensor polling'
        : 'no known transport'
  const age =
    status.newestAgeMs == null
      ? ''
      : ` The newest valid sample is ${Math.floor(status.newestAgeMs / 1000)} seconds old.`

  if (status.quality === 'live')
    return `LIVE: recent valid sensor data for ${cluster} from ${source}.${age}`
  if (status.quality === 'stale')
    return `STALE: the newest valid sample for ${cluster} is older than 45 seconds. The last value is retained, not treated as current.${age}`
  if (status.quality === 'bad')
    return `BAD VALUE: ${cluster} returned sensor samples, but none were valid finite numbers.`
  return `NO DATA: no usable live samples are available for ${cluster}.`
}

function controlModeTooltip(mode: RoomControlContext['mode']): string {
  switch (mode) {
    case 'FAILSAFE':
      return 'A room failsafe is active and takes precedence over other control modes.'
    case 'MANUAL TIMED':
      return 'At least one relay has a timed manual command. Its expiry is shown below.'
    case 'MANUAL OFF':
      return 'At least one relay is held off by a manual override.'
    case 'SCHEDULED':
      return 'At least one relay currently follows a scheduled command.'
    case 'AUTO':
      return 'No manual, scheduled, or failsafe command is reported; automatic control is in effect.'
  }
}

function qualityClass(status: RoomSensorStatus): string {
  if (status.quality === 'bad')
    return 'border-status-danger-border text-status-danger-text bg-status-danger-bg'
  if (status.quality === 'stale')
    return 'border-status-warning-border text-status-warning-text bg-status-warning-bg'
  if (status.quality === 'missing')
    return 'border-border-subtle text-text-muted bg-surface-tertiary'
  return 'border-status-success-border text-status-success-text bg-status-success-bg'
}

function signed(value: number | null, precision: number, unit: string): string | null {
  if (value == null) return null
  return `${value >= 0 ? '+' : ''}${value.toFixed(precision)}${unit}`
}

function trendMetrics(
  location: string,
  trendData: TrendData
): Array<{ cluster: string; metric: keyof NonNullable<TrendData[string]> }> {
  if (location === 'Flower Room') {
    return [
      { cluster: 'front', metric: 'temperature' },
      { cluster: 'front', metric: 'vpd' },
      { cluster: 'back', metric: 'temperature' },
      { cluster: 'back', metric: 'vpd' },
    ]
  }
  if (location === 'Veg Room') {
    return [
      { cluster: 'main', metric: 'temperature' },
      { cluster: 'main', metric: 'rh' },
      { cluster: 'main', metric: 'vpd' },
      { cluster: 'main', metric: 'co2' },
    ]
  }
  const available = Object.keys(trendData.main ?? {}) as Array<keyof NonNullable<TrendData[string]>>
  return available.map(metric => ({ cluster: 'main', metric }))
}

export const DashboardZoneRow = memo(function DashboardZoneRow({
  location,
  cluster,
  devices,
  sensorData,
  statusDevices,
  lightDisplayNames,
  sensorStatus = { quality: 'missing', newestAgeMs: null, source: null, cluster: null },
  decisionSummary = {
    headline: 'TEMP IN BAND',
    headlineKind: 'in_band',
    layer: null,
    layers: [],
    correctingDevice: null,
  },
  controlContext = {
    mode: 'AUTO',
    manualExpiresAt: null,
    syncing: false,
    mismatch: false,
    mismatchDevices: [],
    interlockReasons: [],
    degraded: false,
  },
  activeMode = null,
  nextTransition = null,
  trendData = {},
  now = new Date(),
  icon,
}: DashboardZoneRowProps) {
  const roomDevices = devices.filter(d => d.location === location && d.cluster === cluster)
  const lightDevices = roomDevices.filter(d => d.device_name?.startsWith('light_'))
  const nonLightDevices = roomDevices.filter(d => !d.device_name?.startsWith('light_'))
  const lightState = getRoomLightState(location, devices)
  const displayName = getLocationDisplayName(location)
  const setpointPrefix = `${location}_${cluster}_`
  const isFlower = location === 'Flower Room'
  const selectedLayer = decisionSummary.layer
  const age =
    sensorStatus.newestAgeMs == null ? null : `${Math.floor(sensorStatus.newestAgeMs / 1000)}s`
  const transitionText = formatTransitionCountdown(nextTransition?.at ?? null, now)
  const trendKeys = trendMetrics(location, trendData)
  const deviceStatus = statusDevices as
    | Record<string, Record<string, Record<string, { intensity?: number; load_percent?: number }>>>
    | null
    | undefined

  return (
    <Link
      to={`/zone/${encodeURIComponent(location)}/${encodeURIComponent(cluster)}`}
      className="block w-full min-w-0 bg-surface-primary rounded-lg border border-border-subtle p-2 hover:border-border-emphasis transition-colors"
    >
      <div className="flex flex-row items-stretch gap-2 overflow-x-auto min-h-[8rem]">
        <div className="shrink-0 flex flex-col justify-center min-w-[8rem] pr-1 border-r border-border-subtle">
          <div className="text-xs text-text-muted uppercase font-bold tracking-wide flex items-center gap-1">
            <span>{icon}</span>
            <span className="truncate">{displayName}</span>
          </div>
          <div
            title={qualityTooltip(sensorStatus)}
            className={`mt-1 text-10 px-1 py-0.5 rounded-sm border w-fit cursor-help ${qualityClass(sensorStatus)}`}
          >
            <span>{qualityLabel(sensorStatus)}</span>
            <span className="ml-1 font-mono tabular-nums">
              {age ?? '—'} ·{' '}
              {sensorStatus.source === 'websocket'
                ? 'WS'
                : sensorStatus.source === 'poll'
                  ? 'POLL'
                  : '—'}
            </span>
          </div>
          <span
            className="mt-1 text-10 text-text-secondary cursor-help"
            title={
              activeMode
                ? `Current grow mode reported by automation: ${activeMode}.`
                : 'Current grow mode is unavailable.'
            }
          >
            Mode:{' '}
            <strong className="text-text-default">
              {activeMode ? activeMode.toUpperCase() : '—'}
            </strong>
          </span>
          <span
            className="mt-1 text-10 px-1 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 w-fit cursor-help"
            title={
              `${lightState === '☀️' ? 'Day: at least one room light is on.' : 'Night: no room lights are on.'} ` +
              (nextTransition
                ? `${nextTransition.label} is the next enabled room schedule transition ${transitionText}. Timing comes from automation schedules, not calendar tasks.`
                : 'No enabled room schedule transition is available in the next eight local calendar days.')
            }
          >
            {lightState}{' '}
            <span className="ml-1">
              {nextTransition
                ? `${nextTransition.label} ${transitionText}`
                : 'No scheduled transition'}
            </span>
          </span>
        </div>

        <div className="shrink-0 flex flex-col gap-1 min-w-[12rem]">
          <div
            title="Room decision summary. The headline prioritizes temperature outside its heating/cooling band, then an active correcting device, then the largest VPD delta. VPD and CO₂ deltas are informational, not alarms."
            className="rounded-sm bg-surface-secondary px-1.5 py-1 cursor-help"
          >
            <div className="text-10 uppercase tracking-wide text-text-muted">Decision</div>
            <div className="text-xs font-semibold text-text-default">
              {decisionSummary.headline}
            </div>
            {selectedLayer &&
              selectedLayer.temperatureState !== 'missing' &&
              selectedLayer.temperatureState !== 'in_band' && (
                <div
                  title={`${selectedLayer.temperatureState === 'low' ? 'Temperature is below the heating setpoint.' : 'Temperature is above the cooling setpoint.'} The signed delta is current temperature minus that band boundary; time is continuous out-of-band duration from trend history.`}
                  className="text-10 font-mono tabular-nums text-status-warning-text cursor-help"
                >
                  {signed(selectedLayer.temperatureDelta, 1, '°C')} ·{' '}
                  {selectedLayer.breachFullWindow ? '≥60m' : `${selectedLayer.breachMinutes ?? 0}m`}
                </div>
              )}
            {selectedLayer && selectedLayer.vpdDelta != null && (
              <div
                title="VPD delta is current VPD minus the VPD setpoint. Positive means above target; this is informational, not an alarm."
                className="text-10 font-mono tabular-nums text-text-secondary cursor-help"
              >
                VPD {signed(selectedLayer.vpdDelta, 2, ' kPa')}
              </div>
            )}
            {selectedLayer && selectedLayer.co2Delta != null && (
              <div
                title="CO₂ delta is current concentration minus the CO₂ setpoint. Positive means above target; this is informational, not an alarm."
                className="text-10 font-mono tabular-nums text-text-secondary cursor-help"
              >
                CO₂ {signed(selectedLayer.co2Delta, 0, ' ppm')}
              </div>
            )}
            {decisionSummary.correctingDevice && (
              <div
                title="An active non-light device whose type/name matches the current temperature correction direction."
                className="text-10 text-text-secondary cursor-help"
              >
                Correcting: {decisionSummary.correctingDevice}
              </div>
            )}
          </div>
          <div className="rounded-sm bg-surface-secondary px-1.5 py-1 text-10">
            <div
              className="font-semibold text-text-default cursor-help"
              title={controlModeTooltip(controlContext.mode)}
            >
              {controlContext.mode}
            </div>
            {controlContext.manualExpiresAt && (
              <div
                className="text-text-secondary cursor-help"
                title="Expiry time reported by the timed manual relay command."
              >
                Expires{' '}
                {new Date(controlContext.manualExpiresAt).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                })}
              </div>
            )}
            {controlContext.syncing && (
              <div
                className="text-status-warning-text cursor-help"
                title="Desired relay commands and observed physical states are being reconciled."
              >
                SYNCING
              </div>
            )}
            {controlContext.mismatch && (
              <div
                className="text-status-warning-text cursor-help"
                title="A relay's desired state differs from observed hardware state. Mismatch is suppressed while syncing."
              >
                MISMATCH · {controlContext.mismatchDevices.join(', ')}
              </div>
            )}
            {controlContext.interlockReasons.map(reason => (
              <div
                key={reason}
                className="text-status-danger-text cursor-help"
                title={`Backend interlock check says an ON request would be blocked: ${reason}`}
              >
                INTERLOCK · {reason}
              </div>
            ))}
            {controlContext.degraded && (
              <div
                className="text-status-warning-text cursor-help"
                title="The control service reports a degraded loop. See the Mothernode status ribbon for details."
              >
                CONTROL DEGRADED
              </div>
            )}
          </div>
        </div>

        <div className={`shrink-0 flex ${isFlower ? 'flex-col gap-1' : 'flex-row'}`}>
          {isFlower ? (
            getFlowerDualClimateLayers().map(layer =>
              layer.cluster ? (
                <ClimateMini
                  key={layer.label}
                  label={layer.label}
                  location={location}
                  cluster={layer.cluster}
                  sensorData={sensorData}
                />
              ) : (
                <ClimateMini
                  key={layer.label}
                  label={layer.label}
                  location={location}
                  cluster="back"
                  sensorData={{}}
                />
              )
            )
          ) : (
            <ClimateMini
              label="Climate"
              location={location}
              cluster={cluster}
              sensorData={sensorData}
            />
          )}
        </div>

        <div className="shrink-0 bg-surface-secondary rounded-sm p-1.5 min-w-[10rem]">
          <div
            className="text-10 text-text-muted mb-0.5 cursor-help"
            title="Room setpoints: heating and cooling define the temperature band; CO₂ and VPD are control targets."
          >
            Setpoints
          </div>
          <div className="flex flex-wrap gap-x-2 gap-y-0.5 text-10 font-mono tabular-nums">
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
            <div
              className="text-10 text-text-muted mb-0.5 cursor-help"
              title="Room light outputs. Percent is the reported dimmer intensity; sun/moon indicates the reported on/off state."
            >
              Lights
            </div>
            <div className="flex flex-col gap-0.5">
              {lightDevices.map(device => {
                const deviceName = device.device_name || ''
                const name =
                  lightDisplayNames?.[`${location}_${cluster}_${deviceName}`] ??
                  LIGHT_DISPLAY_NAMES[location]?.[deviceName] ??
                  deviceName
                const intensityKey = `${location}_${cluster}_${deviceName}_intensity`
                const intensity =
                  sensorData[intensityKey] ??
                  deviceStatus?.[location]?.[cluster]?.[deviceName]?.intensity
                return (
                  <div key={deviceName} className="flex items-center justify-between gap-1 text-10">
                    <span className="text-text-secondary truncate flex-1 min-w-0" title={name}>
                      {name}
                    </span>
                    <span
                      className="text-accent-data font-mono tabular-nums shrink-0 cursor-help"
                      title="Reported dimmer intensity, in percent."
                    >
                      {intensity != null ? `${Number(intensity).toFixed(0)}%` : '--'}
                    </span>
                    <span
                      className="shrink-0 cursor-help"
                      title={
                        device.state === 1
                          ? 'Reported light state: ON.'
                          : 'Reported light state: OFF.'
                      }
                    >
                      {device.state === 1 ? '☀️' : '🌙'}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {nonLightDevices.length > 0 && (
          <div className="shrink-0 bg-surface-secondary rounded-sm p-1.5 min-w-[7rem] max-w-[10rem]">
            <div
              className="text-10 text-text-muted mb-0.5 cursor-help"
              title="Non-light room outputs. ON/OFF is the reported device state; load percentage appears when available."
            >
              Devices
            </div>
            <div className="flex flex-col gap-0.5">
              {nonLightDevices.slice(0, 4).map(device => {
                const loadPct =
                  deviceStatus?.[location]?.[cluster]?.[device.device_name ?? '']?.load_percent
                return (
                  <div key={device.device_name} className="flex justify-between gap-1 text-10">
                    <span className="text-text-secondary truncate">{device.device_name}</span>
                    <span
                      className={`shrink-0 px-1 rounded text-8 cursor-help ${device.state === 1 ? 'bg-status-success-bg text-status-success-text' : 'bg-surface-tertiary text-text-muted'}`}
                      title={`Reported device state: ${device.state === 1 ? 'ON' : 'OFF'}${loadPct != null ? `; load ${Number(loadPct).toFixed(0)} percent` : ''}.`}
                    >
                      {device.state === 1 ? 'ON' : 'OFF'}
                      {loadPct != null ? ` ${Number(loadPct).toFixed(0)}%` : ''}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        <div className="shrink-0 flex gap-1">
          {trendKeys.map(({ cluster: trendCluster, metric }) => (
            <DashboardMiniTrend
              key={`${trendCluster}-${metric}`}
              metric={trendData[trendCluster]?.[metric]}
              compact
            />
          ))}
        </div>
      </div>
    </Link>
  )
})
