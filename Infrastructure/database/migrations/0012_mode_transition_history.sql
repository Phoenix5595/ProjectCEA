-- migration: downtime=false
BEGIN;

CREATE TABLE public.migration_0012_state (
    created_table BOOLEAN NOT NULL,
    had_view BOOLEAN NOT NULL
);
INSERT INTO public.migration_0012_state (created_table, had_view)
VALUES (
    to_regclass('public.mode_transition_history') IS NULL,
    to_regclass('public.latest_sensor_values') IS NOT NULL
);

CREATE TABLE IF NOT EXISTS public.mode_transition_history (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    old_mode_id INTEGER REFERENCES public.room_modes(id),
    old_submode_id INTEGER REFERENCES public.flower_submodes(id),
    new_mode_id INTEGER NOT NULL REFERENCES public.room_modes(id),
    new_submode_id INTEGER REFERENCES public.flower_submodes(id),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('api', 'schedule', 'system')),
    "time" TIMESTAMPTZ DEFAULT NOW(),
    parameters_synced JSONB,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT mode_transition_history_time_bounds CHECK (
        "time" >= '2020-01-01'::timestamptz
        AND "time" <= NOW() + INTERVAL '1 day'
    )
);

CREATE INDEX IF NOT EXISTS idx_mode_transition_history_loc_clus_time
    ON public.mode_transition_history (location, cluster, "time" DESC);

COMMENT ON TABLE public.mode_transition_history IS
    'Audit trail for environmental mode transitions in rooms/clusters';
COMMENT ON COLUMN public.mode_transition_history.old_mode_id IS
    'Previous room_modes.id (nullable)';
COMMENT ON COLUMN public.mode_transition_history.new_mode_id IS
    'New room_modes.id';
COMMENT ON COLUMN public.mode_transition_history.parameters_synced IS
    'JSON: old_mode_name, old_submode_name, new_mode_name, new_submode_name, schedule_sync (from ModeTransitionService)';

DROP VIEW IF EXISTS public.latest_sensor_values;

COMMIT;
