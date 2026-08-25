/**
 * Tests for the monitoring semantic table/stat primitives.
 *
 * Covers the happy path (Veg sensor-value ordering/formatting, Flower paired
 * averages, statistics values + sorting, and the chart data-table disclosure
 * with provenance) and the failure path (missing paired averages and null PID
 * values are never fabricated).
 */
import { describe, expect, it } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import type { LiveSensorValue, SensorStatistics } from '../../api'
import type { AlignedData } from '../../data'
import { seriesKey } from '../../data/alignSeries.types'
import { flowerManifest } from '../../config/flowerManifest'
import { vegManifest } from '../../config/vegManifest'
import { ChartDataTable } from '../ChartDataTable'
import { MonitoringFreshness } from '../MonitoringFreshness'
import { RoomAveragesTable } from '../RoomAveragesTable'
import { SensorValueTable } from '../SensorValueTable'
import { StatisticsTable } from '../StatisticsTable'

function tablePanel(manifest: typeof flowerManifest, title: string) {
  const panel = manifest.panels.find((p) => p.kind === 'table' && p.title === title)
  if (!panel || panel.kind !== 'table') throw new Error(`table panel not found: ${title}`)
  return panel
}

function cellForRow(table: HTMLElement, label: string, col = 1): string {
  const rows = within(table).getAllByRole('row')
  const row = rows.find((r) => within(r).queryAllByRole('cell')[0]?.textContent === label)
  if (!row) throw new Error(`row not found: ${label}`)
  return within(row).queryAllByRole('cell')[col]?.textContent ?? ''
}

const NOW = new Date('2026-07-15T12:00:00Z')

