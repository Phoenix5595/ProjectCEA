const NOW = Date.parse('2026-10-06T16:00:00.000Z')
const minute = 60_000
const timestamp = (offset: number) => new Date(NOW + offset * minute).toISOString()
const recorded = { origin: 'recorded', quality: 'exact', is_aggregated: false }

function lightDeviceNames(room: string): readonly string[] {
  return room === 'Veg Room'
    ? ['light_v_1', 'light_v_2', 'light_v_3']
    : ['light_f_1', 'light_f_2', 'light_f_3']
}

/** One absolute recorded ramp, including a genuine dip, at every requested range. */
export function lightRangeHistoryFixture(room: string, start: string, end: string) {
  return {
    lights: lightDeviceNames(room).map((name, index) => ({
      name,
      metric: name,
      provenance: recorded,
      warnings: [],
      points: [],
      steps: index === 0
        ? [
            { timestamp: start, value: 10, provenance: recorded },
            { timestamp: timestamp(-30), value: null, provenance: { ...recorded, quality: 'unavailable' } },
            { timestamp: timestamp(-1), value: 70, provenance: recorded },
          ]
        : [{ timestamp: start, value: index === 1 ? 25 : 60, provenance: recorded }],
      linear: index === 0
        ? [
            // Wide enough to exercise actual hovering at the 7d pixel scale.
            { start: timestamp(-55), end: timestamp(-35), start_value: 10, end_value: 40, provenance: recorded },
            { start: timestamp(-35), end: timestamp(-34.5), start_value: 40, end_value: 39.5, provenance: recorded },
            { start: timestamp(-34.5), end: timestamp(-30), start_value: 39.5, end_value: 70, provenance: recorded },
          ]
        : [],
    })),
    devices: [],
    pid: [],
    range: { start, end },
  }
}

/** Complete rich publication: climate and three canonical future light identities. */
export function lightRangeProjectionFixture(room: string) {
  const source = {
    mode: room === 'Veg Room' ? '1' : '2',
    submode: null,
    period: { period_id: 'light-range-active-schedule', label: 'Running schedule' },
    config_revision: 'f1c7a11',
    draft_revision: null,
  }
  const base = { trajectory_kind: 'effective', quality: 'estimated', source }
  return {
    quality: 'estimated',
    value: [],
    trajectory: {
      contract_version: 1,
      room,
      generated_at: timestamp(0),
      window: { start: timestamp(0), end: timestamp(24 * 60), timezone: 'UTC' },
      revision_scope: 'saved',
      base_config_revision: 'f1c7a11',
      draft_revision: null,
      segments: [
        { ...base, metric: 'heating', unit: 'C', shape: 'step', start: timestamp(0), end: timestamp(24 * 60), value: 22 },
        ...lightDeviceNames(room).flatMap((name, index) => {
          const metric = `light.intensity.${name}`
          if (index !== 0) return [
            { ...base, metric, unit: '%', shape: 'step', start: timestamp(0), end: timestamp(24 * 60), value: index === 1 ? 25 : 60 },
          ]
          return [
            { ...base, metric, unit: '%', shape: 'linear', start: timestamp(0), end: timestamp(60), start_value: 70, end_value: 90 },
            { ...base, metric, unit: '%', shape: 'step', start: timestamp(60), end: timestamp(120), value: 90 },
            { ...base, metric, unit: '%', shape: 'unavailable', start: timestamp(120), end: timestamp(150), quality: 'unavailable', reason: 'Missing future schedule coverage' },
            { ...base, metric, unit: '%', shape: 'step', start: timestamp(150), end: timestamp(24 * 60), value: 0 },
          ]
        }),
      ],
      assumptions: [],
      warnings: [],
    },
  }
}
