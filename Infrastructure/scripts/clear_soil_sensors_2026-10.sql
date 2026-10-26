-- One-time removal of the two pre-DFR0845 RS-485 soil identities (R9 cutover).
--
-- Targets exactly: sensor_registry 4 (rs485, address 2, device 6) and 5
-- (rs485, address 1, device 3), their address history and placements,
-- devices 3 and 6 and their 12 sensors (14-17, 30-37), which hold no
-- measurements. Every guard runs inside one transaction; any mismatch raises
-- and rolls back everything. A second run aborts because the rows are gone.
--
-- Owner-authorised (2026-10-24, re-confirmed 2026-10-26) for one run on
-- sj_cea right after the Plan 3 deploy. Usage:
--   psql -X -v ON_ERROR_STOP=1 -f clear_soil_sensors_2026-10.sql
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL statement_timeout = '30s';
SET LOCAL lock_timeout = '5s';

\echo 'Target sensor_registry rows'
SELECT registry_id, bus, hardware_address, device_id, room_id, rack_id, identity_status, last_seen
FROM sensor_registry WHERE registry_id IN (4, 5) ORDER BY registry_id;

\echo 'Target sensor_address_history rows'
SELECT registry_id, bus, hardware_address, valid_from, valid_to
FROM sensor_address_history WHERE registry_id IN (4, 5) ORDER BY registry_id, valid_from;

\echo 'Target soil_probe_placement rows'
SELECT placement_id, registry_id, room_id, rack_id, x_frac, y_frac, placed_at, removed_at, closed_reason
FROM soil_probe_placement WHERE registry_id IN (4, 5) ORDER BY placement_id;

\echo 'Target devices'
SELECT device_id, rack_id, name, type, serial_number FROM device WHERE device_id IN (3, 6) ORDER BY device_id;

\echo 'Target sensors and measurement counts'
SELECT s.sensor_id, s.device_id, s.name,
       (SELECT count(*) FROM measurement m WHERE m.sensor_id = s.sensor_id) AS measurements
FROM sensor s WHERE s.device_id IN (3, 6) ORDER BY s.sensor_id;

DO $clear_soil$
DECLARE
    v_expected_sensors CONSTANT integer[] := ARRAY[14, 15, 16, 17, 30, 31, 32, 33, 34, 35, 36, 37];
    v_sensors integer[];
    v_count bigint;
    v_rows bigint;
    v_history bigint;
    v_placements bigint;
BEGIN
    -- Lock the target rows so nothing changes between the guards and the deletes.
    PERFORM 1 FROM sensor_registry WHERE registry_id IN (4, 5) FOR UPDATE;
    PERFORM 1 FROM device WHERE device_id IN (3, 6) FOR UPDATE;

    SELECT count(*) INTO v_count FROM sensor_registry
    WHERE (registry_id = 4 AND bus = 'rs485' AND hardware_address = 2 AND device_id = 6)
       OR (registry_id = 5 AND bus = 'rs485' AND hardware_address = 1 AND device_id = 3);
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'guard: registry 4/5 are not (rs485, 2, device 6) and (rs485, 1, device 3)';
    END IF;

    SELECT count(*) INTO v_count FROM sensor_registry
    WHERE device_id IN (3, 6) AND registry_id NOT IN (4, 5);
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'guard: another registry row references devices 3/6';
    END IF;

    SELECT count(*) INTO v_count FROM device
    WHERE device_id IN (3, 6) AND type = 'RS485 Soil Sensor';
    IF v_count <> 2 THEN
        RAISE EXCEPTION 'guard: devices 3/6 are not both RS485 Soil Sensor devices';
    END IF;

    SELECT coalesce(array_agg(sensor_id ORDER BY sensor_id), ARRAY[]::integer[]) INTO v_sensors
    FROM sensor WHERE device_id IN (3, 6);
    IF v_sensors <> v_expected_sensors THEN
        RAISE EXCEPTION 'guard: devices 3/6 own sensors %, expected %', v_sensors, v_expected_sensors;
    END IF;

    SELECT count(*) INTO v_count FROM measurement WHERE sensor_id = ANY (v_expected_sensors);
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'guard: % measurement rows exist for the target sensors', v_count;
    END IF;

    SELECT count(*) INTO v_history FROM sensor_address_history WHERE registry_id IN (4, 5);
    SELECT count(*) INTO v_count FROM sensor_address_history
    WHERE registry_id IN (4, 5) AND bus = 'rs485';
    IF v_history <> 2 OR v_count <> 2 THEN
        RAISE EXCEPTION 'guard: expected exactly 2 rs485 address history rows, found % (% rs485)',
            v_history, v_count;
    END IF;

    SELECT count(*) INTO v_count FROM water_tank_monitor_config
    WHERE sensor_id = ANY (v_expected_sensors);
    IF v_count <> 0 THEN
        RAISE EXCEPTION 'guard: water_tank_monitor_config references the target sensors';
    END IF;

    SELECT count(*) INTO v_placements FROM soil_probe_placement WHERE registry_id IN (4, 5);

    DELETE FROM soil_probe_placement WHERE registry_id IN (4, 5);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> v_placements THEN
        RAISE EXCEPTION 'delete: removed % placements, expected %', v_rows, v_placements;
    END IF;

    DELETE FROM sensor_address_history WHERE registry_id IN (4, 5);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 2 THEN
        RAISE EXCEPTION 'delete: removed % address history rows, expected 2', v_rows;
    END IF;

    DELETE FROM sensor_registry WHERE registry_id IN (4, 5);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 2 THEN
        RAISE EXCEPTION 'delete: removed % registry rows, expected 2', v_rows;
    END IF;

    DELETE FROM sensor WHERE sensor_id = ANY (v_expected_sensors);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 12 THEN
        RAISE EXCEPTION 'delete: removed % sensors, expected 12', v_rows;
    END IF;

    DELETE FROM device WHERE device_id IN (3, 6);
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows <> 2 THEN
        RAISE EXCEPTION 'delete: removed % devices, expected 2', v_rows;
    END IF;
END
$clear_soil$;

\echo 'Remaining target rows (all 0)'
SELECT
    (SELECT count(*) FROM sensor_registry WHERE registry_id IN (4, 5)) AS registry,
    (SELECT count(*) FROM sensor_address_history WHERE registry_id IN (4, 5)) AS address_history,
    (SELECT count(*) FROM soil_probe_placement WHERE registry_id IN (4, 5)) AS placements,
    (SELECT count(*) FROM device WHERE device_id IN (3, 6)) AS devices,
    (SELECT count(*) FROM sensor
     WHERE sensor_id IN (14, 15, 16, 17, 30, 31, 32, 33, 34, 35, 36, 37)) AS sensors;

COMMIT;
