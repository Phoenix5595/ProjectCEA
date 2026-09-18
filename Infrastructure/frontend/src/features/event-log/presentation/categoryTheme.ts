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
    chip: 'bg-[var(--event-relay-dim)] text-[var(--event-relay)] border-[var(--event-relay-border)]',
    border: 'border-[var(--event-relay-border)]',
    text: 'text-[var(--event-relay)]',
  },
  manual_override: {
    label: 'Manual override',
    chip: 'bg-[var(--event-manual-override-dim)] text-[var(--event-manual-override)] border-[var(--event-manual-override-border)]',
    border: 'border-[var(--event-manual-override-border)]',
    text: 'text-[var(--event-manual-override)]',
  },
  ramp: {
    label: 'Ramp',
    chip: 'bg-[var(--event-ramp-dim)] text-[var(--event-ramp)] border-[var(--event-ramp-border)]',
    border: 'border-[var(--event-ramp-border)]',
    text: 'text-[var(--event-ramp)]',
  },
  control: {
    label: 'Control',
    chip: 'bg-[var(--event-control-dim)] text-[var(--event-control)] border-[var(--event-control-border)]',
    border: 'border-[var(--event-control-border)]',
    text: 'text-[var(--event-control)]',
  },
  mutation: {
    label: 'Mutation',
    chip: 'bg-[var(--event-mutation-dim)] text-[var(--event-mutation)] border-[var(--event-mutation-border)]',
    border: 'border-[var(--event-mutation-border)]',
    text: 'text-[var(--event-mutation)]',
  },
  alarm: {
    label: 'Alarm',
    chip: 'bg-[var(--event-alarm-dim)] text-[var(--event-alarm)] border-[var(--event-alarm-border)]',
    border: 'border-[var(--event-alarm-border)]',
    text: 'text-[var(--event-alarm)]',
  },
  system: {
    label: 'System',
    chip: 'bg-[var(--event-system-dim)] text-[var(--event-system)] border-[var(--event-system-border)]',
    border: 'border-[var(--event-system-border)]',
    text: 'text-[var(--event-system)]',
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

export const EVENT_CATEGORY_LABELS: Record<EventCategoryName, string> = Object.fromEntries(
  Object.entries(CATEGORY_VISUALS).map(([category, visual]) => [category, visual.label]),
) as Record<EventCategoryName, string>

export function categoryTheme(category: string): CategoryVisual {
  return CATEGORY_VISUALS[category] ?? FALLBACK_VISUAL
}

export function relayActiveStateClass(isActive: boolean): string {
  return isActive ? 'text-[var(--event-relay)] font-bold' : 'text-text-default'
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
