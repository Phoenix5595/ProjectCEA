BEGIN;

DO $rollback_guard$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM public.sensor_registry AS detached
        WHERE detached.identity_status = 'detached'
          AND NOT EXISTS (
              SELECT 1
              FROM public.sensor_address_history AS history
              WHERE history.registry_id = detached.registry_id
                AND history.bus = detached.bus
                AND history.valid_to IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM public.sensor_registry AS current_address
                    WHERE current_address.registry_id <> detached.registry_id
                      AND current_address.bus = history.bus
                      AND current_address.hardware_address = history.hardware_address
                )
                AND (
                    SELECT count(DISTINCT owner.registry_id)
                    FROM public.sensor_address_history AS owner
                    WHERE owner.bus = history.bus
                      AND owner.hardware_address = history.hardware_address
                ) = 1
          )
    ) THEN
        RAISE EXCEPTION 'sensor_identity_downgrade_address_conflict';
    END IF;
END;
$rollback_guard$;

WITH available_addresses AS (
    SELECT
        detached.registry_id,
        history.hardware_address,
        row_number() OVER (
            PARTITION BY detached.registry_id
            ORDER BY history.valid_from DESC
        ) AS address_rank
    FROM public.sensor_registry AS detached
    JOIN public.sensor_address_history AS history
      ON history.registry_id = detached.registry_id
     AND history.bus = detached.bus
    WHERE detached.identity_status = 'detached'
      AND history.valid_to IS NOT NULL
      AND NOT EXISTS (
          SELECT 1
          FROM public.sensor_registry AS current_address
          WHERE current_address.registry_id <> detached.registry_id
            AND current_address.bus = history.bus
            AND current_address.hardware_address = history.hardware_address
      )
      AND (
          SELECT count(DISTINCT owner.registry_id)
          FROM public.sensor_address_history AS owner
          WHERE owner.bus = history.bus
            AND owner.hardware_address = history.hardware_address
      ) = 1
)
UPDATE public.sensor_registry AS detached
SET hardware_address = available.hardware_address,
    identity_status = 'confirmed'
FROM available_addresses AS available
WHERE detached.registry_id = available.registry_id
  AND available.address_rank = 1;

UPDATE public.device AS device
SET serial_number = CASE
    WHEN registry.bus = 'rs485' THEN 'MODBUS-' || registry.hardware_address::TEXT
    ELSE NULL
END
FROM public.sensor_registry AS registry
WHERE registry.device_id = device.device_id
  AND device.serial_number = 'SENSOR-' || registry.sensor_uid::TEXT;

DROP TABLE public.sensor_address_history;
DROP INDEX public.sensor_registry_bus_address_unique;
ALTER TABLE public.sensor_registry
    ALTER COLUMN hardware_address SET NOT NULL,
    ADD CONSTRAINT sensor_registry_bus_address_unique UNIQUE (bus, hardware_address),
    DROP CONSTRAINT sensor_registry_identity_status_check,
    DROP CONSTRAINT sensor_registry_pending_candidate_registry_id_fkey,
    DROP CONSTRAINT sensor_registry_pending_since_check,
    DROP CONSTRAINT sensor_registry_detached_address_check,
    DROP COLUMN pending_candidate_registry_id,
    DROP COLUMN pending_since,
    DROP COLUMN identity_status,
    DROP COLUMN sensor_uid;

COMMIT;
