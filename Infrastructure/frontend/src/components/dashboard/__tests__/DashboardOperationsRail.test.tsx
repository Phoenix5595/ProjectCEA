import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import DashboardOperationsRail from '../DashboardOperationsRail';
import type { SystemStats } from '../../../hooks/useSystemStatus';

const STATS: SystemStats = {
  cpu_usage: 12.3,
  memory_usage: 45.6,
  disk_usage: 78.9,
  uptime: '1d 2h',
  load_avg: '0.10 / 0.20 / 0.30',
  cpu_temp_c: 47.5,
  throttle_status: '0x0',
  services: [
    { name: 'automation-service', status: 'running' },
    { name: 'can-processor', status: 'stopped' },
    { name: 'backend', status: 'error' },
  ],
};

function renderRail(props: Partial<Parameters<typeof DashboardOperationsRail>[0]> = {}) {
  return render(
    <DashboardOperationsRail
      sensorData={{}}
      systemStats={STATS}
      degraded={null}
      waterLevelPercent={null}
      {...props}
    />
  );
}

describe('DashboardOperationsRail', () => {
  it('renders live Lab temperature and water temperature from sensorData', () => {
    renderRail({ sensorData: { Lab_main_lab_temp: 24.5, Lab_main_water_temperature: 19.5 } });
    expect(screen.getByText('24.5°C')).toBeInTheDocument();
    expect(screen.getByText('19.5°C')).toBeInTheDocument();
  });

  it('labels unavailable humidity, tank level, pressure and irrigation explicitly', () => {
    renderRail();
    const unavailable = screen.getAllByText('sensor not configured');
    // Lab humidity + tank level + pressure + irrigation today
    expect(unavailable).toHaveLength(4);
    expect(screen.getByText('Pressure')).toBeInTheDocument();
    expect(screen.getByText('Irrigation today')).toBeInTheDocument();
  });

  it('renders each service with both a text label and a status pin', () => {
    renderRail();
    expect(screen.getByText('automation-service')).toBeInTheDocument();
    expect(screen.getByText('running')).toBeInTheDocument();
    expect(screen.getByText('stopped')).toBeInTheDocument();
    expect(screen.getByText('error')).toBeInTheDocument();
    expect(document.querySelector('.bg-status-success-vivid')).not.toBeNull();
    expect(document.querySelector('.bg-status-danger-vivid')).not.toBeNull();
  });

  it('renders Pi stats with dashes for missing values', () => {
    renderRail({ systemStats: { ...STATS, load_avg: null, throttle_status: null } });
    expect(screen.getByText('12%')).toBeInTheDocument();
    expect(screen.getByText('47.5°C')).toBeInTheDocument();
    expect(screen.getByText('1d 2h')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThanOrEqual(2);
  });

  it('surfaces the degraded control loop as a labeled warning', () => {
    renderRail({
      degraded: { active: true, reason: 'stale setpoints', failure_count: 3 },
    });
    expect(screen.getByText(/Control loop degraded: stale setpoints/)).toBeInTheDocument();
  });

  it('renders no service pins when service data is unknown', () => {
    renderRail({ systemStats: null });
    expect(screen.getByText('Services —')).toBeInTheDocument();
    expect(screen.queryByText('automation-service')).not.toBeInTheDocument();
  });

  it('clamps a supplied tank level to 0-100 and reports it accessibly', () => {
    renderRail({ waterLevelPercent: 150 });
    expect(
      screen.getByRole('img', { name: 'Water tank level 100%' })
    ).toBeInTheDocument();
  });

  it('reports a mid-range tank level accessibly', () => {
    renderRail({ waterLevelPercent: 42 });
    expect(
      screen.getByRole('img', { name: 'Water tank level 42%' })
    ).toBeInTheDocument();
  });
});
