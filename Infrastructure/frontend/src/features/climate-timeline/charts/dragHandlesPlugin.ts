import type uPlot from 'uplot'
import type { ClimatePeriod } from '../../../types/climatePeriod'
import { timeToMinutes } from '../../../utils/timeMath'
import { periodLengthMinutes } from '../../../utils/climatePeriodTimeline'
import {
  minutesOfDay,
  timeOfDayToInstant,
  VALUE_SNAP,
  type BoundaryEdge,
  type ValueMetric,
} from './dragInteraction'

export interface DragHandlesCallbacks {
  readonly getPeriods: () => readonly ClimatePeriod[]
  readonly getWindow: () => { readonly start: number; readonly end: number }
  readonly isDragEnabled: () => boolean
  readonly onDragStart: () => void
  readonly onDragEnd: () => void
  readonly pushBoundary: (index: number, edge: BoundaryEdge, rawMinutes: number) => void
  readonly pushValue: (index: number, metric: ValueMetric, rawValue: number) => void
  readonly commitBoundaryKey: (index: number, edge: BoundaryEdge, deltaMinutes: number) => void
  readonly commitValueKey: (index: number, metric: ValueMetric, deltaValue: number) => void
}

export type DragHandleSpec =
  | { readonly kind: 'boundary'; readonly index: number; readonly edge: BoundaryEdge; readonly label: string }
  | { readonly kind: 'value'; readonly index: number; readonly metric: ValueMetric; readonly label: string }

const VALUE_SCALE: Record<ValueMetric, string> = {
  heating: 'temp',
  cooling: 'temp',
  vpd: 'vpd',
  co2: 'co2',
}

const METRIC_LABEL: Record<ValueMetric, string> = {
  heating: 'heating setpoint',
  cooling: 'cooling setpoint',
  vpd: 'VPD setpoint',
  co2: 'CO₂ setpoint',
}

const VALUE_FIELD: Record<ValueMetric, 'heating_setpoint' | 'cooling_setpoint' | 'vpd_setpoint' | 'co2_setpoint'> = {
  heating: 'heating_setpoint',
  cooling: 'cooling_setpoint',
  vpd: 'vpd_setpoint',
  co2: 'co2_setpoint',
}

const VALUE_METRICS: readonly ValueMetric[] = ['heating', 'cooling', 'vpd', 'co2']

function gripsSignature(periods: readonly ClimatePeriod[]): string {
  return periods.map((period, index) => {
    const values = VALUE_METRICS.map((metric) => period[VALUE_FIELD[metric]] != null ? metric[0] : '').join('')
    return `${index}:${values}`
  }).join('|')
}

function handleSpecs(periods: readonly ClimatePeriod[]): DragHandleSpec[] {
  const specs: DragHandleSpec[] = []
  periods.forEach((period, index) => {
    const name = period.period_name || `period ${index + 1}`
    specs.push({ kind: 'boundary', index, edge: 'start', label: `Adjust ${name} start` })
    specs.push({ kind: 'boundary', index, edge: 'end', label: `Adjust ${name} end` })
    for (const metric of VALUE_METRICS) {
      if (period[VALUE_FIELD[metric]] != null) {
        specs.push({ kind: 'value', index, metric, label: `Adjust ${name} ${METRIC_LABEL[metric]}` })
      }
    }
  })
  return specs
}

function testIdFor(spec: DragHandleSpec): string {
  return spec.kind === 'boundary'
    ? `control-timeline-boundary-grip-${spec.index}-${spec.edge}`
    : `control-timeline-value-grip-${spec.index}-${spec.metric}`
}

function midMinute(period: ClimatePeriod): number {
  const startMin = timeToMinutes(period.start_time)
  const endMin = timeToMinutes(period.end_time)
  const length = periodLengthMinutes(startMin, endMin)
  return (startMin + Math.floor(length / 2)) % 1440
}

/**
 * Canvas-anchored DOM grips positioned through valToPos on every draw. Grips
 * exist only for scheduled draft values; the effective series has no grips.
 * Drag commits flow through the caller's rAF-coalesced callbacks.
 */
