import { useState } from 'react';

import { AppRibbon } from '../chrome/AppRibbon';
import { RibbonMenuButton } from '../chrome/ribbonMenuButton';
import type { SystemStats } from '../../hooks/useSystemStatus';

export interface MothernodeRibbonProps {
  systemStats: SystemStats | null;
}

export function MothernodeRibbon({ systemStats }: MothernodeRibbonProps) {
  const [open, setOpen] = useState(false);

  const cpu = systemStats?.cpu_usage != null ? `${Number(systemStats.cpu_usage).toFixed(0)}%` : '—';
  const mem = systemStats?.memory_usage != null ? `${Number(systemStats.memory_usage).toFixed(0)}%` : '—';
  const worst =
    systemStats?.services.find((s) => s.status !== 'running')?.name ??
    (systemStats?.services.length ? 'All OK' : '—');

  return (
    <>
      <AppRibbon position="bottom">
        <h2 className="text-base font-bold text-text-default flex items-center gap-1 whitespace-nowrap shrink-0">
          <span className="text-xl leading-none">🖥</span>
          Mothernode
        </h2>
        <div className="flex items-center gap-3 text-xs text-text-secondary min-w-0 flex-1 font-mono tabular-nums">
          <span>CPU {cpu}</span>
          <span>Mem {mem}</span>
          <span className="truncate text-text-muted" title="Services">
            {worst}
          </span>
        </div>
        <RibbonMenuButton
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={open ? 'Close mothernode status' : 'Open mothernode status'}
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          </svg>
        </RibbonMenuButton>
      </AppRibbon>

      {open && (
        <div
          className="fixed inset-0 z-50 flex items-end justify-center sm:items-center sm:p-4"
          role="dialog"
          aria-modal="true"
          aria-label="Mothernode status"
        >
          <button
            type="button"
            className="absolute inset-0 bg-black/40"
            onClick={() => setOpen(false)}
            aria-label="Close"
          />
          <div className="relative w-full max-w-md max-h-[70vh] overflow-y-auto bg-surface-primary border border-border-default rounded-sm p-4 shadow-xl">
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-bold text-text-default uppercase tracking-wide">Mothernode</h2>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="text-text-muted hover:text-text-default px-2"
              >
                ✕
              </button>
            </div>
            {!systemStats ? (
              <p className="text-sm text-text-muted">Loading system status…</p>
            ) : (
              <div className="space-y-3">
                <div className="bg-surface-secondary rounded-sm p-2">
                  <p className="text-xs text-text-muted mb-1">System resources</p>
                  <div className="grid grid-cols-2 gap-2 text-xs font-mono tabular-nums">
                    <p>CPU {cpu}</p>
                    <p>Memory {mem}</p>
                    <p>
                      Disk{' '}
                      {systemStats.disk_usage != null
                        ? `${Number(systemStats.disk_usage).toFixed(0)}%`
                        : '—'}
                    </p>
                    <p>Load {systemStats.load_avg ?? '—'}</p>
                    <p>Uptime {systemStats.uptime ?? '—'}</p>
                  </div>
                </div>
                <div className="bg-surface-secondary rounded-sm p-2">
                  <p className="text-xs text-text-muted mb-1">Service health</p>
                  <div className="space-y-1">
                    {systemStats.services.length === 0 ? (
                      <p className="text-xs text-text-subtle">Status unknown</p>
                    ) : (
                      systemStats.services.map((service, index) => (
                        <div key={index} className="flex items-center justify-between text-xs">
                          <span className="text-text-secondary">{service.name}</span>
                          <span
                            className={`px-1 py-0.5 rounded text-[8px] font-medium ${
                              service.status === 'running'
                                ? 'bg-status-success-bg text-status-success-text'
                                : 'bg-status-danger-bg text-status-danger-text'
                            }`}
                          >
                            {service.status}
                          </span>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
