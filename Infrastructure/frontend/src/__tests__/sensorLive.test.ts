import { describe, it, expect } from 'vitest';
import { parseLiveResponse } from '../utils/sensorLive';

describe('parseLiveResponse', () => {
  it('flattens live payload keys with location_cluster_sensorType', () => {
    const out = parseLiveResponse('Flower Room', 'main', {
      dry_bulb: { data: [{ value: 22.5 }] },
      rh: { data: [{ value: 55 }] },
    });
    expect(out['Flower Room_main_dry_bulb']).toBe(22.5);
    expect(out['Flower Room_main_rh']).toBe(55);
  });

  it('returns empty object for invalid input', () => {
    expect(parseLiveResponse('Veg Room', 'main', null as unknown as Record<string, never>)).toEqual({});
  });
});
