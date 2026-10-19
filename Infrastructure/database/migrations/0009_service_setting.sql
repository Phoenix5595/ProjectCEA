-- migration: downtime=false
BEGIN;

CREATE TABLE public.service_setting (
    service TEXT NOT NULL CHECK (service IN ('automation', 'soil', 'onewire', 'weather')),
    key TEXT NOT NULL,
    value JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_by TEXT NOT NULL,
    PRIMARY KEY (service, key)
);

INSERT INTO public.service_setting (service, key, value, updated_by)
VALUES
    ('automation', 'update_interval', '1'::jsonb, 'migration-0009'),
    ('automation', 'last_good_hold_period', '30'::jsonb, 'migration-0009'),
    ('automation', 'binary_hysteresis', '0.1'::jsonb, 'migration-0009'),
    (
        'automation',
        'pid_defaults',
        '{"heater":{"kp":25,"ki":0.02,"kd":0},"fan":{"kp":25,"ki":0.02,"kd":0},"co2":{"kp":10,"ki":0.01,"kd":0},"default":{"kp":10,"ki":0.01,"kd":0}}'::jsonb,
        'migration-0009'
    ),
    (
        'automation',
        'pid_limits',
        '{"heater":{"kp_min":0,"kp_max":100,"ki_min":0,"ki_max":1,"kd_min":0,"kd_max":10},"fan":{"kp_min":0,"kp_max":100,"ki_min":0,"ki_max":1,"kd_min":0,"kd_max":10},"co2":{"kp_min":0,"kp_max":50,"ki_min":0,"ki_max":0.5,"kd_min":0,"kd_max":5}}'::jsonb,
        'migration-0009'
    ),
    (
        'automation',
        'safety_limits',
        '{"max_temperature":35,"min_temperature":10,"max_humidity":90,"min_humidity":30,"max_co2":2000,"min_co2":400}'::jsonb,
        'migration-0009'
    ),
    ('soil', 'poll_interval_seconds', '5'::jsonb, 'migration-0009'),
    ('soil', 'discovery_interval_seconds', '30'::jsonb, 'migration-0009'),
    ('onewire', 'poll_interval_seconds', '1'::jsonb, 'migration-0009'),
    ('weather', 'station_icao', '"CYQB"'::jsonb, 'migration-0009'),
    ('weather', 'poll_interval_seconds', '900'::jsonb, 'migration-0009'),
    ('weather', 'api_url', '"https://aviationweather.gov/api/data/metar"'::jsonb, 'migration-0009'),
    ('weather', 'room_name', '"Outside"'::jsonb, 'migration-0009'),
    ('weather', 'device_name', '"Weather Station Québec"'::jsonb, 'migration-0009')
ON CONFLICT (service, key) DO NOTHING;

COMMIT;
