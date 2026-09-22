/** Full-height right operations/SCADA rail: Lab, services, Pi, and water tank. */
import type { SystemStats, UseSystemStatusReturn } from '../../hooks/useSystemStatus';
import { getServiceStatusPresentation } from './serviceStatusPresentation';

export interface DashboardOperationsRailProps {
  sensorData: Record<string, number>;
  systemStats: SystemStats | null;
  degraded: UseSystemStatusReturn['degraded'];
  /** Canonical tank level percent; null when no real level sensor exists. */
  waterLevelPercent: number | null;
  /**
   * 'stack' (default): narrow full-height rail, sections stacked.
   * 'grid': wide slot (e.g. dashboard lower row), sections in a 2x2 grid.
   */
  layout?: 'stack' | 'grid';
}

function formatMetric(value: number | undefined, unit: string, digits = 1): string {
  return value != null ? `${Number(value).toFixed(digits)}${unit}` : '—';
}

/** SCADA-style water tank: cylindrical vessel with domed roof, siding seams,
 * inlet/outlet pipes and a side sight-glass level gauge with graduation ticks.
 * With no sensor data the gauge renders dashed with an explicit NO DATA tag —
 * never a fabricated level. Static SVG, no animation. */
function WaterTankGraphic({ levelPercent }: { levelPercent: number | null }) {
  // Vessel interior: y 20 (top) .. 82 (bottom); gauge sits alongside.
  const BODY_TOP = 20;
  const BODY_BOTTOM = 82;
  const level = levelPercent != null ? Math.min(100, Math.max(0, levelPercent)) : null;
  const hasLevel = level != null;
  const outline = 'var(--border-emphasis)';
  const seam = 'var(--border-subtle)';
  return (
    <div
      className="flex flex-wrap items-center gap-2"
      role="img"
      aria-label={
        hasLevel
          ? `Water tank level ${level}%`
          : 'Water tank level: sensor not configured'
      }
    >
      <svg viewBox="0 0 76 100" className="h-24 w-[4.75rem] shrink-0" aria-hidden focusable="false">
        {/* inlet pipe from the roof */}
        <path d="M36 12 V5 H50 V13" fill="none" stroke={outline} strokeWidth="2" />
        {/* domed roof */}
        <path
          d="M12 20 Q38 4 64 20 Z"
          fill="var(--surface-tertiary)"
          stroke={outline}
          strokeWidth="2"
        />
        {/* cylindrical body */}
        <rect x="12" y={BODY_TOP} width="52" height={BODY_BOTTOM - BODY_TOP} fill="var(--surface-tertiary)" stroke={outline} strokeWidth="2" />
        {/* water level inside the vessel */}
        {level != null && (
          <rect
            x="13"
            y={BODY_BOTTOM - (level / 100) * (BODY_BOTTOM - BODY_TOP)}
            width="50"
            height={(level / 100) * (BODY_BOTTOM - BODY_TOP)}
            fill="#3b82f6"
            opacity="0.4"
          />
        )}
        {/* siding seams */}
        <line x1="27" y1={BODY_TOP + 1} x2="27" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="1" />
        <line x1="38" y1={BODY_TOP + 1} x2="38" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="1" />
        <line x1="49" y1={BODY_TOP + 1} x2="49" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="1" />
        {/* ladder rail hint on the vessel */}
        <line x1="20" y1={BODY_TOP + 4} x2="20" y2={BODY_BOTTOM - 4} stroke={seam} strokeWidth="1" />
        <line x1="16" y1={BODY_TOP + 4} x2="16" y2={BODY_BOTTOM - 4} stroke={seam} strokeWidth="1" />
        {[30, 42, 54, 66, 74].map((y) => (
          <line key={y} x1="16" y1={y} x2="20" y2={y} stroke={seam} strokeWidth="1" />
        ))}
        {/* skirt + ground line */}
        <rect x="18" y={BODY_BOTTOM} width="40" height="6" fill="var(--surface-secondary)" stroke={outline} strokeWidth="2" />
        <line x1="4" y1="94" x2="72" y2="94" stroke={outline} strokeWidth="2" />
        {/* outlet pipe with valve */}
        <path d="M12 74 H4 M6 72 H12 M6 72 V78" fill="none" stroke={outline} strokeWidth="2" />
        {/* sight glass level gauge with graduation ticks */}
        <rect
          x="66"
          y={BODY_TOP}
          width="8"
          height={BODY_BOTTOM - BODY_TOP}
          fill="var(--surface-base)"
          stroke={outline}
          strokeWidth="1.5"
          strokeDasharray={level != null ? undefined : '3 2'}
        />
        {level != null && (
          <rect
            x="66.75"
            y={BODY_BOTTOM - (level / 100) * (BODY_BOTTOM - BODY_TOP)}
            width="6.5"
            height={(level / 100) * (BODY_BOTTOM - BODY_TOP)}
            fill="#38bdf8"
            opacity="0.85"
          />
        )}
        {[0, 25, 50, 75, 100].map((pct) => (
          <line
            key={pct}
            x1="66"
            y1={BODY_BOTTOM - (pct / 100) * (BODY_BOTTOM - BODY_TOP)}
            x2="70"
            y2={BODY_BOTTOM - (pct / 100) * (BODY_BOTTOM - BODY_TOP)}
            stroke={outline}
            strokeWidth="1"
          />
        ))}
        {!hasLevel && (
          <text x="38" y="54" textAnchor="middle" fontSize="8.5" fontWeight="700" letterSpacing="0.06em" fill="var(--color-text-muted, #94a3b8)">
            NO DATA
          </text>
        )}
      </svg>
      <div className="flex flex-col justify-center text-xs min-w-0">
        <span className="text-text-secondary">Tank level</span>
        <span className="font-mono tabular-nums text-text-default">
          {level != null ? `${level}%` : '—'}
        </span>
        {!hasLevel && <span className="text-text-muted">sensor not configured</span>}
      </div>
    </div>
  );
}

interface RailSectionProps {
  title: string;
  children: React.ReactNode;
  boxed?: boolean;
}

function RailSection({ title, children, boxed = false }: RailSectionProps) {
  return (
    <section
      aria-label={title}
      className={
        boxed
          ? 'border border-border-subtle rounded-md px-2 py-2 min-h-0 overflow-y-auto'
          : 'border-b border-border-subtle px-2 py-2'
      }
    >
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
  layout = 'stack',
}: DashboardOperationsRailProps) {
  const labTemp = sensorData['Lab_main_lab_temp'];
  const waterTemp = sensorData['Lab_main_water_temperature'];
  const labHumidity = sensorData['Lab_main_humidity'];
  const grid = layout === 'grid';

  return (
    <div
      className={
        grid
          ? 'grid h-full min-h-0 grid-cols-2 auto-rows-fr gap-2 overflow-y-auto bg-surface-primary border border-border-subtle rounded-lg p-2'
          : 'flex flex-col h-full min-h-0 overflow-y-auto bg-surface-primary border border-border-subtle rounded-lg'
      }
    >
      <RailSection title="Lab" boxed={grid}>
        <MetricRow label="Temp" value={formatMetric(labTemp, '°C')} />
        <MetricRow
          label="Humidity"
          value={labHumidity != null ? formatMetric(labHumidity, '%') : '—'}
          hint={labHumidity != null ? undefined : 'sensor not configured'}
        />
      </RailSection>

      <RailSection title="Services" boxed={grid}>
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

      <RailSection title="Pi" boxed={grid}>
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

      <RailSection title="Water" boxed={grid}>
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
