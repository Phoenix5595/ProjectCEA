/** Full-height right operations/SCADA rail: Lab, services, Pi, and water tank. */
import type { SystemStats, UseSystemStatusReturn } from '../../hooks/useSystemStatus';
import { getServiceStatusPresentation } from './serviceStatusPresentation';

export interface DashboardOperationsRailProps {
  sensorData: Record<string, number>;
  systemStats: SystemStats | null;
  degraded: UseSystemStatusReturn['degraded'];
  /** Canonical tank level percent; null when no real level sensor exists. */
  waterLevelPercent: number | null;
}

function formatMetric(value: number | undefined, unit: string, digits = 1): string {
  return value != null ? `${Number(value).toFixed(digits)}${unit}` : '—';
}

/** 2-D SCADA water drum: visible vessel outline, graduation ticks, reserved level strip.
 * With no sensor data the tank still renders — dashed strip + NO DATA, never a level. */
function WaterTankGraphic({ levelPercent }: { levelPercent: number | null }) {
  const hasLevel = levelPercent != null;
  const clamped = hasLevel ? Math.min(100, Math.max(0, levelPercent)) : null;
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="img"
      aria-label={
        hasLevel
          ? `Water tank level ${clamped}%`
          : 'Water tank level: sensor not configured'
      }
    >
      <div className="relative h-24 w-16 shrink-0 rounded-sm border border-border-emphasis bg-surface-tertiary">
        {/* Graduation ticks at 25/50/75% of vessel height */}
        {[25, 50, 75].map((pct) => (
          <div
            key={pct}
            aria-hidden
            className="absolute left-0 right-0 border-t border-dashed border-border-default"
            style={{ bottom: `${pct}%` }}
          />
        ))}
        {/* Reserved level strip (~18% of vessel width), filled from the bottom */}
        <div
          className={`absolute bottom-1.5 top-1.5 left-1.5 w-[18%] rounded-xs ${
            hasLevel ? 'border border-border-emphasis bg-surface-base' : 'border border-dashed border-border-emphasis'
          }`}
        >
          {hasLevel && (
            <div
              className="absolute inset-x-0 bottom-0 bg-sky-500/70"
              style={{ height: `${clamped}%` }}
            />
          )}
        </div>
        {!hasLevel && (
          <span className="absolute left-[26%] right-1 top-1/2 -translate-y-1/2 text-center text-[9px] font-semibold uppercase tracking-wide text-text-muted leading-tight">
            no data
          </span>
        )}
      </div>
      <div className="flex flex-col justify-center text-xs min-w-0">
        <span className="text-text-secondary">Tank level</span>
        <span className="font-mono tabular-nums text-text-default">
          {hasLevel ? `${clamped}%` : '—'}
        </span>
        {!hasLevel && <span className="text-text-muted">sensor not configured</span>}
      </div>
    </div>
  );
}

interface RailSectionProps {
  title: string;
  children: React.ReactNode;
}

function RailSection({ title, children }: RailSectionProps) {
  return (
    <section aria-label={title} className="border-b border-border-subtle px-2 py-2">
      <h3 className="text-xs font-bold uppercase tracking-wide text-text-muted mb-1.5">{title}</h3>
      {children}
    </section>
  );
}

interface MetricRowProps {
  label: string;
  value: string;
  hint?: string;
}

function MetricRow({ label, value, hint }: MetricRowProps) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-0.5 text-xs">
      <span className="text-text-secondary shrink-0">{label}</span>
      <span className="font-mono tabular-nums text-text-default text-right min-w-0">
        {value}
        {hint && <span className="block text-text-muted font-sans text-left">{hint}</span>}
      </span>
    </div>
  );
}

export default function DashboardOperationsRail({
  sensorData,
  systemStats,
  degraded,
  waterLevelPercent,
}: DashboardOperationsRailProps) {
  const labTemp = sensorData['Lab_main_lab_temp'];
  const waterTemp = sensorData['Lab_main_water_temperature'];
  const labHumidity = sensorData['Lab_main_humidity'];

  return (
    <div className="flex flex-col h-full min-h-0 overflow-y-auto bg-surface-primary border border-border-subtle rounded-lg">
      <RailSection title="Lab">
        <MetricRow label="Temp" value={formatMetric(labTemp, '°C')} />
        <MetricRow
          label="Humidity"
          value={labHumidity != null ? formatMetric(labHumidity, '%') : '—'}
          hint={labHumidity != null ? undefined : 'sensor not configured'}
        />
      </RailSection>

      <RailSection title="Services">
        {!systemStats || systemStats.services.length === 0 ? (
          <p className="text-xs text-text-muted">Services —</p>
        ) : (
          <ul className="space-y-1.5">
            {systemStats.services.map((service, index) => {
              const presentation = getServiceStatusPresentation(service.status);
              return (
                <li key={`${service.name}-${index}`} className="text-xs">
                  <span className="flex items-center gap-1.5 min-w-0">
                    <span
                      aria-hidden
                      className={`size-2 rounded-full shrink-0 ${presentation.dotClass}`}
                    />
                    <span className="text-text-secondary min-w-0 break-words">{service.name}</span>
                  </span>
                  <span className={`block pl-3.5 font-medium ${presentation.textClass}`}>
                    {presentation.label}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </RailSection>

      <RailSection title="Pi">
        <MetricRow label="CPU" value={formatMetric(systemStats?.cpu_usage ?? undefined, '%', 0)} />
        <MetricRow
          label="Memory"
          value={formatMetric(systemStats?.memory_usage ?? undefined, '%', 0)}
        />
        <MetricRow label="Disk" value={formatMetric(systemStats?.disk_usage ?? undefined, '%', 0)} />
        <MetricRow label="Load" value={systemStats?.load_avg ?? '—'} />
        <MetricRow
          label="CPU temp"
          value={formatMetric(systemStats?.cpu_temp_c ?? undefined, '°C')}
        />
        <MetricRow label="Uptime" value={systemStats?.uptime ?? '—'} />
        <MetricRow label="Throttle" value={systemStats?.throttle_status ?? '—'} />
        {degraded?.active && (
          <p role="alert" className="mt-1.5 text-xs text-status-warning-text">
            Control loop degraded: {degraded.reason || 'recovering'} · failures{' '}
            {degraded.failure_count ?? 0}
          </p>
        )}
      </RailSection>

      <RailSection title="Water">
        <WaterTankGraphic levelPercent={waterLevelPercent} />
        <div className="mt-2 space-y-1">
          <MetricRow
            label="Water temp"
            value={waterTemp != null ? formatMetric(waterTemp, '°C') : '—'}
          />
          <MetricRow label="Pressure" value="—" hint="sensor not configured" />
          <MetricRow label="Irrigation today" value="—" hint="sensor not configured" />
        </div>
      </RailSection>
    </div>
  );
}
