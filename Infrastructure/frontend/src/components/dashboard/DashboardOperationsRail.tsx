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

/** Low-contrast 2-D water drum with a reserved level strip; no decoration, no animation. */
function WaterTankGraphic({ levelPercent }: { levelPercent: number | null }) {
  const hasLevel = levelPercent != null;
  const clamped = hasLevel ? Math.min(100, Math.max(0, levelPercent)) : null;
  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      role="img"
      aria-label={
        hasLevel
          ? `Water tank level ${clamped}%`
          : 'Water tank level: sensor not configured'
      }
    >
      <div className="relative w-14 border border-border-default rounded-sm bg-surface-secondary overflow-hidden">
        {/* Level strip: ~15% of vessel width, filled from the bottom */}
        <div className="absolute inset-y-1 left-1 w-[15%] border border-border-subtle rounded-xs">
          {hasLevel && (
            <div
              className="absolute inset-x-0 bottom-0 bg-sky-500/60"
              style={{ height: `${clamped}%` }}
            />
          )}
        </div>
      </div>
      <div className="flex flex-col justify-center text-xs">
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
