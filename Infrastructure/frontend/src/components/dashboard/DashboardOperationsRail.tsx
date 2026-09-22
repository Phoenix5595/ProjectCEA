/** Operations rail: Lab strip in the dashboard lower row and the SCADA water
 * tank under the event log. Machine/service status lives in the bottom
 * Mothernode ribbon, so this rail only carries what the ribbon does not. */
import type { UseSystemStatusReturn } from '../../hooks/useSystemStatus';

export interface DashboardOperationsRailProps {
  sensorData: Record<string, number>;
  /** Kept for API compatibility; service/machine status lives in the ribbon. */
  systemStats?: UseSystemStatusReturn['systemStats'];
  degraded?: UseSystemStatusReturn['degraded'];
  /** Canonical tank level percent; null when no real level sensor exists. */
  waterLevelPercent: number | null;
  /**
   * 'stack' (default): narrow rail, sections stacked.
   * 'grid': wide slot (dashboard lower row), sections side by side.
   */
  layout?: 'stack' | 'grid';
  /** Which sections to render. 'water' is shown alone under the event log. */
  sections?: 'all' | 'lab' | 'water';
}

function formatMetric(value: number | undefined, unit: string, digits = 1): string {
  return value != null ? `${Number(value).toFixed(digits)}${unit}` : '—';
}

/** SCADA-style water tank: cylindrical vessel with domed roof, siding seams,
 * inlet/outlet pipes and a side sight-glass level gauge with graduation ticks.
 * With no sensor data the gauge renders dashed with an explicit NO DATA tag —
 * never a fabricated level. Static SVG, no animation. */
