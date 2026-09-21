import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ThemeProvider } from '../../contexts/ThemeContext';
import Dashboard from '../Dashboard';

vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: () => ({ devices: [], sensorData: {} }),
}));

vi.mock('../../hooks/useSensorPolling', () => ({
  useSensorPolling: () => ({
    devices: [],
    sensorData: {},
    lightDisplayNames: {},
    flowerClusterWarnings: [],
    loading: false,
  }),
}));

vi.mock('../../hooks/useSystemStatus', () => ({
  useSystemStatus: () => ({
    systemStats: {
      cpu_usage: 10,
      memory_usage: 20,
      disk_usage: 30,
      uptime: '0d 1h',
      services: [{ name: 'automation-service', status: 'running' }],
    },
    statusDevices: {},
    degraded: null,
  }),
}));

vi.mock('../../hooks/useCalendarEvents', () => ({
  useCalendarEvents: () => ({ events: [], loading: false, refresh: vi.fn() }),
}));

vi.mock('../../features/event-log/state/useEventLog', () => ({
  useEventLog: () => ({ entries: [], isConnected: true }),
  useEventLogPagination: () => ({
    paging: { oldestCursor: null, hasMore: false, loadingOlder: false },
    loadOlder: async () => undefined,
  }),
}));

vi.mock('../../services/api', () => ({
  apiClient: {
    getLatestWeather: vi.fn().mockResolvedValue({ data: null }),
    getSystemStatus: vi.fn().mockResolvedValue({}),
    getSystemHealth: vi.fn().mockResolvedValue([]),
  },
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <Dashboard />
      </ThemeProvider>
    </MemoryRouter>
  );
}

describe('Dashboard dense layout', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders exactly the Flower and Veg room links (Lab lives in the rail)', () => {
    renderPage();

    const zoneLinks = screen
      .getAllByRole('link')
      .filter((link) => link.getAttribute('href')?.startsWith('/zone/'));
    expect(zoneLinks).toHaveLength(2);
    expect(zoneLinks.map((l) => l.getAttribute('href'))).toEqual(
      expect.arrayContaining(['/zone/Flower%20Room/main', '/zone/Veg%20Room/main'])
    );
    expect(zoneLinks.some((link) => link.getAttribute('href')?.includes('Lab'))).toBe(false);
  });

  it('renders exactly one shared EventLog', () => {
    renderPage();
    expect(screen.getAllByRole('heading', { name: /Event Log/i })).toHaveLength(1);
  });

  it('renders both ribbons', () => {
    renderPage();
    expect(screen.getByText('Siberian Jungle')).toBeInTheDocument();
    expect(screen.getByText('Mothernode')).toBeInTheDocument();
  });

  it('renders the operations rail with Lab section and service text', () => {
    renderPage();
    expect(screen.getByRole('region', { name: 'Lab' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Services' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Pi' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Water' })).toBeInTheDocument();
    // Rail pin + MothernodeRibbon pin
    expect(screen.getAllByText('automation-service')).toHaveLength(2);
  });

  it('renders the calendar inspector with its overview actions', () => {
    renderPage();
    expect(screen.getByRole('complementary', { name: 'Calendar inspector' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'New event' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create flower grow plan' })).toBeInTheDocument();
  });
});
