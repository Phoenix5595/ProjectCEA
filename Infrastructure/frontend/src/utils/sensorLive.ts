/**
 * Parse live sensor API response into flat keys `${location}_${cluster}_${sensorType}` → value.
 */
export function parseLiveResponse(
  location: string,
  cluster: string,
  liveData: Record<string, { data?: Array<{ value?: number }> }>
): Record<string, number> {
  const flat: Record<string, number> = {};
  if (!liveData || typeof liveData !== 'object') return flat;
  for (const [sensorType, resp] of Object.entries(liveData)) {
    const dp = Array.isArray(resp?.data) && resp.data.length > 0 ? resp.data[0] : null;
    if (dp?.value != null) flat[`${location}_${cluster}_${sensorType}`] = Number(dp.value);
  }
  return flat;
}
