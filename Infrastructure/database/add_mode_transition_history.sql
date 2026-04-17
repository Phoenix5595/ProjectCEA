-- Migration: Create mode_transition_history table
-- Created: 2026-02-09
-- Purpose: Audit trail for environmental mode transitions (api/schedule/system)

CREATE TABLE IF NOT EXISTS mode_transition_history (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL,
    old_mode_id INTEGER REFERENCES room_modes(id),
    old_submode_id INTEGER REFERENCES flower_submodes(id),
    new_mode_id INTEGER NOT NULL REFERENCES room_modes(id),
    new_submode_id INTEGER REFERENCES flower_submodes(id),
    triggered_by TEXT NOT NULL CHECK (triggered_by IN ('api', 'schedule', 'system')),
    triggered_at TIMESTAMPTZ DEFAULT NOW(),
    parameters_synced JSONB,
    success BOOLEAN DEFAULT true,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for query performance on location, cluster and time
CREATE INDEX IF NOT EXISTS idx_mode_transition_history_loc_clus_time 
    ON mode_transition_history (location, cluster, triggered_at DESC);

-- Comment for documentation
COMMENT ON TABLE mode_transition_history IS 'Audit trail for environmental mode transitions in rooms/clusters';
COMMENT ON COLUMN mode_transition_history.old_mode_id IS 'Previous room_modes.id (nullable)';
COMMENT ON COLUMN mode_transition_history.new_mode_id IS 'New room_modes.id';
COMMENT ON COLUMN mode_transition_history.parameters_synced IS 'JSON: old_mode_name, old_submode_name, new_mode_name, new_submode_name, schedule_sync (from ModeTransitionService)';
