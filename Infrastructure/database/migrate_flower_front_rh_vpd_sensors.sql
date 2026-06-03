-- Register Flower Room front-cluster RH/VPD logical sensors on Node 2 (same device
-- as dry_bulb_f) so Grafana/dashboard SQL expecting rh_f + vpd_f always resolves,
-- and can-processor can bind readings once CAN mappings publish those names.
-- Safe no-op if rows exist (device_id + name unique).
-- Apply on PRIMARY only; physical replica inherits rows via WAL.

BEGIN;

INSERT INTO sensor (device_id, name, unit, data_type)
SELECT d.device_id, v.name, v.unit, v.data_type
FROM device d
CROSS JOIN (
  VALUES
    ('rh_f', '%', 'humidity'),
    ('vpd_f', 'kPa', 'pressure_deficit')
) AS v(name, unit, data_type)
WHERE d.name = 'Node 2'
ON CONFLICT (device_id, name) DO NOTHING;

COMMIT;
