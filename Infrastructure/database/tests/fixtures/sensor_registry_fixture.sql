\set ON_ERROR_STOP on

-- Disposable fixture for the sensor_registry migration tests.
-- Guard: must run inside the guarded monitoring_test_ namespace.

DO $guard$
BEGIN
    IF current_database() IN ('cea_sensors', 'cea_sensors_test')
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'sensor registry fixtures require the monitoring_test_ database namespace';
    END IF;
END
$guard$;

-- Production-shaped metadata hierarchy (fresh-install subset of cea_schema.sql).
CREATE TABLE room (
    room_id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    target_vpd REAL,
    target_temp REAL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE rack (
    rack_id SERIAL PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES room(room_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(room_id, name)
);

CREATE TABLE device (
    device_id SERIAL PRIMARY KEY,
    rack_id INTEGER REFERENCES rack(rack_id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    ip_address TEXT,
    serial_number TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE sensor (
    sensor_id SERIAL PRIMARY KEY,
    device_id INTEGER NOT NULL REFERENCES device(device_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    data_type TEXT NOT NULL,
    channel INTEGER CHECK (channel IS NULL OR (channel >= 0 AND channel <= 15)),
    calibration_offset REAL DEFAULT 0.0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(device_id, name)
);

CREATE TABLE measurement (
    time TIMESTAMPTZ NOT NULL,
    sensor_id INTEGER NOT NULL REFERENCES sensor(sensor_id) ON DELETE CASCADE,
    value REAL NOT NULL,
    status TEXT DEFAULT 'ok',
    PRIMARY KEY (time, sensor_id)
);

-- Legacy deployment state mirrored from production:
--   CAN nodes 1-3 exist as `Node N` devices.
--   RS-485 probes carry MODBUS-<id> serials; five were parked on
--   Front Bed (more than the four-slot bed capacity) and two sit on
--   Back Bed / a non-Flower rack to exercise preservation + overflow.
INSERT INTO room (room_id, name) VALUES
    (1, 'Flower Room'),
    (2, 'Veg Room');

SELECT setval('room_room_id_seq', 2);

INSERT INTO rack (rack_id, room_id, name) VALUES
    (1, 1, 'Front Bed'),
    (2, 1, 'Back Bed'),
    (3, 2, 'Veg Bench');

SELECT setval('rack_rack_id_seq', 3);

INSERT INTO device (device_id, rack_id, name, type, serial_number, created_at) VALUES
    (1, 2, 'Node 1', 'sensor-node', NULL, '2025-05-01T00:00:00Z'),
    (2, 1, 'Node 2', 'sensor-node', NULL, '2025-05-01T00:00:00Z'),
    (3, 3, 'Node 3', 'sensor-node', NULL, '2025-05-01T00:00:00Z'),
    (4, 1, 'Soil Sensor - Front Bed', 'RS485 Soil Sensor', 'MODBUS-226', '2025-06-01T00:00:00Z'),
    (5, 2, 'Soil Sensor - Back Bed', 'RS485 Soil Sensor', 'MODBUS-227', '2025-06-02T00:00:00Z'),
    (6, 1, 'Soil Sensor - Front Bed', 'RS485 Soil Sensor', 'MODBUS-228', '2025-06-03T00:00:00Z'),
    (7, 1, 'Soil Sensor - Front Bed', 'RS485 Soil Sensor', 'MODBUS-229', '2025-06-04T00:00:00Z'),
    (8, 1, 'Soil Sensor - Front Bed', 'RS485 Soil Sensor', 'MODBUS-230', '2025-06-05T00:00:00Z'),
    (9, 1, 'Soil Sensor - Front Bed', 'RS485 Soil Sensor', 'MODBUS-231', '2025-06-06T00:00:00Z'),
    (10, 3, 'Soil Sensor - Veg Bench', 'RS485 Soil Sensor', 'MODBUS-300', '2025-06-07T00:00:00Z');

SELECT setval('device_device_id_seq', 10);
