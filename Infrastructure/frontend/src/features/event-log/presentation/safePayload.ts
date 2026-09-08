const SAFE_FIELD_KEYS = new Set([
  'device_id', 'state', 'room', 'cluster', 'setpoint', 'active',
  'mode', 'mode_id', 'channel', 'board', 'pin', 'intensity',
  'notes_changed', 'notes_length', 'old_mode', 'new_mode',
  'crop_batch_id', 'url_hostname', 'displaced_id',
])

const REDACTED_KEYS = new Set([
  'password', 'secret', 'token', 'api_key', 'apikey',
  'authorization', 'credential', 'private_key',
])

export function extractSafeFields(payload: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(payload)) {
    if (REDACTED_KEYS.has(key)) continue
    if (SAFE_FIELD_KEYS.has(key)) result[key] = value
  }
  return result
}

const MAX_STRING_LENGTH = 100

export function formatPayloadValue(value: unknown): string {
  if (value === null || value === undefined) return '--'
  if (typeof value === 'string') return value.length > MAX_STRING_LENGTH ? `${value.slice(0, MAX_STRING_LENGTH)}...` : value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  try { return JSON.stringify(value) } catch { return '[unserializable]' }
}
