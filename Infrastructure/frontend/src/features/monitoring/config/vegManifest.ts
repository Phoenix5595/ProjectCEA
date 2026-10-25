/**
 * Veg Room monitoring manifest.
 *
 * This is the Veg Room's native monitoring panel contract. Panel order,
 * names, units, bounds, colors, and line styles follow the established
 * dashboard layout.
 *
 * The parity test maps every canonical panel/series/row onto this manifest and
 * rejects unknown additions.
 */
import type { MonitoringManifest } from './manifestTypes'

export const vegManifest: MonitoringManifest = {
  room: 'Veg Room',
  sensorClusters: ['main'],
  panels: [
    {
      kind: 'table',
      id: 'veg-values',
      title: 'Sensor Values',
      columns: ['Data'],
      rows: [
        'Dry Bulb',
        'Wet Bulb',
        'RH',
        'VPD',
        'CO2',
        'Pressure',
        'Secondary Temp',
        'Secondary RH',
        'Water Level',
        'Last Update',
      ],
      units: ['°C', '°C', '%', '%', ' kPa', ' ppm', ' hPa', ' mm'],
      hasLastUpdate: true,
    },
    {
      kind: 'timeseries',
      id: 'veg-climate',
      title: 'Veg climate conditions',
      // Environmental sensors plus effective climate setpoints: Monitoring
      // adds the projected targets, Historical shows the recorded ones.
      sources: ['sensor', 'climate'],
      families: ['temperature', 'rh', 'vpd'],
      defaults: { unit: 'celsius', softMin: 15 },
      series: [
        {
          name: 'dry_bulb_v',
          displayName: 'Dry Bulb (°C)',
          unit: 'celsius',
          axisPlacement: 'auto',
          color: '#e02b2b',
        },
        {
          name: 'wet_bulb_v',
          displayName: 'Wet Bulb (°C)',
          unit: 'celsius',
          axisPlacement: 'auto',
          color: '#4169e1',
        },
        {
          name: 'rh_v',
          displayName: 'RH (%)',
          unit: 'percent',
          axisPlacement: 'right',
          softMax: 100,
          decimals: 1,
          color: '#fade2a',
        },
        {
          name: 'vpd_v',
          displayName: 'VPD (kPa)',
          unit: 'kpa',
          axisPlacement: 'auto',
          decimals: 2,
          color: '#2f7fe0',
          lineWidth: 3,
        },
        {
          name: 'DAY Period Overlay',
          displayName: 'DAY Period Overlay',
          unit: 'percent',
          axisPlacement: 'hidden',
          softMin: 0,
          softMax: 100,
          color: 'yellow',
        },
        {
          name: 'NIGHT Period Overlay',
          displayName: 'NIGHT Period Overlay',
          unit: 'percent',
          axisPlacement: 'hidden',
          softMin: 0,
          softMax: 100,
          color: 'blue',
        },
      ],
    },
    {
      kind: 'timeseries',
      id: 'veg-systems',
      title: 'Veg atmosphere & equipment',
      // Device states, PID output and duty cycles are automation views, not
      // room monitoring graph data.
      sources: ['sensor', 'light'],
      families: ['co2', 'pressure', 'device', 'light'],
      defaults: { unit: 'ppm' },
      series: [
        {
          name: 'pressure_v',
          displayName: 'Pressure (hPa)',
          unit: 'hpa',
          axisPlacement: 'right',
          decimals: 1,
        },
        {
          name: 'light_v_1',
          displayName: 'Eyefinity Top - Intensity',
          unit: 'percent',
          axisPlacement: 'right',
          softMin: 0,
          softMax: 100,
          color: 'yellow',
        },
        {
          name: 'light_v_2',
          displayName: 'Ridgetop Bottom Right - Intensity',
          unit: 'percent',
          axisPlacement: 'hidden',
          softMin: 0,
          softMax: 100,
          color: 'orange',
        },
        {
          name: 'light_v_3',
          displayName: 'Ridgetop Bottom Left - Intensity',
          unit: 'percent',
          axisPlacement: 'hidden',
          softMin: 0,
          softMax: 100,
          color: 'purple',
        },
      ],
    },
    {
      kind: 'table',
      id: 'veg-statistics',
      title: 'Statistics - All Available Sensors',
      columns: ['Sensor', 'Min', 'Max', 'Average', 'Std Dev'],
      rows: [
        'Dry Bulb (°C)',
        'Wet Bulb (°C)',
        'RH (%)',
        'VPD (kPa)',
        'CO2 (ppm)',
        'Pressure (hPa)',
        'Secondary Temp (°C)',
        'Secondary RH (%)',
        'Water Level (mm)',
      ],
      hasLastUpdate: false,
    },
  ],
}
