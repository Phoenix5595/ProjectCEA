import type { ClimatePeriod } from '../../../types/climatePeriod'
import type { TimelineDraft } from '../state/timelineDraft'

export type ClimatePeriodsTableAdapter = {
  readonly periods: ClimatePeriod[]
  onChange(periods: ClimatePeriod[]): void
}

export function createClimatePeriodsTableAdapter(
  state: TimelineDraft,
  editPeriods: (periods: readonly ClimatePeriod[]) => void,
): ClimatePeriodsTableAdapter {
  return {
    periods: state.draft.periods.map((period) => ({ ...period })),
    onChange: editPeriods,
  }
}
