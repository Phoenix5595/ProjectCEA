\set ON_ERROR_STOP on

-- Assertions for migrate_sensor_registry.sql.
-- The harness applies the migration twice (idempotence) BEFORE running
-- this file, so every seed row must already be present exactly once and
-- re-running must have changed nothing observable.

DO $guard$
BEGIN
    IF current_database() IN ('cea_sensors', 'cea_sensors_test')
       OR current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
        RAISE EXCEPTION 'sensor registry cases require the monitoring_test_ database namespace';
    END IF;
END
$guard$;

-- ------------------------------------------------------------------
-- Helper: assert one SQL statement raises a specific SQLSTATE.
-- ------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE assert_sqlstate(expected TEXT, action TEXT) LANGUAGE plpgsql AS $$
DECLARE
    caught TEXT := NULL;
BEGIN
    BEGIN
        EXECUTE action;
    EXCEPTION WHEN OTHERS THEN
        caught := SQLSTATE;
    END;
    IF caught IS NULL THEN
        RAISE EXCEPTION 'expected SQLSTATE % but statement succeeded: %', expected, action;
    END IF;
    IF caught <> expected THEN
        RAISE EXCEPTION 'expected SQLSTATE %, got %: %', expected, caught, action;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 1. Idempotence: registry holds exactly the seeded rows after two runs.
-- ------------------------------------------------------------------
DO $$
DECLARE
    can_rows INTEGER;
    rs485_rows INTEGER;
BEGIN
    SELECT count(*) INTO can_rows FROM sensor_registry WHERE bus = 'can';
    SELECT count(*) INTO rs485_rows FROM sensor_registry WHERE bus = 'rs485';
    IF can_rows <> 3 THEN
        RAISE EXCEPTION 'expected 3 seeded CAN rows, found %', can_rows;
    END IF;
    IF rs485_rows <> 7 THEN
        RAISE EXCEPTION 'expected 7 seeded RS-485 rows, found %', rs485_rows;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 2. Unique (bus, hardware_address).
-- ------------------------------------------------------------------
CALL assert_sqlstate('23505', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('can', 1, 'duplicate node')
$stmt$);

CALL assert_sqlstate('23505', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('rs485', 226, 'duplicate probe')
$stmt$);

-- ------------------------------------------------------------------
-- 3. Positive hardware address only.
-- ------------------------------------------------------------------
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('can', 0, 'zero address')
$stmt$);

CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('rs485', -4, 'negative probe')
$stmt$);

-- ------------------------------------------------------------------
-- 4. Non-empty display_name.
-- ------------------------------------------------------------------
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('can', 77, '   ')
$stmt$);

-- ------------------------------------------------------------------
-- 5. Legal assignment shapes only.
-- ------------------------------------------------------------------
-- Unassigned row is legal.
INSERT INTO sensor_registry (bus, hardware_address, display_name)
VALUES ('can', 42, 'Node 42');

-- Assigned CAN (room + location, no rack) is legal.
INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id, location_in_room)
VALUES ('can', 43, 'Node 43',
    (SELECT room_id FROM room WHERE name = 'Veg Room'), 'front');

-- Assigned RS-485 (rack only) is legal.
INSERT INTO sensor_registry (bus, hardware_address, display_name, rack_id)
VALUES ('rs485', 232, 'probe 232',
    (SELECT rack_id FROM rack WHERE room_id = (SELECT room_id FROM room WHERE name = 'Flower Room') AND name = 'Back Bed'));

-- Illegal: assigned CAN with a rack.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id, rack_id, location_in_room)
    VALUES ('can', 50, 'bad can', (SELECT room_id FROM room WHERE name = 'Flower Room'),
        (SELECT rack_id FROM rack WHERE name = 'Front Bed'), 'front')
$stmt$);

-- Illegal: assigned CAN missing location.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id)
    VALUES ('can', 51, 'bad can', (SELECT room_id FROM room WHERE name = 'Flower Room'))
$stmt$);

-- Illegal: assigned RS-485 with room/location instead of rack-only.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id, rack_id, location_in_room)
    VALUES ('rs485', 52, 'bad rs485', (SELECT room_id FROM room WHERE name = 'Flower Room'),
        (SELECT rack_id FROM rack WHERE name = 'Front Bed'), 'front')
$stmt$);

