import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { ThemeProvider } from '../../contexts/ThemeContext'
import Dashboard from '../Dashboard'

vi.mock('../../hooks/useWebSocket', () => ({
  useWebSocket: () => ({ devices: [], sensorData: {} }),
}))

vi.mock('../../hooks/useSensorPolling', () => ({
  useSensorPolling: () => ({
    devices: [],
    sensorData: {},
    sensorMeta: {},
    zoneStatus: {},
    lastPollAt: null,
    lightDisplayNames: {},
    flowerClusterWarnings: [],
    loading: false,
  }),
  deriveZoneSensorStatus: vi.fn(() => ({
    quality: 'missing',
    newestObservedAtMs: null,
    ageMs: null,
    source: null,
    error: null,
  })),
}))
vi.mock('../../hooks/useDashboardLiveData', () => ({
  useDashboardLiveData: () => ({
    devices: [],
    sensorData: {},
    sensorMeta: {},
    zoneStatus: {},
    lightDisplayNames: {},
    flowerClusterWarnings: [],
    loading: false,
    lastPollAt: null,
    transport: 'degraded',
  }),
}))

vi.mock('../../hooks/useControlSnapshot', () => ({
  useControlSnapshot: () => ({ snapshot: null }),
}))

vi.mock('../../hooks/useDashboardScheduleContext', () => ({
  useDashboardScheduleContext: () => ({
    modes: {},
    schedules: [],
    loading: false,
    error: null,
    updatedAt: null,
    refresh: vi.fn(),
  }),
  getDashboardMode: () => null,
}))

vi.mock('../../hooks/useDashboardTrends', () => ({
  useDashboardTrends: () => ({
    byRoom: {},
    updatedAt: {},
    errors: {},
    loading: false,
    refresh: vi.fn(),
  }),
}))

vi.mock('../../hooks/useActiveAlarms', () => ({
  useActiveAlarms: () => ({
    alarms: [],
    loading: false,
    error: null,
    updatedAt: null,
    acknowledgingKey: null,
    acknowledgementErrors: {},
    refresh: vi.fn(),
    acknowledge: vi.fn(),
  }),
  alarmIdentity: (alarm: { location: string; cluster: string; alarm_name: string }) =>
    `${alarm.location}:${alarm.cluster}:${alarm.alarm_name}`,
}))

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
}))

vi.mock('../../hooks/useCalendarEvents', () => ({
  useCalendarEvents: () => ({ events: [], loading: false, refresh: vi.fn() }),
}))

vi.mock('../../features/event-log/state/useEventLog', () => ({
  useEventLog: () => ({ entries: [], isConnected: true }),
  useEventLogPagination: () => ({
    paging: { oldestCursor: null, hasMore: false, loadingOlder: false },
    loadOlder: async () => undefined,
  }),
}))

vi.mock('../../services/api', () => ({
  apiClient: {
    getLatestWeather: vi.fn().mockResolvedValue({ data: null }),
    getSystemStatus: vi.fn().mockResolvedValue({}),
    getSystemHealth: vi.fn().mockResolvedValue([]),
  },
}))

function renderPage() {
  return render(
    <MemoryRouter>
      <ThemeProvider>
        <Dashboard />
      </ThemeProvider>
    </MemoryRouter>
  )
}

describe('Dashboard dense layout', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders Flower, Veg, and Lab as horizontal room links', () => {
    renderPage()

    const zoneLinks = screen
      .getAllByRole('link')
      .filter(link => link.getAttribute('href')?.startsWith('/zone/'))
    expect(zoneLinks).toHaveLength(3)
    expect(zoneLinks.map(l => l.getAttribute('href'))).toEqual(
      expect.arrayContaining([
        '/zone/Flower%20Room/main',
        '/zone/Veg%20Room/main',
        '/zone/Lab/main',
      ])
    )
  })

  it('renders exactly one shared EventLog', () => {
    renderPage()
    expect(screen.getAllByRole('heading', { name: /Event Log/i })).toHaveLength(1)
  })

  it('renders both ribbons', () => {
    renderPage()
    expect(screen.getByText('Siberian Jungle')).toBeInTheDocument()
    expect(screen.getByText('Mothernode')).toBeInTheDocument()
  })

  it('renders the Lab room bar and the water tank section', () => {
    renderPage()
    expect(
      screen.getAllByRole('link').some(link => link.getAttribute('href')?.includes('/zone/Lab/'))
    ).toBe(true)
    expect(screen.getByRole('region', { name: 'Water' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Services' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Pi' })).not.toBeInTheDocument()
  })

  it('renders the calendar inspector with its overview actions', () => {
    renderPage()
    expect(screen.getByRole('complementary', { name: 'Calendar inspector' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New event' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create flower grow plan' })).toBeInTheDocument()
  })
})
