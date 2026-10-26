BEGIN;

-- Veg room-level RS-485 rows are illegal under the pre-0013 check.
UPDATE public.sensor_registry
SET room_id = NULL, updated_at = clock_timestamp()
WHERE bus = 'rs485' AND rack_id IS NULL AND room_id IS NOT NULL;

ALTER TABLE public.sensor_registry
    DROP CONSTRAINT sensor_registry_legal_state,
    ADD CONSTRAINT sensor_registry_legal_state CHECK (
        (room_id IS NULL AND rack_id IS NULL AND location_in_room IS NULL)
        OR (bus = 'can' AND room_id IS NOT NULL AND location_in_room IS NOT NULL AND rack_id IS NULL)
        OR (bus = 'rs485' AND rack_id IS NOT NULL AND room_id IS NULL AND location_in_room IS NULL)
    );

DROP TABLE public.soil_probe_placement;

ALTER TABLE public.soil_bed_profile
    DROP COLUMN width_ft,
    DROP COLUMN length_ft;

COMMIT;
