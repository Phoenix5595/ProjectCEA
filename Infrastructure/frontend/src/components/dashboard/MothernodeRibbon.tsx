import { useState } from 'react';
import { Menu, Monitor } from 'lucide-react';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';

import { AppRibbon } from '../chrome/AppRibbon';
import { RibbonMenuButton } from '../chrome/ribbonMenuButton';
import type { SystemStats } from '../../hooks/useSystemStatus';
import { getServiceStatusPresentation, type ServiceStatus } from './serviceStatusPresentation';

const SERVICE_STATUS_DETAIL: Record<ServiceStatus, string> = {
  running: 'Service is active and responding to health probes.',
  stopped: 'Service process is registered but not running.',
  error: 'Service reported an error or failed its health check.',
  unreachable: 'Service did not answer — it may be offline or the network path is down.',
};

function serviceTitle(name: string, status: ServiceStatus, latencyMs: number | undefined): string {
  const lines = [`${name}: ${status}`, SERVICE_STATUS_DETAIL[status]];
  if (typeof latencyMs === 'number') lines.push(`Latency ${latencyMs} ms`);
  return lines.filter(Boolean).join('\n');
}

export interface MothernodeRibbonProps {
  systemStats: SystemStats | null;
}

export function MothernodeRibbon({ systemStats }: MothernodeRibbonProps) {
  const [open, setOpen] = useState(false);

  const cpu = systemStats?.cpu_usage != null ? `${Number(systemStats.cpu_usage).toFixed(0)}%` : '—';
  const mem = systemStats?.memory_usage != null ? `${Number(systemStats.memory_usage).toFixed(0)}%` : '—';
  const disk = systemStats?.disk_usage != null ? `${Number(systemStats.disk_usage).toFixed(0)}%` : '—';
  const cpuTemp = systemStats?.cpu_temp_c != null ? `${Number(systemStats.cpu_temp_c).toFixed(1)}°C` : '—';

  return (
    <>
      <AppRibbon position="bottom">
        <h2 className="text-base font-bold text-text-default flex items-center gap-1 whitespace-nowrap shrink-0">
          <Monitor className="size-5 shrink-0" />
          Mothernode
        </h2>
        <div className="flex items-center gap-3 text-xs text-text-secondary min-w-0 flex-1 font-mono tabular-nums">
          <span>CPU {cpu}</span>
          <span>Mem {mem}</span>
          <span>Disk {disk}</span>
          <span className="whitespace-nowrap">CPU temp {cpuTemp}</span>
          <div className="flex items-center gap-2 min-w-0 flex-1 overflow-x-auto" aria-label="Service status">
            {!systemStats || systemStats.services.length === 0 ? (
              <span className="text-text-muted whitespace-nowrap">Services —</span>
            ) : (
              systemStats.services.map((service, index) => {
                const presentation = getServiceStatusPresentation(service.status);
                return (
                  <span
                    key={`${service.name}-${index}`}
                    className="flex items-center gap-1 whitespace-nowrap shrink-0 cursor-help"
                    title={serviceTitle(service.name, service.status, service.latency_ms)}
                  >
                    <span aria-hidden className={`size-2.5 rounded-full shrink-0 ${presentation.dotClass}`} />
                    <span className="text-text-secondary">{service.name}</span>
                  </span>
                );
              })
            )}
          </div>
        </div>
        <RibbonMenuButton
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={open ? 'Close mothernode status' : 'Open mothernode status'}
        >
          <Menu className="size-5" />
        </RibbonMenuButton>
      </AppRibbon>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          className="max-sm:top-auto max-sm:bottom-0 max-sm:left-0 max-sm:translate-x-0 max-sm:translate-y-0 max-sm:rounded-b-none w-full max-w-md max-h-[70vh] overflow-y-auto bg-surface-primary border-border-default rounded-sm p-4 shadow-xl"
        >
          <DialogTitle className="text-sm font-bold text-text-default uppercase tracking-wide mb-3">Mothernode</DialogTitle>
            {!systemStats ? (
              <p className="text-sm text-text-muted">Loading system status…</p>
            ) : (
              <div className="space-y-3">
                <div className="bg-surface-secondary rounded-sm p-2">
                  <p className="text-xs text-text-muted mb-1">System resources</p>
                  <div className="grid grid-cols-2 gap-2 text-xs font-mono tabular-nums">
                    <p>CPU {cpu}</p>
                    <p>Memory {mem}</p>
                    <p>Disk {disk}</p>
                    <p>Load {systemStats.load_avg ?? '—'}</p>
                    <p>Uptime {systemStats.uptime ?? '—'}</p>
                    <p>CPU temp {cpuTemp}</p>
                    <p>Throttle {systemStats.throttle_status ?? '—'}</p>
                  </div>
                </div>
                <div className="bg-surface-secondary rounded-sm p-2">
                  <p className="text-xs text-text-muted mb-1">Service health</p>
                  <div className="space-y-1">
                    {systemStats.services.length === 0 ? (
                      <p className="text-xs text-text-subtle">Status unknown</p>
                    ) : (
                      systemStats.services.map((service, index) => {
                        const presentation = getServiceStatusPresentation(service.status);
                        return (
                          <div
                            key={index}
                            className="flex items-center justify-between text-xs cursor-help"
                            title={serviceTitle(service.name, service.status, service.latency_ms)}
                          >
                            <span className="text-text-secondary">{service.name}</span>
                            <span className={`flex items-center gap-1.5 font-medium ${presentation.textClass}`}>
                              <span aria-hidden className={`size-2.5 rounded-full ${presentation.dotClass}`} />
                              {presentation.label}
                            </span>
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              </div>
            )}
        </DialogContent>
      </Dialog>
    </>
  );
}
