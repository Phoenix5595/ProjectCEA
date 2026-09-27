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

/**
 * Live sensor-type names vary by backend registry: Flower streams carry the
 * cluster suffix (`dry_bulb_f`/`rh_f`/`co2_f`/`vpd_f` for front, `_b` for
 * back), older shapes use unsuffixed names. Look up every plausible key.
 */
const CLIMATE_KEY_CANDIDATES: Record<'temp' | 'rh' | 'co2' | 'vpd', string[]> = {
  temp: ['dry_bulb_f', 'dry_bulb_b', 'dry_bulb', 'temperature_sensor', 'lab_temp'],
  rh: ['rh_f', 'rh_b', 'relative_humidity', 'rh'],
  co2: ['co2_f', 'co2_b', 'co2'],
  vpd: ['vpd_f', 'vpd_b', 'vpd'],
};

function getClimateKey(
  sensorData: Record<string, number>,
  location: string,
  cluster: string,
  metric: 'temp' | 'rh' | 'co2' | 'vpd',
): number | undefined {
  return getSensorKey(sensorData, location, cluster, ...CLIMATE_KEY_CANDIDATES[metric]);
}

export function getClimateDisplay(
  sensorData: Record<string, number>,
  location: string,
  cluster: string,
  metric: 'rh' | 'co2' | 'vpd',
  unit: string,
  precision = 2,
): string {
  const value = getClimateKey(sensorData, location, cluster, metric);
  if (value == null) return `--${unit}`;
  return `${Number(value).toFixed(precision)}${unit}`;
}

export function renderTemperature(location: string, cluster: string, sensorData: Record<string, number>): string {
  const value = getClimateKey(sensorData, location, cluster, 'temp');
  if (value == null) return '--°C';
  if (value > 100) return `${((value - 32) * 5 / 9).toFixed(2)}°C`;
  return `${Number(value).toFixed(2)}°C`;
}

export function hasClimateData(location: string, cluster: string, sensorData: Record<string, number>): boolean {
  return (
    getClimateKey(sensorData, location, cluster, 'temp') != null ||
    getClimateKey(sensorData, location, cluster, 'rh') != null
  );
}
