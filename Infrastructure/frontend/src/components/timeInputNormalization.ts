/** Normalize a typed clock text into HH:MM when the operator typed it without separators. */
export function normalizeTypedTimeText(raw: string): string {
  if (/^\d{2}:\d{2}$/.test(raw)) return raw
  const digits = raw.replace(/\D/g, '')
  if (digits.length === 0 || digits.length > 4) return raw
  if (digits.length === 1 || digits.length === 2) {
    const hourPair = digits.padStart(2, '0')
    if (Number(hourPair) > 23) return raw
    return `${hourPair}:00`
  }
  const hourPair = digits.slice(0, digits.length - 2).padStart(2, '0')
  if (Number(hourPair) > 23) return raw
  return `${hourPair}:${digits.slice(-2)}`
}
