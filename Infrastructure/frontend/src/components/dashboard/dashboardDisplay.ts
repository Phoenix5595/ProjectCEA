/** Shared sensor display helpers for dashboard zone rows/cards. */

export function getRoomLightState(location: string, devices: { location: string; cluster: string; device_name?: string; state?: number }[]): string {
  const roomLights = devices.filter(
    (d) => d.location === location && d.cluster === 'main' && d.device_name?.startsWith('light_')
  );
  return roomLights.some((light) => light.state === 1) ? '☀️' : '🌙';
}

export function getSetpointColor(): string {
  return 'text-accent-setpoint';
}

export function getSensorDisplay(sensorData: Record<string, number>, key: string, unit: string, precision = 2): string {
  if (sensorData[key] == null) return `--${unit}`;
  return `${Number(sensorData[key]).toFixed(precision)}${unit}`;
}

function getSensorKey(
  sensorData: Record<string, number>,
  location: string,
  cluster: string,
  ...candidates: string[]
): number | undefined {
  for (const candidate of candidates) {
    const key = `${location}_${cluster}_${candidate}`;
    if (key in sensorData) return sensorData[key];
  }
  return undefined;
}

export function renderTemperature(location: string, cluster: string, sensorData: Record<string, number>): string {
  const candidates = ['dry_bulb_f', 'temperature_sensor', 'lab_temp'];
  const value = getSensorKey(sensorData, location, cluster, ...candidates);
  if (value == null) return '--°C';
  if (value > 100) return `${((value - 32) * 5 / 9).toFixed(2)}°C`;
  return `${Number(value).toFixed(2)}°C`;
}

export function hasClimateData(location: string, cluster: string, sensorData: Record<string, number>): boolean {
  const rhKey = `${location}_${cluster}_relative_humidity`;
  const temp = getSensorKey(sensorData, location, cluster, 'dry_bulb_f', 'temperature_sensor', 'lab_temp');
  return temp != null || sensorData[rhKey] != null;
}
