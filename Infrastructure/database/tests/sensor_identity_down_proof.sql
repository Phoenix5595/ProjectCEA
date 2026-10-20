DO $database_guard$
BEGIN
    IF current_database() !~ '^monitoring_test_[a-z0-9_]+$'
       OR lower(current_database()) LIKE '%cea_sensors%'
       OR lower(current_database()) LIKE '%sj_cea%' THEN
        RAISE EXCEPTION 'sensor identity rollback proof requires a disposable database';
    END IF;
END;
$database_guard$;

DO $legacy_lookup_proof$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM public.sensor_registry AS sr
        JOIN public.device AS d ON d.device_id = sr.device_id
        WHERE sr.bus = 'rs485' AND sr.hardware_address = 233
          AND d.serial_number = 'MODBUS-233'
    ) THEN
        RAISE EXCEPTION 'sensor_identity_down_rs485_lookup_missing';
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM public.sensor_registry AS sr
        JOIN public.device AS d ON d.device_id = sr.device_id
        WHERE sr.bus = 'can' AND sr.hardware_address = 4
          AND d.type = 'sensor-node' AND d.name = 'Node 4'
    ) THEN
        RAISE EXCEPTION 'sensor_identity_down_can_lookup_missing';
    END IF;
END;
$legacy_lookup_proof$;
