import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import DashboardOperationsRail from '../DashboardOperationsRail';

const DEGRADED = { active: true, reason: 'stale setpoints', failure_count: 3 } as const;

describe('DashboardOperationsRail', () => {
  it('renders the Lab strip with live temperature and humidity hint', () => {
    render(
      <DashboardOperationsRail
        sensorData={{ Lab_main_lab_temp: 24.5, Lab_main_water_temperature: 19.5 }}
        degraded={null}
        waterLevelPercent={null}
        layout="grid"
        sections="lab"
      />,
    );
    expect(screen.getByRole('region', { name: 'Lab' })).toBeInTheDocument();
    expect(screen.getByText('24.5°C')).toBeInTheDocument();
    expect(screen.getAllByText('sensor not configured').length).toBe(1); // humidity only
    expect(screen.queryByRole('region', { name: 'Water' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Services' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Pi' })).not.toBeInTheDocument();
  });

  it('surfaces the degraded control loop in the Lab strip', () => {
    render(
      <DashboardOperationsRail
        sensorData={{}}
        degraded={DEGRADED}
        waterLevelPercent={null}
        layout="grid"
        sections="lab"
      />,
    );
    expect(screen.getByText(/Control loop degraded: stale setpoints/)).toBeInTheDocument();
  });

  it('renders the SCADA water tank section with honest unavailable states', () => {
    render(
      <DashboardOperationsRail
        sensorData={{ Lab_main_water_temperature: 19.5 }}
        degraded={null}
        waterLevelPercent={null}
        sections="water"
      />,
    );
    expect(screen.getByRole('region', { name: 'Water' })).toBeInTheDocument();
    expect(screen.getByText('19.5°C')).toBeInTheDocument();
    expect(screen.getByText('NO DATA')).toBeInTheDocument();
    // tank level + pressure + irrigation (Lab is not rendered in this mode)
    expect(screen.getAllByText('sensor not configured')).toHaveLength(3);
    expect(screen.queryByRole('region', { name: 'Lab' })).not.toBeInTheDocument();
  });

  it('clamps a supplied tank level and reports it accessibly', () => {
    render(
      <DashboardOperationsRail
        sensorData={{}}
        degraded={null}
        waterLevelPercent={150}
        sections="water"
      />,
    );
    expect(screen.getByRole('img', { name: 'Water tank level 100%' })).toBeInTheDocument();
  });

  it('renders both sections in the default full-rail mode', () => {
    render(
      <DashboardOperationsRail
        sensorData={{ Lab_main_lab_temp: 21.0 }}
        degraded={null}
        waterLevelPercent={null}
      />,
    );
    expect(screen.getByRole('region', { name: 'Lab' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Water' })).toBeInTheDocument();
  });
});