-- Illegal: assigned RS-485 without a rack.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, location_in_room)
    VALUES ('rs485', 53, 'bad rs485', 'front')
$stmt$);

-- Illegal: unknown bus value.
CALL assert_sqlstate('23514', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name)
    VALUES ('canbus', 54, 'bad bus')
$stmt$);

-- ------------------------------------------------------------------
-- 6. CAN room positions hold at most one node (partial unique index).
-- ------------------------------------------------------------------
CALL assert_sqlstate('23505', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id, location_in_room)
    VALUES ('can', 55, 'Node 55',
        (SELECT room_id FROM room WHERE name = 'Flower Room'), 'back')
$stmt$);

-- A different (room, position) pair is still available.
INSERT INTO sensor_registry (bus, hardware_address, display_name, room_id, location_in_room)
VALUES ('can', 56, 'Node 56',
    (SELECT room_id FROM room WHERE name = 'Flower Room'), 'main');

-- Unassigned rows reserve no position: 42 stays unassigned while 43 was
-- assigned above.

-- ------------------------------------------------------------------
-- 7. device_id links at most one registry row.
-- ------------------------------------------------------------------
CALL assert_sqlstate('23505', $stmt$
    INSERT INTO sensor_registry (bus, hardware_address, display_name, device_id)
    VALUES ('can', 58, 'Node 58', (SELECT device_id FROM device WHERE name = 'Node 1'))
$stmt$);

-- ------------------------------------------------------------------
-- 8. Canonical Flower room + the two beds exist exactly once.
-- ------------------------------------------------------------------
DO $$
DECLARE
    flower_count INTEGER;
    bed_count INTEGER;
BEGIN
    SELECT count(*) INTO flower_count FROM room WHERE name = 'Flower Room';
    IF flower_count <> 1 THEN
        RAISE EXCEPTION 'expected exactly one Flower Room, found %', flower_count;
    END IF;

    SELECT count(*) INTO bed_count
    FROM rack r JOIN room f ON f.room_id = r.room_id
    WHERE f.name = 'Flower Room' AND r.name IN ('Front Bed', 'Back Bed');
    IF bed_count <> 2 THEN
        RAISE EXCEPTION 'expected exactly two Flower beds, found %', bed_count;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 9. Legacy CAN seed preserved (1 Flower/back, 2 Flower/front, 3 Veg/main).
-- ------------------------------------------------------------------
DO $$
DECLARE
    mismatched INTEGER;
BEGIN
    SELECT count(*) INTO mismatched
    FROM sensor_registry sr
    JOIN device d ON d.device_id = sr.device_id
    JOIN room r ON r.room_id = sr.room_id
    WHERE sr.bus = 'can'
      AND NOT (
            (sr.hardware_address = 1 AND d.name = 'Node 1' AND r.name = 'Flower Room' AND sr.location_in_room = 'back')
         OR (sr.hardware_address = 2 AND d.name = 'Node 2' AND r.name = 'Flower Room' AND sr.location_in_room = 'front')
         OR (sr.hardware_address = 3 AND d.name = 'Node 3' AND r.name = 'Veg Room' AND sr.location_in_room = 'main')
      );
    IF mismatched <> 0 THEN
        RAISE EXCEPTION 'legacy CAN seed deviates from hard-coded mappings: % rows', mismatched;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 10. Legacy RS-485 seed preserved with deterministic overflow.
-- ------------------------------------------------------------------
DO $$
DECLARE
    mismatches INTEGER;
    front_assigned INTEGER;
    back_assigned INTEGER;
