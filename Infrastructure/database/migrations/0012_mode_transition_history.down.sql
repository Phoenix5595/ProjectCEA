BEGIN;

DO $restore_0012$
DECLARE
    v_created_table BOOLEAN;
    v_had_view BOOLEAN;
BEGIN
    SELECT created_table, had_view
    INTO v_created_table, v_had_view
    FROM public.migration_0012_state;

    IF v_created_table THEN
        EXECUTE 'DROP TABLE public.mode_transition_history';
    END IF;

    IF v_had_view THEN
        EXECUTE $view$
            CREATE OR REPLACE VIEW latest_sensor_values AS
            SELECT
                s.sensor_id,
                m.time,
                m.value,
                m.status,
                s.name AS sensor_name,
                s.unit AS sensor_unit,
                d.name AS device_name,
                r.name AS room_name
            FROM sensor s
            JOIN LATERAL (
                SELECT time, value, status
                FROM measurement mm
                WHERE mm.sensor_id = s.sensor_id
                ORDER BY time DESC
                LIMIT 1
            ) m ON TRUE
            JOIN device d ON s.device_id = d.device_id
            LEFT JOIN rack rk ON d.rack_id = rk.rack_id
            LEFT JOIN room r ON rk.room_id = r.room_id
        $view$;
        EXECUTE $view_comment$
            COMMENT ON VIEW latest_sensor_values IS
                'Latest row per sensor via LATERAL + index (sensor_id, time DESC); avoids full-chunk scans.'
        $view_comment$;
    END IF;

    DROP TABLE public.migration_0012_state;
END
$restore_0012$;

COMMIT;
