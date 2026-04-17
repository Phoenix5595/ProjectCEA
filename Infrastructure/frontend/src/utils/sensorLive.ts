/**
 * Parse live sensor API response into flat keys `${location}_${cluster}_${sensorType}` → value.
 */
export function parseLiveResponse(
  location: string,
  cluster: string,
  liveData: Record<string, { data?: Array<{ value?: number; timestamp?: string | number | Date }> }>,
  options?: { maxAgeMs?: number; nowMs?: number }
): Record<string, number> {
  const flat: Record<string, number> = {};
  const maxAgeMs = options?.maxAgeMs ?? 45000;
  const nowMs = options?.nowMs ?? Date.now();
  if (!liveData || typeof liveData !== 'object') return flat;
  for (const [sensorType, resp] of Object.entries(liveData)) {
    const dp = Array.isArray(resp?.data) && resp.data.length > 0 ? resp.data[0] : null;
    if (dp?.value == null) continue;
    if (dp.timestamp != null) {
      const ts = dp.timestamp instanceof Date ? dp.timestamp.getTime() : new Date(dp.timestamp).getTime();
      if (Number.isFinite(ts) && nowMs - ts > maxAgeMs) {
        continue;
      }
    }
    flat[`${location}_${cluster}_${sensorType}`] = Number(dp.value);
  }
  return flat;
}
