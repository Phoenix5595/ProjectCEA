/** System status panel component for dashboard. */
import type { SystemStats } from '../hooks/useSystemStatus';

export interface SystemStatusPanelProps {
  systemStats: SystemStats | null;
}

export function SystemStatusPanel({ systemStats }: SystemStatusPanelProps) {
  if (!systemStats) {
    return (
      <div className="bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col lg:w-[26%]">
        <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
          <span className="flex items-center gap-2"><span>🖥</span> Mothernode Status</span>
          <div className="flex items-center gap-1">
            <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-surface-tertiary text-text-muted">?</span>
          </div>
        </div>
        <div className="flex-1 border-b border-border-subtle pb-2 mb-2 overflow-y-auto">
          <div className="bg-surface-secondary rounded-sm p-2">
            <div className="text-xs text-text-muted mb-1">System Status</div>
            <div className="text-xs text-text-subtle">Loading system status...</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-surface-primary rounded-lg border border-border-subtle p-3 flex flex-col lg:w-[26%]">
      <div className="flex-1 border-b border-border-subtle pb-2 mb-2 overflow-y-auto">
        <div className="text-[14px] text-text-muted uppercase font-bold tracking-wider mb-2 flex items-center justify-between">
          <span className="flex items-center gap-2"><span>🖥</span> Mothernode Status</span>
          <div className="flex items-center gap-1">
            <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-status-success-bg/50 text-status-success border border-status-success-border/50 cursor-help" title="All Systems Operational">✓</span>
          </div>
        </div>
        <div className="space-y-2">
          {/* System Resources */}
          <div className="bg-surface-secondary rounded-sm p-2">
            <div className="text-xs text-text-muted mb-1">System Resources</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-text-subtle">CPU</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <span>{systemStats.cpu_usage != null ? `${Number(systemStats.cpu_usage).toFixed(2)}%` : '—'}</span>
                  {systemStats.cpu_usage != null && (
                    <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden">
                      <div className="h-full bg-status-success transition-all" style={{ width: `${Math.min(systemStats.cpu_usage, 100)}%` }} />
                    </div>
                  )}
                </div>
              </div>
              <div>
                <div className="text-text-subtle">Memory</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <span>{systemStats.memory_usage != null ? `${Number(systemStats.memory_usage).toFixed(2)}%` : '—'}</span>
                  {systemStats.memory_usage != null && (
                    <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden">
                      <div className="h-full bg-btn-primary-data transition-all" style={{ width: `${Math.min(systemStats.memory_usage, 100)}%` }} />
                    </div>
                  )}
                </div>
              </div>
              <div>
                <div className="text-text-subtle">Disk</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <span>{systemStats.disk_usage != null ? `${Number(systemStats.disk_usage).toFixed(2)}%` : '—'}</span>
                  {systemStats.disk_usage != null && (
                    <div className="w-8 h-1 bg-surface-tertiary rounded-sm overflow-hidden">
                      <div className="h-full bg-accent-setpoint transition-all" style={{ width: `${Math.min(systemStats.disk_usage, 100)}%` }} />
                    </div>
                  )}
                </div>
              </div>
              <div>
                <div className="text-text-subtle">Load Avg</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.load_avg ?? '—'}</div>
              </div>
              <div>
                <div className="text-text-subtle">Processes</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.process_count ?? '—'}</div>
              </div>
              <div>
                <div className="text-text-subtle">Uptime</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.uptime ?? '—'}</div>
              </div>
            </div>
          </div>

          {/* Service Health */}
          <div className="bg-surface-secondary rounded-sm p-2">
            <div className="text-xs text-text-muted mb-1">Service Health</div>
            <div className="grid grid-cols-1 gap-1">
              {systemStats.services.length === 0 ? (
                <div className="text-xs text-text-subtle">Status unknown</div>
              ) : (
                systemStats.services.map((service, index) => (
                  <div key={index} className="flex items-center justify-between text-xs">
                    <div className="flex items-center gap-2 flex-1">
                      <div className={`w-1.5 h-1.5 rounded-full ${
                        service.status === 'running' ? 'bg-status-success' :
                        service.status === 'unreachable' ? 'bg-status-danger' : 'bg-status-warning'
                      }`} />
                      <span className="text-text-secondary">{service.name}</span>
                    </div>
                    <div className="flex items-center gap-1">
                      {service.latency_ms != null && (
                        <span className="text-text-subtle text-[8px]">{service.latency_ms}ms</span>
                      )}
                      <span className={`px-1 py-0.5 rounded text-[8px] font-medium ${
                        service.status === 'running' ? 'bg-status-success-bg text-status-success-text' :
                        service.status === 'unreachable' || service.status === 'stopped' ? 'bg-status-danger-bg text-status-danger-text' :
                        'bg-status-warning-bg text-status-warning-text'
                      }`}>
                        {service.status === 'running' ? '✓' : '✗'}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Hardware Info */}
          <div className="bg-surface-secondary rounded-sm p-2">
            <div className="text-xs text-text-muted mb-1">Hardware Info</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-text-subtle">Model</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">Raspberry Pi 5</div>
              </div>
              <div>
                <div className="text-text-subtle">CPU Cores</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">4x 2.4GHz</div>
              </div>
              <div>
                <div className="text-text-subtle">RAM Total</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">8GB</div>
              </div>
              <div>
                <div className="text-text-subtle">Storage</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">256GB SSD</div>
              </div>
              <div>
                <div className="text-text-subtle">Temp</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">
                  {systemStats.cpu_temp_c != null ? `${Number(systemStats.cpu_temp_c).toFixed(2)}°C` : '—'}
                </div>
              </div>
              <div>
                <div className="text-text-subtle">Throttle</div>
                <div className="text-text-default font-mono tabular-nums text-[10px]">{systemStats.throttle_status ?? '—'}</div>
              </div>
            </div>
          </div>

          {/* Network */}
          <div className="bg-surface-secondary rounded-sm p-2">
            <div className="text-xs text-text-muted mb-1">Network</div>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div>
                <div className="text-text-subtle">API (8000)</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success" />
                  <span>Active</span>
                </div>
              </div>
              <div>
                <div className="text-text-subtle">Auto (8001)</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success" />
                  <span>Active</span>
                </div>
              </div>
              <div>
                <div className="text-text-subtle">CAN Bus</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success" />
                  <span>250kbps</span>
                </div>
              </div>
              <div>
                <div className="text-text-subtle">WebSocket</div>
                <div className="text-text-default font-mono tabular-nums flex items-center gap-1">
                  <div className="w-1.5 h-1.5 rounded-full bg-status-success animate-pulse" />
                  <span>Live</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
