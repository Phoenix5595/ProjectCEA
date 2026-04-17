import { describe, it, expect } from 'vitest';
import { parseLiveResponse } from '../utils/sensorLive';

describe('parseLiveResponse', () => {
  it('flattens live payload keys with location_cluster_sensorType', () => {
    const now = Date.UTC(2026, 0, 1, 0, 0, 10);
    const out = parseLiveResponse('Flower Room', 'main', {
      dry_bulb: { data: [{ value: 22.5, timestamp: new Date(now - 5000).toISOString() }] },
      rh: { data: [{ value: 55, timestamp: new Date(now - 5000).toISOString() }] },
    }, { nowMs: now });
    expect(out['Flower Room_main_dry_bulb']).toBe(22.5);
    expect(out['Flower Room_main_rh']).toBe(55);
  });

  it('returns empty object for invalid input', () => {
    expect(parseLiveResponse('Veg Room', 'main', null as unknown as Record<string, never>)).toEqual({});
  });

  it('drops stale datapoints older than maxAgeMs', () => {
    const now = Date.UTC(2026, 0, 1, 0, 1, 0);
    const out = parseLiveResponse(
      'Flower Room',
      'front',
      {
        dry_bulb_f: { data: [{ value: 23.1, timestamp: new Date(now - 120000).toISOString() }] },
        rh_f: { data: [{ value: 52.2, timestamp: new Date(now - 2000).toISOString() }] },
      },
      { nowMs: now, maxAgeMs: 45000 }
    );
    expect(out['Flower Room_front_dry_bulb_f']).toBeUndefined();
    expect(out['Flower Room_front_rh_f']).toBe(52.2);
  });
});
