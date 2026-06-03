/** System status polling hook for dashboard. */
import { useEffect, useState } from 'react';
import { apiClient } from '../services/api';
import { logger } from '../utils/logger';

export interface SystemStats {
  cpu_usage: number | null;
  memory_usage: number | null;
  disk_usage: number | null;
  uptime: string | null;
  load_avg?: string | null;
  process_count?: number | null;
  cpu_temp_c?: number | null;
  throttle_status?: string | null;
  services: Array<{
    name: string;
    status: 'running' | 'stopped' | 'error' | 'unreachable';
    latency_ms?: number;
  }>;
}

export interface UseSystemStatusReturn {
  systemStats: SystemStats | null;
  statusDevices: Record<string, Record<string, Record<string, { intensity?: number; load_percent?: number }>>> | null;
  degraded: {
    active?: boolean;
    reason?: string;
    failure_count?: number;
    success_count?: number;
    updated_at?: string;
  } | null;
}

/** Format uptime from seconds to human-readable string */
function formatUptime(sec: number): string {
  const d = Math.floor(sec / 86400);
  const h = Math.floor((sec % 86400) / 3600);
  return `${d}d ${h}h`;
}

/**
 * Hook for polling system status and health.
 * System stats refresh every 5 seconds, service health every 60 seconds.
 */
export function useSystemStatus(): UseSystemStatusReturn {
  const [systemStats, setSystemStats] = useState<SystemStats | null>(null);
  const [statusDevices, setStatusDevices] = useState<Record<string, Record<string, Record<string, { intensity?: number; load_percent?: number }>>> | null>(null);
  const [degraded, setDegraded] = useState<UseSystemStatusReturn['degraded']>(null);

  // System stats polling (5 seconds)
  useEffect(() => {
    const refreshStats = async () => {
      try {
        const statusResponse = await apiClient.getSystemStatus();
        if (statusResponse) {
          const sys = statusResponse.system;
          setSystemStats(prev => {
            const current = prev || {
              cpu_usage: null, memory_usage: null, disk_usage: null,
              uptime: null, services: []
            };
            return {
              ...current,
              cpu_usage: sys?.cpu_percent ?? current.cpu_usage,
              memory_usage: sys?.memory_percent ?? current.memory_usage,
              disk_usage: sys?.disk_percent ?? current.disk_usage,
              uptime: sys?.uptime_seconds != null ? formatUptime(sys.uptime_seconds) : current.uptime,
              load_avg: Array.isArray(sys?.load_avg)
                ? sys?.load_avg.map((n) => n.toFixed(2)).join(' / ')
                : current.load_avg,
              process_count: sys?.process_count ?? current.process_count,
              cpu_temp_c: sys?.cpu_temp_c ?? current.cpu_temp_c,
              throttle_status: sys?.throttle_status ?? current.throttle_status,
            };
          });

          if (statusResponse.devices) {
            setStatusDevices(statusResponse.devices);
          }
          setDegraded(statusResponse.degraded ?? null);
        }
      } catch (error) {
        logger.warn('System stats refresh failed', error);
      }
    };

    refreshStats();
    const statsInterval = setInterval(refreshStats, 5000);
    return () => clearInterval(statsInterval);
  }, []);

  // Service health polling (60 seconds)
  useEffect(() => {
    const refreshHealth = async () => {
      try {
        const healthData = await apiClient.getSystemHealth();
        setSystemStats(prev => {
          const current = prev || {
            cpu_usage: null, memory_usage: null, disk_usage: null,
            uptime: null, services: []
          };
          return {
            ...current,
            services: Array.isArray(healthData)
              ? healthData.map((s) => ({
                  name: s.name,
                  status: s.status as 'running' | 'stopped' | 'error' | 'unreachable',
                  latency_ms: s.latency_ms
                }))
              : current.services
          };
        });
      } catch (error) {
        logger.warn('Service health refresh failed', error);
      }
    };

    refreshHealth();
    const healthInterval = setInterval(refreshHealth, 60000);
    return () => clearInterval(healthInterval);
  }, []);

  return { systemStats, statusDevices, degraded };
}