export function dragHandlesPlugin(callbacks: DragHandlesCallbacks): uPlot.Plugin {
  let container: HTMLDivElement | null = null
  let signature = ''
  let activeDrag: { readonly spec: DragHandleSpec; onMove: (event: MouseEvent) => void; onUp: () => void } | null = null

  const buildGrips = (u: uPlot): void => {
    if (container === null) return
    const periods = callbacks.getPeriods()
    const nextSignature = gripsSignature(periods)
    const enabled = callbacks.isDragEnabled()
    if (!enabled) {
      if (container.childElementCount > 0) container.replaceChildren()
      signature = ''
      return
    }
    if (signature === nextSignature && container.childElementCount > 0) return
    signature = nextSignature
    container.replaceChildren()
    for (const spec of handleSpecs(periods)) {
      const grip = document.createElement('button')
      grip.type = 'button'
      grip.dataset.testid = testIdFor(spec)
      grip.setAttribute('aria-label', spec.label)
      grip.style.position = 'absolute'
      grip.style.border = '1px solid rgba(128, 128, 128, 0.6)'
      grip.style.background = 'rgba(128, 128, 128, 0.35)'
      grip.style.padding = '0'
      grip.style.cursor = spec.kind === 'boundary' ? 'ew-resize' : 'ns-resize'
      if (spec.kind === 'boundary') {
        grip.style.width = '8px'
        grip.style.height = '100%'
        grip.style.top = '0'
      } else {
        grip.style.width = '10px'
        grip.style.height = '10px'
        grip.style.borderRadius = '5px'
      }
      grip.addEventListener('mousedown', (event) => startDrag(event, spec, u))
      grip.addEventListener('keydown', (event) => {
        if (spec.kind === 'boundary') {
          if (event.key === 'ArrowLeft') callbacks.commitBoundaryKey(spec.index, spec.edge, -5)
          if (event.key === 'ArrowRight') callbacks.commitBoundaryKey(spec.index, spec.edge, 5)
        } else if (spec.metric !== undefined) {
          const step = VALUE_SNAP[spec.metric]
          if (event.key === 'ArrowUp') callbacks.commitValueKey(spec.index, spec.metric, step)
          if (event.key === 'ArrowDown') callbacks.commitValueKey(spec.index, spec.metric, -step)
        }
      })
      container.appendChild(grip)
    }
  }

  const positionGrips = (u: uPlot): void => {
    if (container === null) return
    const periods = callbacks.getPeriods()
    const window = callbacks.getWindow()
    for (const child of Array.from(container.children)) {
      const grip = child as HTMLElement
      const testId = grip.dataset.testid ?? ''
      const boundaryMatch = /^control-timeline-boundary-grip-(\d+)-(start|end)$/.exec(testId)
      const valueMatch = /^control-timeline-value-grip-(\d+)-(heating|cooling|vpd|co2)$/.exec(testId)
      if (boundaryMatch) {
        const period = periods[Number(boundaryMatch[1])]
        const edge = boundaryMatch[2] as BoundaryEdge
        if (!period) continue
        const instant = timeOfDayToInstant(timeToMinutes(edge === 'start' ? period.start_time : period.end_time), window.start)
        if (instant > window.end) {
          grip.style.display = 'none'
          continue
        }
        grip.style.display = ''
        grip.style.left = `${u.valToPos(instant, 'x', true) - 4}px`
      } else if (valueMatch) {
        const period = periods[Number(valueMatch[1])]
        const metric = valueMatch[2] as ValueMetric
        if (!period) continue
        const value = period[VALUE_FIELD[metric]]
        if (value == null) continue
        const instant = timeOfDayToInstant(midMinute(period), window.start)
        if (instant > window.end) {
          grip.style.display = 'none'
          continue
        }
        grip.style.display = ''
        grip.style.left = `${u.valToPos(instant, 'x', true) - 5}px`
        grip.style.top = `${u.valToPos(value, VALUE_SCALE[metric], true) - 5}px`
      }
    }
  }

  const startDrag = (event: MouseEvent, spec: DragHandleSpec, u: uPlot): void => {
    if (!callbacks.isDragEnabled() || activeDrag !== null) return
    event.preventDefault()
    callbacks.onDragStart()
    const rootRect = u.root.getBoundingClientRect()
    const onMove = (moveEvent: MouseEvent): void => {
      if (spec.kind === 'boundary') {
        const xCanvas = moveEvent.clientX - rootRect.left
        const instant = u.posToVal(xCanvas, 'x')
        callbacks.pushBoundary(spec.index, spec.edge, minutesOfDay(instant))
        return
      }
      if (spec.metric === undefined) return
      const yCanvas = moveEvent.clientY - rootRect.top
      const raw = u.posToVal(yCanvas, VALUE_SCALE[spec.metric])
      callbacks.pushValue(spec.index, spec.metric, raw)
    }
    const onUp = (): void => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      activeDrag = null
      callbacks.onDragEnd()
    }
    activeDrag = { spec, onMove, onUp }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  return {
    hooks: {
      init: (u) => {
        container = document.createElement('div')
        container.style.position = 'absolute'
        container.style.inset = '0'
        container.style.pointerEvents = 'none'
        u.root.appendChild(container)
        buildGrips(u)
      },
      draw: (u) => {
        buildGrips(u)
        positionGrips(u)
      },
      destroy: () => {
        if (activeDrag !== null) {
          window.removeEventListener('mousemove', activeDrag.onMove)
          window.removeEventListener('mouseup', activeDrag.onUp)
          activeDrag = null
        }
        container?.remove()
        container = null
      },
    },
  }
}
