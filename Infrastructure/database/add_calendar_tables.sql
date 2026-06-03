-- Calendar subsystem tables (run on Pi primary; idempotent where possible)

CREATE TABLE IF NOT EXISTS calendar_event (
    id SERIAL PRIMARY KEY,
    location TEXT NOT NULL,
    cluster TEXT NOT NULL DEFAULT 'main',
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE,
    all_day BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    crop_batch_id INTEGER REFERENCES crop_batch(batch_id) ON DELETE SET NULL,
    grow_plan_id UUID,
    recurrence_rule TEXT,
    recurrence_parent_id INTEGER REFERENCES calendar_event(id) ON DELETE SET NULL,
    ical_uid TEXT NOT NULL,
    external_provider TEXT,
    external_calendar_id TEXT,
    external_event_id TEXT,
    external_etag TEXT,
    last_synced_at TIMESTAMPTZ,
    sync_status TEXT NOT NULL DEFAULT 'synced',
    deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_event_ical_uid ON calendar_event (ical_uid);
CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_event_external
    ON calendar_event (external_provider, external_calendar_id, external_event_id)
    WHERE external_event_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_calendar_event_active
    ON calendar_event (location, start_date, end_date)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_calendar_event_grow_plan
    ON calendar_event (grow_plan_id)
    WHERE deleted_at IS NULL AND grow_plan_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS calendar_mode_application (
    id SERIAL PRIMARY KEY,
    event_id INTEGER NOT NULL REFERENCES calendar_event(id) ON DELETE CASCADE,
    applied_date DATE NOT NULL,
    mode_id INTEGER,
    submode_id INTEGER,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    triggered_by TEXT NOT NULL,
    UNIQUE (event_id, applied_date)
);

CREATE TABLE IF NOT EXISTS grow_plan_idempotency (
    idempotency_key UUID PRIMARY KEY,
    grow_plan_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_sync_connection (
    id SERIAL PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'nextcloud' CHECK (provider = 'nextcloud'),
    display_name TEXT,
    account_email TEXT,
    caldav_base_url TEXT NOT NULL,
    credentials_encrypted BYTEA NOT NULL,
    target_calendar_url TEXT NOT NULL,
    sync_token TEXT,
    last_sync_at TIMESTAMPTZ,
    last_error TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS calendar_room_profile (
    location TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    color_key TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    nextcloud_calendar_url TEXT,
    caldav_sync_token TEXT,
    enabled BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO calendar_room_profile (location, display_name, color_key, sort_order)
VALUES
    ('Flower Room', 'Flower', 'flower', 0),
    ('Veg Room', 'Vegetation', 'veg', 1),
    ('Lab', 'Laboratory', 'lab', 2)
ON CONFLICT (location) DO NOTHING;
