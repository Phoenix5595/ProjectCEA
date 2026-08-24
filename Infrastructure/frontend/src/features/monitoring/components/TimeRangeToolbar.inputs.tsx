/**
 * Absolute-range form for the monitoring time-range toolbar.
 *
 * A presentational component: it renders the two Toronto wall-time inputs,
 * Apply/Clear actions, the fall-fold chooser, and inline validation. It also
 * owns wall-time formatting and preflight validation used by the parent.
 */
import { formatInTimeZone } from 'date-fns-tz'
import {
  offsetLabel,
  parseWallInput,
  resolveWallTimeWithChoice,
  TORONTO_TZ,
  validateRange,
  type FallFoldChoice,
} from './timeRangeToolbar.time'

export function formatWallInput(value: Date): string {
  return formatInTimeZone(value, TORONTO_TZ, "yyyy-MM-dd'T'HH:mm")
}

export function fixedInputError(startInput: string, endInput: string): string | null {
  const startComps = parseWallInput(startInput)
  const endComps = parseWallInput(endInput)
  if (startComps === null || endComps === null) return 'Enter both a start and an end time'

  const start = resolveWallTimeWithChoice(startComps, null)
  const end = resolveWallTimeWithChoice(endComps, null)
  if (start.kind === 'nonexistent' || end.kind === 'nonexistent') {
    return 'That time does not exist in Toronto (spring-forward gap)'
  }
  if (start.kind === 'ambiguous' || end.kind === 'ambiguous') return null
  return validateRange(start.utc, end.utc)
}

export interface FallFoldState {
  field: 'start' | 'end'
  choice: FallFoldChoice | null
  firstUtc: Date
  secondUtc: Date
}

export interface AbsoluteRangeFormProps {
  startInput: string
  endInput: string
  error: string | null
  fallFold: FallFoldState | null
  errorId: string
  onStartChange: (value: string) => void
  onEndChange: (value: string) => void
  onApply: () => void
  onClear: () => void
  onFallFoldChoice: (choice: FallFoldChoice) => void
  applyDisabled: boolean
}

export function AbsoluteRangeForm({
  startInput,
  endInput,
  error,
  fallFold,
  errorId,
  onStartChange,
  onEndChange,
  onApply,
  onClear,
  onFallFoldChoice,
  applyDisabled,
}: AbsoluteRangeFormProps) {
  return (
    <>
      <div className="mon-toolbar__absolute">
        <label>
          Start
          <input
            type="datetime-local"
            value={startInput}
            onChange={(e) => onStartChange(e.target.value)}
            aria-invalid={error !== null}
            aria-describedby={error !== null ? errorId : undefined}
          />
        </label>
        <label>
          End
          <input
            type="datetime-local"
            value={endInput}
            onChange={(e) => onEndChange(e.target.value)}
            aria-invalid={error !== null}
            aria-describedby={error !== null ? errorId : undefined}
          />
        </label>
        <button type="button" onClick={onApply} disabled={applyDisabled}>
          Apply
        </button>
        <button type="button" onClick={onClear}>
          Clear
        </button>
      </div>

      {fallFold !== null && fallFold.choice === null && (
        <div className="mon-toolbar__fold" role="group" aria-label="Choose which occurrence">
          <span>This time occurs twice. Choose:</span>
          <button type="button" onClick={() => onFallFoldChoice('first')}>
            {offsetLabel(fallFold.firstUtc)}
          </button>
          <button type="button" onClick={() => onFallFoldChoice('second')}>
            {offsetLabel(fallFold.secondUtc)}
          </button>
        </div>
      )}

      {error !== null && (
        <p id={errorId} className="mon-toolbar__error" role="alert">
          {error}
        </p>
      )}
    </>
  )
}
