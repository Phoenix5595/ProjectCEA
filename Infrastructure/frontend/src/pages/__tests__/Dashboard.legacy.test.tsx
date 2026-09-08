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
  useSystemStatus: () => ({ systemStats: null, statusDevices: {}, degraded: null }),
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

describe('Dashboard legacy control history removal', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders current dashboard cards and shared EventLog without Recent on/off cards', () => {
    renderPage();

    expect(screen.getByText('Vegetation Room')).toBeInTheDocument();
    expect(screen.getByText('Flower Room')).toBeInTheDocument();

    const zoneRows = screen.getAllByRole('link');
    expect(zoneRows.some((link) => link.textContent?.includes('Lab'))).toBe(true);

    expect(screen.queryByText('Recent on/off')).not.toBeInTheDocument();
  });

  it('renders shared EventLog heading', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /Event Log/i })).toBeInTheDocument();
  });

  it('retains navigation links to zone detail pages', () => {
    const { container } = renderPage();
    const links = Array.from(container.querySelectorAll('a'));
    expect(links.some((a) => a.getAttribute('href')?.includes('/zone/Veg%20Room/main'))).toBe(true);
    expect(links.some((a) => a.getAttribute('href')?.includes('/zone/Flower%20Room/main'))).toBe(true);
    expect(links.some((a) => a.getAttribute('href')?.includes('/zone/Lab/main'))).toBe(true);
  });
});
