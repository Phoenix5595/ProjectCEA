-- migration: downtime=false
BEGIN;

ALTER TABLE public.sensor_registry
    ADD COLUMN sensor_uid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    ADD COLUMN identity_status TEXT NOT NULL DEFAULT 'confirmed',
    ADD COLUMN pending_candidate_registry_id BIGINT,
    ADD COLUMN pending_since TIMESTAMPTZ,
    ALTER COLUMN hardware_address DROP NOT NULL;

ALTER TABLE public.sensor_registry
    DROP CONSTRAINT sensor_registry_bus_address_unique,
    ADD CONSTRAINT sensor_registry_identity_status_check
        CHECK (identity_status IN ('confirmed', 'pending', 'detached')),
    ADD CONSTRAINT sensor_registry_pending_candidate_registry_id_fkey
        FOREIGN KEY (pending_candidate_registry_id)
        REFERENCES public.sensor_registry(registry_id) ON DELETE SET NULL,
    ADD CONSTRAINT sensor_registry_pending_since_check
        CHECK ((identity_status = 'pending') = (pending_since IS NOT NULL)),
    ADD CONSTRAINT sensor_registry_detached_address_check
        CHECK ((identity_status = 'detached') = (hardware_address IS NULL));

CREATE UNIQUE INDEX sensor_registry_bus_address_unique
    ON public.sensor_registry (bus, hardware_address)
    WHERE hardware_address IS NOT NULL;

-- Link old devices only when one exact legacy match exists and that device is
-- not already assigned to another registry row. Ambiguous or absent matches
-- remain unlinked for operator-safe handling.
WITH candidates AS (
    SELECT
        sr.registry_id,
        d.device_id,
        count(*) OVER (PARTITION BY sr.registry_id) AS registry_matches,
        count(*) OVER (PARTITION BY d.device_id) AS device_matches
    FROM public.sensor_registry AS sr
    JOIN public.device AS d
      ON sr.bus = 'rs485'
     AND d.serial_number = 'MODBUS-' || sr.hardware_address::TEXT
    WHERE sr.device_id IS NULL
), unique_matches AS (
    SELECT registry_id, device_id
    FROM candidates
    WHERE registry_matches = 1 AND device_matches = 1
)
UPDATE public.sensor_registry AS sr
SET device_id = matches.device_id
FROM unique_matches AS matches
WHERE sr.registry_id = matches.registry_id
  AND NOT EXISTS (
      SELECT 1
      FROM public.sensor_registry AS already_linked
      WHERE already_linked.device_id = matches.device_id
  );

WITH candidates AS (
    SELECT
        sr.registry_id,
        d.device_id,
        count(*) OVER (PARTITION BY sr.registry_id) AS registry_matches,
        count(*) OVER (PARTITION BY d.device_id) AS device_matches
    FROM public.sensor_registry AS sr
    JOIN public.device AS d
      ON sr.bus = 'can'
     AND d.name = 'Node ' || sr.hardware_address::TEXT
     AND d.type = 'sensor-node'
    WHERE sr.device_id IS NULL
), unique_matches AS (
    SELECT registry_id, device_id
    FROM candidates
    WHERE registry_matches = 1 AND device_matches = 1
)
UPDATE public.sensor_registry AS sr
SET device_id = matches.device_id
FROM unique_matches AS matches
WHERE sr.registry_id = matches.registry_id
  AND NOT EXISTS (
      SELECT 1
      FROM public.sensor_registry AS already_linked
      WHERE already_linked.device_id = matches.device_id
  );

CREATE TABLE public.sensor_address_history (
    registry_id BIGINT NOT NULL
        REFERENCES public.sensor_registry(registry_id) ON DELETE CASCADE,
    bus TEXT NOT NULL,
    hardware_address INTEGER NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    PRIMARY KEY (registry_id, valid_from)
);

INSERT INTO public.sensor_address_history (
    registry_id, bus, hardware_address, valid_from
)
SELECT registry_id, bus, hardware_address, first_seen
FROM public.sensor_registry
WHERE hardware_address IS NOT NULL;

DO $migration_notice$
DECLARE
    unmatched RECORD;
BEGIN
    FOR unmatched IN
        SELECT registry_id, bus, hardware_address
        FROM public.sensor_registry
        WHERE device_id IS NULL
        ORDER BY bus, hardware_address
    LOOP
        RAISE NOTICE 'sensor_identity_unlinked registry_id=% bus=% hardware_address=%',
            unmatched.registry_id, unmatched.bus, unmatched.hardware_address;
    END LOOP;
END;
$migration_notice$;

COMMIT;
