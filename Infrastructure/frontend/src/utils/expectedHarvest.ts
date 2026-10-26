/** Expected-harvest date display helpers. */

/** 'yyyy-MM-dd' → 'Jan 3' on a fixed UTC calendar day. */
export function formatHarvestDay(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}
