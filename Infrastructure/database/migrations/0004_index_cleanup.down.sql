-- Roll back the index cleanup. The historical indexes are restored with the
-- same definitions; the two added indexes are removed.
\set ON_ERROR_STOP on

BEGIN;

DROP INDEX IF EXISTS public.idx_effective_setpoints_device_mode_time;
DROP INDEX IF EXISTS public.idx_pid_parameter_history_room_type_time;

CREATE INDEX IF NOT EXISTS idx_measurement_time
    ON public.measurement ("time" DESC);
CREATE INDEX IF NOT EXISTS idx_measurement_sensor_id
    ON public.measurement (sensor_id);
CREATE INDEX IF NOT EXISTS idx_effective_setpoints_timestamp
    ON public.effective_setpoints ("timestamp" DESC);
CREATE INDEX IF NOT EXISTS idx_effective_setpoints_location
    ON public.effective_setpoints (location, cluster);

COMMIT;
