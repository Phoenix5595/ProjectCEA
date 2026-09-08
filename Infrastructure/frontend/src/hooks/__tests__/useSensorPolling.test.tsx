import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useSensorPolling } from '../useSensorPolling';
import { apiClient } from '../../services/api';

vi.mock('../../services/api', () => ({
  apiClient: {
    getAllDevices: vi.fn(),
    getSensorDataBulk: vi.fn(),
    getDevicesForLocationCluster: vi.fn(),
    getLiveSensorData: vi.fn(),
    getAllLiveSensorData: vi.fn(),
  },
}));

describe('useSensorPolling legacy control history removal', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.mocked(apiClient.getAllDevices).mockResolvedValue([]);
    vi.mocked(apiClient.getSensorDataBulk).mockResolvedValue({});
    vi.mocked(apiClient.getDevicesForLocationCluster).mockResolvedValue({ location: 'Veg Room', cluster: 'main', devices: {} });
    vi.mocked(apiClient.getLiveSensorData).mockResolvedValue({});
    vi.mocked(apiClient.getAllLiveSensorData).mockResolvedValue([]);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it('does not call getControlHistory during dashboard polling', async () => {
    renderHook(() => useSensorPolling({ interval: 5000 }));

    await waitFor(() => expect(apiClient.getAllDevices).toHaveBeenCalled());

    vi.advanceTimersByTime(15000);
    await waitFor(() => expect(apiClient.getLiveSensorData).toHaveBeenCalled());

    expect(apiClient.getControlHistory).toBeUndefined();
    expect(
      Object.getOwnPropertyNames(apiClient).some((name) => /controlHistory/i.test(name))
    ).toBe(false);
  });

  it('exposes only dashboard data state without controlHistory field', async () => {
    const { result } = renderHook(() => useSensorPolling({ interval: 5000 }));

    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current).toHaveProperty('devices');
    expect(result.current).toHaveProperty('sensorData');
    expect(result.current).toHaveProperty('lightDisplayNames');
    expect(result.current).toHaveProperty('flowerClusterWarnings');
    expect(result.current).toHaveProperty('loading');
    expect(result.current).toHaveProperty('refresh');

    expect(result.current).not.toHaveProperty('controlHistory');
  });
});