function WaterTankGraphic({
  levelPercent,
  waterTemp,
}: {
  levelPercent: number | null;
  waterTemp: number | undefined;
}) {
  const BODY_TOP = 20;
  const BODY_BOTTOM = 82;
  const level = levelPercent != null ? Math.min(100, Math.max(0, levelPercent)) : null;
  const hasLevel = level != null;
  const outline = 'var(--border-emphasis)';
  const seam = 'var(--border-subtle)';
  return (
    <div
      className="relative w-full aspect-square min-h-0"
      role="img"
      aria-label={
        hasLevel
          ? `Water tank level ${level}%`
          : 'Water tank level: sensor not configured'
      }
    >
      <svg viewBox="0 0 100 100" className="absolute inset-0 size-full" aria-hidden focusable="false">
        <path d="M46 13 V5 H64 V14" fill="none" stroke={outline} strokeWidth="1.8" />
        <path d="M15 20 Q48 3 81 20 Z" fill="var(--surface-tertiary)" stroke={outline} strokeWidth="1.8" />
        <rect x="15" y={BODY_TOP} width="66" height={BODY_BOTTOM - BODY_TOP} fill="var(--surface-tertiary)" stroke={outline} strokeWidth="1.8" />
        {level != null && (
          <rect
            x="16"
            y={BODY_BOTTOM - (level / 100) * (BODY_BOTTOM - BODY_TOP)}
            width="64"
            height={(level / 100) * (BODY_BOTTOM - BODY_TOP)}
            fill="#3b82f6"
            opacity="0.4"
          />
        )}
        {[30, 42, 54, 66, 74].map((y) => (
          <line key={y} x1="20" y1={y} x2="76" y2={y} stroke={seam} strokeWidth="0.7" />
        ))}
        <line x1="29" y1={BODY_TOP + 1} x2="29" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="0.8" />
        <line x1="51" y1={BODY_TOP + 1} x2="51" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="0.8" />
        <line x1="70" y1={BODY_TOP + 1} x2="70" y2={BODY_BOTTOM - 1} stroke={seam} strokeWidth="0.8" />
        <rect x="23" y={BODY_BOTTOM} width="50" height="6" fill="var(--surface-secondary)" stroke={outline} strokeWidth="1.8" />
        <line x1="8" y1="94" x2="92" y2="94" stroke={outline} strokeWidth="1.8" />
        <path d="M15 74 H7 M9 71 V77" fill="none" stroke={outline} strokeWidth="1.8" />
        <rect
          x="83"
          y={BODY_TOP}
          width="10"
          height={BODY_BOTTOM - BODY_TOP}
          fill="var(--surface-base)"
          stroke={outline}
          strokeWidth="1.4"
          strokeDasharray={level != null ? undefined : '3 2'}
        />
        {level != null && (
          <rect
            x="83.75"
            y={BODY_BOTTOM - (level / 100) * (BODY_BOTTOM - BODY_TOP)}
            width="8.5"
            height={(level / 100) * (BODY_BOTTOM - BODY_TOP)}
            fill="#38bdf8"
            opacity="0.85"
          />
        )}
        {[0, 25, 50, 75, 100].map((pct) => (
          <line
            key={pct}
            x1="83"
            y1={BODY_BOTTOM - (pct / 100) * (BODY_BOTTOM - BODY_TOP)}
            x2="88"
            y2={BODY_BOTTOM - (pct / 100) * (BODY_BOTTOM - BODY_TOP)}
            stroke={outline}
            strokeWidth="0.9"
          />
        ))}
      </svg>
      <div className="absolute left-[22%] right-[24%] top-[34%] bottom-[12%] flex flex-col justify-center gap-1 px-1 text-center">
        <span className="text-[clamp(0.55rem,1.1vw,0.8rem)] font-bold uppercase tracking-wide text-text-secondary">
          Water tank
        </span>
        {!hasLevel && (
          <span className="rounded-sm border border-border-subtle/70 bg-surface-base/25 px-1 py-0.5 font-mono text-[clamp(0.55rem,1vw,0.75rem)] font-bold text-text-muted">
            NO DATA
          </span>
        )}
        <span className="rounded-sm border border-border-subtle/70 bg-surface-base/25 px-1 py-0.5 font-mono text-[clamp(0.55rem,1vw,0.75rem)] text-text-default">
          Temp {waterTemp != null ? `${waterTemp.toFixed(1)}°C` : '—'}
        </span>
        <span className="rounded-sm border border-border-subtle/70 bg-surface-base/25 px-1 py-0.5 font-mono text-[clamp(0.5rem,0.9vw,0.7rem)] text-text-secondary">
          Pressure —
        </span>
        <span className="rounded-sm border border-border-subtle/70 bg-surface-base/25 px-1 py-0.5 font-mono text-[clamp(0.5rem,0.9vw,0.7rem)] text-text-secondary">
          Irrig. today —
        </span>
      </div>
      <div className="absolute right-0 top-[31%] text-[clamp(0.45rem,0.8vw,0.6rem)] font-mono text-text-muted [writing-mode:vertical-rl]">
        {level != null ? `${level}%` : '—'}
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
  degraded,
  waterLevelPercent,
  layout = 'stack',
  sections = 'all',
}: DashboardOperationsRailProps) {
  const labTemp = sensorData['Lab_main_lab_temp'];
  const waterTemp = sensorData['Lab_main_water_temperature'];
  const labHumidity = sensorData['Lab_main_humidity'];
  const grid = layout === 'grid';
  const showLab = sections !== 'water';
  const showWater = sections !== 'lab';
  // Pi machine stats and service status live in the bottom Mothernode ribbon;
  // the lower-row strip only carries Lab (and degraded, while it is active).

  return (
    <div
      className={
        grid
          ? `grid h-full min-h-0 grid-cols-1 auto-rows-fr gap-2 overflow-y-auto bg-surface-primary border border-border-subtle rounded-lg p-2`
          : sections === 'water'
            ? 'flex flex-col min-h-0 bg-surface-primary border border-border-subtle rounded-lg'
            : 'flex flex-col h-full min-h-0 overflow-y-auto bg-surface-primary border border-border-subtle rounded-lg'
      }
    >
      {showLab && (
        <RailSection title="Lab" boxed={grid}>
          <MetricRow label="Temp" value={formatMetric(labTemp, '°C')} />
          <MetricRow
            label="Humidity"
            value={labHumidity != null ? formatMetric(labHumidity, '%') : '—'}
            hint={labHumidity != null ? undefined : 'sensor not configured'}
          />
          {degraded?.active && (
            <p role="alert" className="mt-1.5 text-xs text-status-warning-text">
              Control loop degraded: {degraded.reason || 'recovering'} · failures{' '}
              {degraded.failure_count ?? 0}
            </p>
          )}
        </RailSection>
      )}

      {showWater && (
        <RailSection title="Water" boxed={grid}>
          <WaterTankGraphic levelPercent={waterLevelPercent} waterTemp={waterTemp} />
        </RailSection>
      )}
    </div>
  );
}