describe('monitoring table primitives', () => {
  it('labels retained range data with its last-good and error timestamps', () => {
    render(
      <MonitoringFreshness
        lastGoodAt={new Date(2026, 6, 15, 11, 59)}
        errorAt={new Date(2026, 6, 15, 12)}
      />,
    )

    expect(screen.getByRole('status')).toHaveTextContent('Data stale.')
    expect(screen.getByRole('status')).toHaveTextContent('Last good monitoring range: 2026/07/15 11:59:00.')
    expect(screen.getByRole('status')).toHaveTextContent('Latest range error: 2026/07/15 12:00:00.')
  })

  it('renders the averages title above the Sensor and Value columns', () => {
    const averages = tablePanel(flowerManifest, 'Averages')
    const { container } = render(
      <RoomAveragesTable title="Averages" rows={averages.rows} front={[]} back={[]} />,
    )
    const table = within(container).getByRole('table', { name: 'Averages' })
    const headerRows = within(table).getAllByRole('row').slice(0, 2)

    expect(within(headerRows[0]).getByRole('columnheader')).toHaveTextContent('Averages')
    expect(within(headerRows[0]).getByRole('columnheader')).toHaveAttribute('colspan', '2')
    expect(within(headerRows[1]).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Sensor',
      'Value',
    ])
  })

  it('matches Flower Veg statistics and accessible data tables', () => {
    // --- Veg sensor values: canonical ordering + unit-family formatting ---
    const veg = tablePanel(vegManifest, 'Sensor Values')
    const vegValues: LiveSensorValue[] = [
      { sensor: 'dry_bulb_v', value: 24.5, timestamp: NOW },
      { sensor: 'wet_bulb_v', value: 18.2, timestamp: NOW },
      { sensor: 'rh_v', value: 55.3, timestamp: NOW },
      { sensor: 'vpd_v', value: 1.234, timestamp: NOW },
      { sensor: 'co2_v', value: 812, timestamp: NOW },
      { sensor: 'pressure_v', value: 1013.4, timestamp: NOW },
      { sensor: 'secondary_temp_v', value: 23.1, timestamp: NOW },
      { sensor: 'secondary_rh_v', value: 60.0, timestamp: NOW },
      { sensor: 'water_level_v', value: 42.7, timestamp: NOW },
    ]
    const { container: vegContainer } = render(
      <SensorValueTable title="Sensor Values" rows={veg.rows} values={vegValues} nodeSuffix="v" now={NOW} />,
    )
    const vegTable = within(vegContainer).getByRole('table', { name: 'Sensor Values' })
    expect(cellForRow(vegTable, 'Dry Bulb')).toBe('24.5°C')
    expect(cellForRow(vegTable, 'VPD')).toBe('1.23 kPa')
    expect(cellForRow(vegTable, 'CO2')).toBe('812 ppm')
    expect(cellForRow(vegTable, 'Pressure')).toBe('1013.4 hPa')
    expect(cellForRow(vegTable, 'Water Level')).toBe('42.7 mm')
    expect(cellForRow(vegTable, 'Last Update')).toMatch(/^\d{4}\/\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/)

    // --- Flower averages: paired Front/Back ---
    const avg = tablePanel(flowerManifest, 'Averages')
    const front: LiveSensorValue[] = [
      { sensor: 'dry_bulb_f', value: 22.0, timestamp: NOW },
      { sensor: 'wet_bulb_f', value: 17.0, timestamp: NOW },
      { sensor: 'rh_f', value: 50.0, timestamp: NOW },
      { sensor: 'vpd_f', value: 1.0, timestamp: NOW },
      { sensor: 'co2_f', value: 800, timestamp: NOW },
      { sensor: 'pressure_f', value: 1012.0, timestamp: NOW },
      { sensor: 'water_level_f', value: 40.0, timestamp: NOW },
    ]
    const back: LiveSensorValue[] = [
      { sensor: 'dry_bulb_b', value: 24.0, timestamp: NOW },
      { sensor: 'wet_bulb_b', value: 19.0, timestamp: NOW },
      { sensor: 'rh_b', value: 60.0, timestamp: NOW },
      { sensor: 'vpd_b', value: 1.4, timestamp: NOW },
      { sensor: 'co2_b', value: 900, timestamp: NOW },
      { sensor: 'pressure_b', value: 1014.0, timestamp: NOW },
      { sensor: 'water_level_b', value: 50.0, timestamp: NOW },
    ]
    const { container: avgContainer } = render(
      <RoomAveragesTable title="Averages" rows={avg.rows} front={front} back={back} />,
    )
    const avgTable = within(avgContainer).getByRole('table', { name: 'Averages' })
    expect(cellForRow(avgTable, 'Dry Bulb Avg')).toBe('23.0°C')
    expect(cellForRow(avgTable, 'RH Avg')).toBe('55.0%')
    expect(cellForRow(avgTable, 'VPD Avg')).toBe('1.20 kPa')
    expect(cellForRow(avgTable, 'CO2 Avg')).toBe('850 ppm')

    // --- Flower statistics: values + numeric sorting ---
    const statsPanel = tablePanel(flowerManifest, 'Statistics - All Available Sensors')
    const stats: SensorStatistics[] = [
      { sensor: 'dry_bulb_f', node: 'front', minimum: 20.1, maximum: 26.3, average: 23.2, stddev_samp: 1.5, sample_count: 100 },
      { sensor: 'dry_bulb_b', node: 'back', minimum: 21.0, maximum: 27.0, average: 24.0, stddev_samp: 1.2, sample_count: 100 },
      { sensor: 'vpd_f', node: 'front', minimum: 0.8, maximum: 1.6, average: 1.2, stddev_samp: 0.2, sample_count: 100 },
    ]
    const { container: statsContainer } = render(
      <StatisticsTable title="Statistics" rows={statsPanel.rows} statistics={stats} />,
    )
    const statsTable = within(statsContainer).getByRole('table', { name: 'Statistics' })
    expect(cellForRow(statsTable, 'Dry Bulb (°C) - Front', 1)).toBe('20.1')
    expect(cellForRow(statsTable, 'Dry Bulb (°C) - Front', 3)).toBe('23.2')
    expect(cellForRow(statsTable, 'VPD (kPa) - Front', 3)).toBe('1.20')

    // Sort by Average ascending: VPD (1.2) before dry_bulb_f (23.2) before dry_bulb_b (24.0).
    fireEvent.click(within(statsTable).getByRole('button', { name: /Average/ }))
    const rowsAsc = within(statsTable).getAllByRole('row').slice(1)
    expect(within(rowsAsc[0]).getAllByRole('cell')[0].textContent).toBe('VPD (kPa) - Front')
    // Toggle to descending: dry_bulb_b (24.0) first.
    fireEvent.click(within(statsTable).getByRole('button', { name: /Average/ }))
    const rowsDesc = within(statsTable).getAllByRole('row').slice(1)
    expect(within(rowsDesc[0]).getAllByRole('cell')[0].textContent).toBe('Dry Bulb (°C) - Back')

    // --- Chart data table disclosure: non-envelope series + provenance ---
    const aligned: AlignedData = {
      x: [1750000000000, 1750000001000],
      series: [
        { key: seriesKey('sensor', 'dry_bulb_f', 'mean'), label: 'Dry Bulb (°C) - Front', kind: 'sensor', source: 'sensor', metric: 'dry_bulb_f', family: 'temperature', role: 'mean', y: [23.2, 23.5], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
        { key: seriesKey('sensor', 'dry_bulb_f', 'min'), label: 'Dry Bulb (°C) - Front min', kind: 'sensor', source: 'sensor', metric: 'dry_bulb_f', family: 'temperature', role: 'min', y: [22.9, 23.1], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
        { key: seriesKey('sensor', 'dry_bulb_f', 'max'), label: 'Dry Bulb (°C) - Front max', kind: 'sensor', source: 'sensor', metric: 'dry_bulb_f', family: 'temperature', role: 'max', y: [23.6, 23.9], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '°C', unitFamily: 'celsius' },
        { key: seriesKey('climate', 'heater_pid_output', 'point'), label: 'Heater - PID Output', kind: 'point', source: 'climate', metric: 'heater_pid_output', family: 'device', role: 'point', y: [null, 42.0], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%', unitFamily: 'percent' },
      ],
      bands: [{ key: seriesKey('sensor', 'dry_bulb_f', 'band'), minKey: seriesKey('sensor', 'dry_bulb_f', 'min'), maxKey: seriesKey('sensor', 'dry_bulb_f', 'max') }],
      photoperiod: [],
      nowIndex: 0,
      aggregated: false,
    }
    const { container: chartContainer } = render(<ChartDataTable title="Climate" data={aligned} />)
    const toggle = screen.getByRole('button', { name: 'View data as table' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const chartTable = within(chartContainer).getByRole('table', { name: 'Climate data' })
    const headers = within(chartTable).getAllByRole('columnheader').map((h) => h.textContent)
    expect(headers).toContain('Dry Bulb (°C) - Front')
    expect(headers).toContain('Heater - PID Output')
    expect(headers).not.toContain('Dry Bulb (°C) - Front min')
    expect(headers).not.toContain('Dry Bulb (°C) - Front max')
    const firstRow = within(chartTable).getAllByRole('row')[1]
    expect(within(firstRow).getAllByRole('cell')[1].textContent).toContain('23.2°C')
    expect(within(firstRow).getAllByRole('cell')[1].textContent).toContain('recorded/exact')
    expect(within(firstRow).getAllByRole('cell')[2].textContent).toBe('—')
  }, 30000)

  it('does not fabricate missing paired averages or null PID', () => {
    // --- Flower averages with an empty (valid) Front cluster ---
    const avg = tablePanel(flowerManifest, 'Averages')
    const back: LiveSensorValue[] = [
      { sensor: 'dry_bulb_b', value: 24.0, timestamp: NOW },
      { sensor: 'rh_b', value: 55.0, timestamp: NOW },
    ]
    const { container: avgContainer } = render(
      <RoomAveragesTable title="Averages" rows={avg.rows} front={[]} back={back} />,
    )
    const avgTable = within(avgContainer).getByRole('table', { name: 'Averages' })
    expect(cellForRow(avgTable, 'Dry Bulb Avg')).toBe('—')
    expect(cellForRow(avgTable, 'RH Avg')).toBe('—')
    expect(cellForRow(avgTable, 'Last Update')).toMatch(/^\d{4}\/\d{2}\/\d{2}/)

    // --- Chart data table preserves null PID as an em dash ---
    const aligned: AlignedData = {
      x: [1750000000000],
      series: [
        { key: seriesKey('climate', 'heater_pid_output', 'point'), label: 'Heater - PID Output', kind: 'point', source: 'climate', metric: 'heater_pid_output', family: 'device', role: 'point', y: [null], origin: 'recorded', quality: 'exact', isAggregated: false, unit: '%', unitFamily: 'percent' },
      ],
      bands: [],
      photoperiod: [],
      nowIndex: 0,
      aggregated: false,
    }
    const { container: chartContainer } = render(<ChartDataTable title="Climate" data={aligned} />)
    fireEvent.click(screen.getByRole('button', { name: 'View data as table' }))
    const chartTable = within(chartContainer).getByRole('table', { name: 'Climate data' })
    const firstRow = within(chartTable).getAllByRole('row')[1]
    expect(within(firstRow).getAllByRole('cell')[1].textContent).toBe('—')
  })
})
