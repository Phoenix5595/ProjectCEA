const recorded = (quality = 'exact', is_aggregated = false) => ({
  origin: 'recorded',
  quality,
  is_aggregated,
})

function lightNames(room: string): [string, string, string] {
  return room === 'Veg Room'
    ? ['light_v_1', 'light_v_2', 'light_v_3']
    : ['light_f_1', 'light_f_2', 'light_f_3']
}

function at(start: string, end: string, fraction: number): string {
  const startMs = new Date(start).getTime()
  const endMs = new Date(end).getTime()
  return new Date(startMs + Math.round((endMs - startMs) * fraction)).toISOString()
}

function rawLight(name: string, start: string, end: string, value: number) {
  return {
    name,
    metric: name,
    provenance: recorded(),
    warnings: [],
    points: [
      { timestamp: start, value, nominal_value: value, device_name: name, mode: 'DAY', provenance: recorded() },
      { timestamp: at(start, end, 0.5), value: value + 10, nominal_value: value + 10, device_name: name, mode: 'NIGHT', provenance: recorded() },
      { timestamp: at(start, end, 0.9), value: value + 5, nominal_value: value + 5, device_name: name, mode: 'DAY', provenance: recorded() },
    ],
    steps: [],
    linear: [],
  }
}

function deviceState(deviceName: string, timestamp: string, state: number) {
  return {
    name: deviceName,
    provenance: recorded('exact', true),
    warnings: [],
    points: [
      {
        timestamp,
        provenance: recorded('exact', true),
        device_name: deviceName,
        device_state: state,
        device_mode: 'auto',
        control_reason: 'schedule',
      },
    ],
  }
}

function pidState(deviceName: string, timestamp: string, pidOutput: number, duty: number) {
  return {
    name: deviceName,
    provenance: recorded('exact', true),
    warnings: [],
    points: [
      {
        timestamp,
        provenance: recorded('exact', true),
        device_name: deviceName,
        pid_output: pidOutput,
        duty_cycle_percent: duty,
      },
    ],
  }
}

/** Backend-shaped equipment sections: one budgeted timeline and two raw histories. */
export function equipmentHistoryFixture(room: string, start: string, end: string) {
  const [budgeted, rawOne, rawTwo] = lightNames(room)
  const timestamp = at(start, end, 0.9)
  const heater = room === 'Veg Room' ? 'Heater Veg' : 'Heater Flower'
  return {
    lights: [
      {
        name: budgeted,
        metric: budgeted,
        provenance: recorded(),
        warnings: [],
        points: [],
        steps: [
          { timestamp: start, value: 40, provenance: recorded() },
          { timestamp: at(start, end, 0.18), value: 0, provenance: recorded() },
          { timestamp: at(start, end, 0.40), value: 10, provenance: recorded() },
          { timestamp: at(start, end, 0.72), value: null, provenance: recorded('unavailable') },
          { timestamp: at(start, end, 0.86), value: 0, provenance: recorded() },
        ],
        linear: [
          {
            start: at(start, end, 0.42),
            end: at(start, end, 0.52),
            start_value: 40,
            end_value: 80,
            provenance: recorded(),
          },
        ],
      },
      rawLight(rawOne, start, end, 25),
      rawLight(rawTwo, start, end, 55),
    ],
    devices: [
      ...lightNames(room).map(name => deviceState(name, timestamp, 1)),
      deviceState(heater, timestamp, 1),
    ],
    pid: [
      ...lightNames(room).map(name => pidState(name, timestamp, 15, 40)),
      pidState(heater, timestamp, 18, 22.5),
    ],
  }
}

function segmentSource(mode: string, periodId: string) {
  return {
    mode,
    submode: null,
    period: { period_id: periodId, label: mode === 'NIGHT' ? 'Night cycle' : 'Day cycle' },
    config_revision: 'f1c7a11',
    draft_revision: null,
  }
}

/** Rich projection keeps the selected light's step/ramp/gap boundaries explicit. */
export function equipmentProjectionFixture(room: string, start: string, end: string) {
  const [primary, second, third] = lightNames(room)
  const rampStart = at(start, end, 0.02)
  const rampEnd = at(start, end, 0.04)
  const gapStart = at(start, end, 0.06)
  const offStart = at(start, end, 0.08)
  const mode = 'DAY'
  const source = segmentSource(mode, 'equipment-day')
  const segments: Array<Record<string, unknown>> = [
    {
      shape: 'step', start, end, metric: 'heating_setpoint', unit: 'C',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 22,
    },
    {
      shape: 'step', start, end: rampStart, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 40,
    },
    {
      shape: 'linear', start: rampStart, end: rampEnd, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, start_value: 40, end_value: 80,
    },
    {
      shape: 'step', start: rampEnd, end: gapStart, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 80,
    },
    {
      shape: 'unavailable', start: gapStart, end: offStart, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'effective', quality: 'unavailable', source, reason: 'fixture gap',
    },
    {
      shape: 'step', start: offStart, end, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 0,
    },
    {
      shape: 'step', start, end, metric: `light.intensity.${primary}`, unit: '%',
      trajectory_kind: 'scheduled', quality: 'estimated', source, value: 95,
    },
    {
      shape: 'step', start, end, metric: `light.intensity.${second}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 35,
    },
    {
      shape: 'step', start, end, metric: `light.intensity.${third}`, unit: '%',
      trajectory_kind: 'effective', quality: 'estimated', source, value: 65,
    },
  ]
  return {
    quality: 'estimated',
    value: [],
    trajectory: {
      contract_version: 1,
      room,
      generated_at: start,
      window: { start, end, timezone: 'UTC' },
      revision_scope: 'saved',
      base_config_revision: 'f1c7a11',
      draft_revision: null,
      segments,
      assumptions: [],
      warnings: [],
    },
  }
}
