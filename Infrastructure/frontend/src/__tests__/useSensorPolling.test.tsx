import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { useSensorPolling } from '../hooks/useSensorPolling';

const getAllDevices = vi.fn().mockResolvedValue([]);
const getSensorDataBulk = vi.fn().mockResolvedValue({});
const getControlHistory = vi.fn().mockResolvedValue([]);
const getLiveSensorData = vi.fn().mockResolvedValue({});
const getLatestWeather = vi.fn().mockResolvedValue(null);
const getSystemStatus = vi.fn().mockResolvedValue(null);

vi.mock('../services/api', () => ({
  apiClient: {
    getAllDevices: () => getAllDevices(),
    getSensorDataBulk: () => getSensorDataBulk(),
    getControlHistory: () => getControlHistory(),
    getLiveSensorData: () => getLiveSensorData(),
    getLatestWeather: () => getLatestWeather(),
    getSystemStatus: () => getSystemStatus(),
  },
}));

describe('useSensorPolling', () => {
  beforeEach(() => {
    getAllDevices.mockClear();
    getSensorDataBulk.mockClear();
    getControlHistory.mockClear();
    getLiveSensorData.mockClear();
    getLatestWeather.mockClear();
    getSystemStatus.mockClear();
  });

  it('initial load does not call weather or system status (handled elsewhere)', async () => {
    const { unmount } = renderHook(() => useSensorPolling({ interval: 60_000 }));

    await waitFor(() => {
      expect(getAllDevices).toHaveBeenCalled();
    });

    expect(getLatestWeather).not.toHaveBeenCalled();
    expect(getSystemStatus).not.toHaveBeenCalled();
    expect(getSensorDataBulk).toHaveBeenCalled();
    expect(getControlHistory).toHaveBeenCalled();

    unmount();
  });
});
