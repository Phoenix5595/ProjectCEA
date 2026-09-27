/**
 * Colour-coded event-category theme: one owner-approved palette entry per
 * backend EventCategory. Every colour usage is paired with its text label
 * (WCAG 1.4.1 - colour never alone). Tokens are declared per theme in
 * `src/styles/themes.css` as additive `--event-*` variables and consumed via
 * static arbitrary-value utilities so Tailwind's source scanner can see them.
 */

export interface CategoryVisual {
  label: string
  /** Badge classes for the category chip (dim bg, hue text, border). */
  chip: string
  /** Category-row under-border/log hue classes. */
  border: string
  /** Text-colour classes for the category name. */
  text: string
}

const CATEGORY_VISUALS: Record<string, CategoryVisual> = {
  relay: {
    label: 'Relay',
    chip: 'bg-event-relay-dim text-text-default border-event-relay-border',
    border: 'border-event-relay-border',
    text: 'text-event-relay',
  },
  manual_override: {
    label: 'Manual override',
    chip: 'bg-event-manual-override-dim text-text-default border-event-manual-override-border',
    border: 'border-event-manual-override-border',
    text: 'text-event-manual-override',
  },
  ramp: {
    label: 'Ramp',
    chip: 'bg-event-ramp-dim text-text-default border-event-ramp-border',
    border: 'border-event-ramp-border',
    text: 'text-event-ramp',
  },
  control: {
    label: 'Control',
    chip: 'bg-event-control-dim text-text-default border-event-control-border',
    border: 'border-event-control-border',
    text: 'text-event-control',
  },
  mutation: {
    label: 'Mutation',
    chip: 'bg-event-mutation-dim text-text-default border-event-mutation-border',
    border: 'border-event-mutation-border',
    text: 'text-event-mutation',
  },
  alarm: {
    label: 'Alarm',
    chip: 'bg-event-alarm-dim text-text-default border-event-alarm-border',
    border: 'border-event-alarm-border',
    text: 'text-event-alarm',
  },
  system: {
    label: 'System',
    chip: 'bg-event-system-dim text-text-default border-event-system-border',
    border: 'border-event-system-border',
    text: 'text-event-system',
  },
  sensor: {
    label: 'Sensors',
    chip: 'bg-event-sensor-dim text-text-default border-event-sensor-border',
    border: 'border-event-sensor-border',
    text: 'text-event-sensor',
  },
}

const FALLBACK_VISUAL: CategoryVisual = {
  label: 'Other',
  chip: 'bg-surface-tertiary text-text-default border-border-default',
  border: 'border-border-default',
  text: 'text-text-default',
}

export type EventCategoryName =
  | 'relay'
  | 'manual_override'
  | 'ramp'
  | 'control'
  | 'mutation'
  | 'alarm'
  | 'system'
  | 'sensor'

export const EVENT_CATEGORY_LABELS: Record<EventCategoryName, string> = Object.fromEntries(
  Object.entries(CATEGORY_VISUALS).map(([category, visual]) => [category, visual.label])
) as Record<EventCategoryName, string>

export function categoryTheme(category: string): CategoryVisual {
  return CATEGORY_VISUALS[category] ?? FALLBACK_VISUAL
}

/**
 * Presentation bucket for an entry: same as the backend category, except the
 * `system` category is displayed split — sensor/device health events land in
 * the orange "Sensors" bucket while platform events stay slate "System".
 * Filtering keeps working on the raw backend category.
 */
export function displayCategoryOf(entry: { category: string; type: string }): string {
  if (
    entry.category === 'system' &&
    (entry.type.startsWith('sensor.') || entry.type.startsWith('device.'))
  ) {
    return 'sensor'
  }
  return entry.category
}

export function relayActiveStateClass(isActive: boolean): string {
  return isActive ? 'text-event-relay font-bold' : 'text-text-default'
}

/**
 * Relay-activation event types whose state text renders in the relay green
 * shade when the payload carries an engaged/active state.
 */
export function isRelayActiveEvent(entry: {
  category: string
  payload: Record<string, unknown>
}): boolean {
  if (entry.category !== 'relay') return false
  return entry.payload.state === true || entry.payload.observed_state === true
}
