import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../../contexts/ThemeContext';

import { MothernodeRibbon } from '../MothernodeRibbon';
import type { SystemStats } from '../../../hooks/useSystemStatus';

const STATS: SystemStats = {
  cpu_usage: 15,
  memory_usage: 42,
  disk_usage: 28,
  uptime: '1d 0h',
  load_avg: '0.50 / 0.30 / 0.20',
  cpu_temp_c: 45,
  throttle_status: '0x0',
  services: [
    { name: 'automation-service', status: 'running', latency_ms: 12 },
    { name: 'can-processor', status: 'stopped' },
    { name: 'backend', status: 'error', latency_ms: 0 },
  ],
};

function renderRibbon(systemStats: SystemStats | null = STATS) {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <MothernodeRibbon systemStats={systemStats} />
      </ThemeProvider>
    </MemoryRouter>,
  );
}

describe('MothernodeRibbon service pins', () => {
  it('renders status dots per service without status text on the bar', () => {
    renderRibbon();
    expect(screen.getByText('automation-service')).toBeInTheDocument();
    expect(screen.getByText('can-processor')).toBeInTheDocument();
    // Dots are colored circles (vivid tokens), never text labels.
    expect(document.querySelector('.bg-status-success-vivid')).not.toBeNull();
    expect(document.querySelector('.bg-status-danger-vivid')).not.toBeNull();
    // The status word itself lives only in the hover tooltip / dialog.
    expect(screen.queryByText('running')).not.toBeInTheDocument();
    expect(screen.queryByText('stopped')).not.toBeInTheDocument();
  });

  it('explains each pin on hover with status, meaning and latency', () => {
    renderRibbon();
    const pin = screen.getByText('automation-service').closest('span[title]');
    expect(pin?.getAttribute('title')).toContain('automation-service: running');
    expect(pin?.getAttribute('title')).toContain('responding to health probes');
    expect(pin?.getAttribute('title')).toContain('Latency 12 ms');
  });

  it('shows machine stats and Services — when service data is unknown', () => {
    renderRibbon(null);
    expect(screen.getByText('CPU —')).toBeInTheDocument();
    expect(screen.getByText('Services —')).toBeInTheDocument();
  });

  it('renders machine stats with real values', () => {
    renderRibbon();
    expect(screen.getByText('CPU 15%')).toBeInTheDocument();
    expect(screen.getByText('Mem 42%')).toBeInTheDocument();
    expect(screen.getByText('Disk 28%')).toBeInTheDocument();
    expect(screen.getByText('CPU temp 45.0°C')).toBeInTheDocument();
  });
});