BEGIN
    SELECT count(*) INTO mismatches
    FROM sensor_registry sr
    JOIN rack bk ON bk.rack_id = sr.rack_id
    WHERE sr.bus = 'rs485'
      AND sr.hardware_address IN (226, 227, 228, 229, 230, 231, 300)
      AND NOT (
            -- Front Bed keeps the four oldest legacy probes.
            (sr.hardware_address IN (226, 228, 229, 230) AND bk.name = 'Front Bed')
            -- Back Bed keeps its single legacy probe.
         OR (sr.hardware_address = 227 AND bk.name = 'Back Bed')
      );
    IF mismatches <> 0 THEN
        RAISE EXCEPTION 'legacy RS-485 seed placement wrong: % rows', mismatches;
    END IF;

    -- The fifth Front Bed probe and the non-Flower probe stay unassigned.
    SELECT count(*) INTO mismatches
    FROM sensor_registry
    WHERE bus = 'rs485'
      AND hardware_address IN (231, 300)
      AND (rack_id IS NOT NULL OR room_id IS NOT NULL OR location_in_room IS NOT NULL);
    IF mismatches <> 0 THEN
        RAISE EXCEPTION 'overflow/non-Flower probes must stay unassigned';
    END IF;

    -- The legacy device rack link is preserved for assigned probes.
    SELECT count(*) INTO mismatches
    FROM sensor_registry sr
    JOIN device d ON d.device_id = sr.device_id
    LEFT JOIN rack dr ON dr.rack_id = d.rack_id
    WHERE sr.bus = 'rs485'
      AND sr.rack_id IS NOT NULL
      AND d.rack_id IS DISTINCT FROM sr.rack_id;
    IF mismatches <> 0 THEN
        RAISE EXCEPTION 'seeded probes must preserve their device rack link: % rows', mismatches;
    END IF;

    -- Per-bed capacity after seeding.
    SELECT count(*) INTO front_assigned
    FROM sensor_registry sr JOIN rack bk ON bk.rack_id = sr.rack_id
    WHERE sr.bus = 'rs485' AND bk.name = 'Front Bed';
    SELECT count(*) INTO back_assigned
    FROM sensor_registry sr JOIN rack bk ON bk.rack_id = sr.rack_id
    WHERE sr.bus = 'rs485' AND bk.name = 'Back Bed';
    IF front_assigned > 4 OR back_assigned > 4 THEN
        RAISE EXCEPTION 'seed violated bed capacity: front=% back=%', front_assigned, back_assigned;
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 11. Assignment updates keep the legal shape and stamp updated_at.
-- ------------------------------------------------------------------
UPDATE sensor_registry
SET rack_id = (SELECT rack_id FROM rack WHERE name = 'Back Bed'),
    updated_at = NOW()
WHERE bus = 'rs485' AND hardware_address = 231;

DO $$
DECLARE
    updated_rows INTEGER;
BEGIN
    SELECT count(*) INTO updated_rows
    FROM sensor_registry
    WHERE bus = 'rs485' AND hardware_address = 231
      AND rack_id = (SELECT rack_id FROM rack WHERE name = 'Back Bed')
      AND room_id IS NULL AND location_in_room IS NULL
      AND updated_at IS NOT NULL;
    IF updated_rows <> 1 THEN
        RAISE EXCEPTION 'reassignment to Back Bed failed';
    END IF;
END;
$$;

-- Restore the seeded state for any future re-run of this case.
DELETE FROM sensor_registry WHERE bus = 'rs485' AND hardware_address = 231;
INSERT INTO sensor_registry (bus, hardware_address, device_id, display_name, first_seen)
SELECT 'rs485', 231, d.device_id, d.name, d.created_at
FROM device d
WHERE d.serial_number = 'MODBUS-231';

-- ------------------------------------------------------------------
-- 12. Deleting a linked device keeps the registry row and clears the link.
-- ------------------------------------------------------------------
INSERT INTO device (name, type) VALUES ('Scratch Device', 'test');
INSERT INTO sensor_registry (bus, hardware_address, display_name, device_id)
VALUES ('can', 59, 'Node 59', (SELECT device_id FROM device WHERE name = 'Scratch Device'));
DELETE FROM device WHERE name = 'Scratch Device';

DO $$
DECLARE
    surviving INTEGER;
BEGIN
    SELECT count(*) INTO surviving
    FROM sensor_registry
    WHERE bus = 'can' AND hardware_address = 59 AND device_id IS NULL;
    IF surviving <> 1 THEN
        RAISE EXCEPTION 'device deletion must leave the registry row with a cleared link';
    END IF;
END;
$$;

-- ------------------------------------------------------------------
-- 13. Foreign keys: room deletion restricted while a registry row points at it.
-- ------------------------------------------------------------------
CALL assert_sqlstate('23503', $stmt$
    DELETE FROM room WHERE name = 'Flower Room'
$stmt$);

-- Remove scratch rows so the case is re-runnable.
DELETE FROM sensor_registry WHERE hardware_address IN (42, 43, 56, 59);
