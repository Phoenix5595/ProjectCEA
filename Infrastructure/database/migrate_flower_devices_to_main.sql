-- Migrate Flower Room control-plane cluster from legacy front/back to canonical `main`.
-- Equipment, schedules, modes, and DB state must use `main`; CAN/ingestion may still tag
-- telemetry as (Flower Room, front) or (Flower Room, back) for sensor suffixes only.
--
-- Run after backup. Order matters: delete `front` slices first, then rename `back` -> `main`.
-- If you already have rows for (Flower Room, main), resolve duplicates before running.

BEGIN;

-- Climate & schedules
DELETE FROM climate_periods WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE climate_periods SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

DELETE FROM schedules WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE schedules SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

-- Device state & hardware mapping
DELETE FROM device_states WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE device_states SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

DELETE FROM device_mappings WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE device_mappings SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

-- Active mode / parameters (one row per location+cluster for room_active_mode)
DELETE FROM room_active_mode WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE room_active_mode SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

DELETE FROM mode_parameters WHERE location = 'Flower Room' AND cluster = 'front';
UPDATE mode_parameters SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

-- Notes (if table exists in your deployment)
-- DELETE FROM notes WHERE location = 'Flower Room' AND cluster = 'front';
-- UPDATE notes SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';

-- Time-series / history (optional but keeps Grafana/API consistent)
UPDATE effective_setpoints SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';
DELETE FROM effective_setpoints WHERE location = 'Flower Room' AND cluster = 'front';

UPDATE automation_state SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';
DELETE FROM automation_state WHERE location = 'Flower Room' AND cluster = 'front';

UPDATE control_history SET cluster = 'main' WHERE location = 'Flower Room' AND cluster = 'back';
DELETE FROM control_history WHERE location = 'Flower Room' AND cluster = 'front';

COMMIT;
