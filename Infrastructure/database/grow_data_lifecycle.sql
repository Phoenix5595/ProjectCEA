-- Grow data lifecycle schema (approved plan step 2).
--
-- Idempotent forward migration for grow-aware lossless archiving:
--   * grow_lifecycle plus write-once first-closure fields and the linkage
--     backfill report,
--   * the archive catalog (archive_dataset, archive_window, archive_artifact,
--     archive_component, archive_load, archive_job, archive_availability),
--   * explicit identity scope overrides (archive_scope_override),
--   * Calendar outbound-sync state and write binding.
--
-- Boundaries of this file:
--   * Forward-only and idempotent. It never changes source rows.
--   * It creates no source policies, compression jobs or retention jobs.
--     Source compression is configured separately by the operator-applied
--     grow_data_source_compression.sql.
--   * It changes no role or privilege. Apply it as the role that owns the
--     archive schema objects (cea_user in production) so ownership carries
--     access; an operator applying it as another role must transfer ownership
--     to cea_user before activation.
--
-- Apply with:
--   psql -X --set=ON_ERROR_STOP=1 --file=grow_data_lifecycle.sql <database>
-- Requires: TimescaleDB 2.x and btree_gist available, and the Calendar
-- subsystem tables (calendar_event, crop_batch) to be present.

\set ON_ERROR_STOP on

SET search_path = public, pg_catalog;

BEGIN;

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS btree_gist;

DO $prereq$
BEGIN
    IF to_regclass('public.calendar_event') IS NULL
       OR to_regclass('public.crop_batch') IS NULL THEN
        RAISE EXCEPTION 'grow_data_lifecycle requires the Calendar subsystem tables (calendar_event, crop_batch)';
    END IF;
END
$prereq$;

-- ============================================================================
-- 1. Grow lifecycle
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.grow_lifecycle (
    grow_plan_id UUID PRIMARY KEY,
    crop_batch_id INTEGER NOT NULL UNIQUE REFERENCES public.crop_batch(batch_id),
    state TEXT NOT NULL DEFAULT 'open',
    calendar_revision BIGINT NOT NULL DEFAULT 1,
    original_drying_end DATE,
    first_closed_at TIMESTAMPTZ,
    closed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT grow_lifecycle_state_check CHECK (state IN ('open', 'closed')),
    CONSTRAINT grow_lifecycle_calendar_revision_check CHECK (calendar_revision > 0),
    -- original_drying_end and first_closed_at are set together at the first
    -- explicit closure and are write-once afterwards (enforced below).
    CONSTRAINT grow_lifecycle_first_closure_complete
        CHECK ((first_closed_at IS NULL) = (original_drying_end IS NULL)),
    CONSTRAINT grow_lifecycle_closed_at_required
        CHECK (state <> 'closed' OR closed_at IS NOT NULL)
);

COMMENT ON TABLE public.grow_lifecycle IS
    'Explicit grow lifecycle: one row per top-level Calendar grow UUID mapped to exactly one crop batch.';

CREATE OR REPLACE FUNCTION public.grow_lifecycle_enforce_write_once()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $function$
BEGIN
    -- The batch linkage of a grow UUID is fixed; divergent linkage is rejected
    -- rather than merged or re-invented.
    IF NEW.crop_batch_id IS DISTINCT FROM OLD.crop_batch_id THEN
        RAISE EXCEPTION 'grow_lifecycle crop batch linkage is fixed for grow %', OLD.grow_plan_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'grow_lifecycle_crop_batch_write_once';
    END IF;
    -- original_drying_end and first_closed_at are written once at the first
    -- explicit closure and never change afterwards; reopening preserves both.
    IF OLD.first_closed_at IS NOT NULL
       AND (NEW.first_closed_at IS DISTINCT FROM OLD.first_closed_at
            OR NEW.original_drying_end IS DISTINCT FROM OLD.original_drying_end) THEN
        RAISE EXCEPTION 'grow_lifecycle first closure is write-once for grow %', OLD.grow_plan_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'grow_lifecycle_first_closure_write_once';
    END IF;
    RETURN NEW;
END;
$function$;

DO $trigger$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgrelid = 'public.grow_lifecycle'::regclass
          AND tgname = 'grow_lifecycle_write_once'
          AND NOT tgisinternal
    ) THEN
        EXECUTE 'CREATE TRIGGER grow_lifecycle_write_once
                 BEFORE UPDATE ON public.grow_lifecycle
                 FOR EACH ROW EXECUTE FUNCTION public.grow_lifecycle_enforce_write_once()';
    END IF;
END
$trigger$;

-- Backfill only distinct active top-level Calendar grow UUIDs (non-deleted
-- phase events carrying an authoritative grow_plan_id) whose events agree on
-- exactly one crop batch and whose batch is not claimed by another grow.
WITH grow_batches AS (
    SELECT e.grow_plan_id,
           COUNT(DISTINCT e.crop_batch_id) AS distinct_batches,
           MIN(e.crop_batch_id) AS batch_id
    FROM public.calendar_event e
    WHERE e.grow_plan_id IS NOT NULL
      AND e.deleted_at IS NULL
    GROUP BY e.grow_plan_id
),
batch_owners AS (
    SELECT gb.batch_id, COUNT(DISTINCT gb.grow_plan_id) AS owner_count
    FROM grow_batches gb
    WHERE gb.batch_id IS NOT NULL
    GROUP BY gb.batch_id
)
INSERT INTO public.grow_lifecycle (grow_plan_id, crop_batch_id, state)
SELECT gb.grow_plan_id, gb.batch_id, 'open'
FROM grow_batches gb
JOIN batch_owners bo ON bo.batch_id = gb.batch_id
WHERE gb.distinct_batches = 1
  AND bo.owner_count = 1
  AND NOT EXISTS (
        SELECT 1
        FROM public.grow_lifecycle gl
        WHERE gl.crop_batch_id = gb.batch_id
          AND gl.grow_plan_id <> gb.grow_plan_id
  )
ON CONFLICT (grow_plan_id) DO NOTHING;

-- Schema preflight report for grow linkage. Every distinct active top-level
-- grow UUID that is not yet in grow_lifecycle is listed with its blocking
-- reason; lifecycle activation is blocked for those grows until an owner
-- resolves the linkage. linkage_state values:
--   missing_batch    - no phase event carries a crop batch
--   ambiguous_batch  - phase events disagree on the crop batch
--   shared_batch     - the single batch is also claimed by another grow UUID
--   backfill_pending - resolvable but not backfilled (defensive)
CREATE OR REPLACE VIEW public.grow_lifecycle_backfill_report AS
WITH grow_batches AS (
    SELECT e.grow_plan_id,
           COUNT(*) AS phase_events,
           COUNT(*) FILTER (WHERE e.crop_batch_id IS NULL) AS events_without_batch,
           COUNT(DISTINCT e.crop_batch_id) AS distinct_batches,
           MIN(e.crop_batch_id) AS batch_id
    FROM public.calendar_event e
    WHERE e.grow_plan_id IS NOT NULL
      AND e.deleted_at IS NULL
    GROUP BY e.grow_plan_id
),
batch_owners AS (
    SELECT gb.batch_id, COUNT(DISTINCT gb.grow_plan_id) AS owner_count
    FROM grow_batches gb
    WHERE gb.batch_id IS NOT NULL
    GROUP BY gb.batch_id
)
SELECT gb.grow_plan_id,
       gb.batch_id,
       gb.phase_events,
       gb.events_without_batch,
       gb.distinct_batches,
       CASE
           WHEN gb.batch_id IS NULL THEN 'missing_batch'
           WHEN gb.distinct_batches > 1 THEN 'ambiguous_batch'
           WHEN COALESCE(bo.owner_count, 0) > 1 THEN 'shared_batch'
           ELSE 'backfill_pending'
       END AS linkage_state
FROM grow_batches gb
LEFT JOIN batch_owners bo ON bo.batch_id = gb.batch_id
LEFT JOIN public.grow_lifecycle gl ON gl.grow_plan_id = gb.grow_plan_id
WHERE gl.grow_plan_id IS NULL;

COMMENT ON VIEW public.grow_lifecycle_backfill_report IS
    'Blocking grow-linkage preflight report: active top-level grow UUIDs without an unambiguous crop batch.';

-- ============================================================================
-- 2. Archive catalog
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.archive_dataset (
    dataset_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind TEXT NOT NULL,
    grow_plan_id UUID REFERENCES public.grow_lifecycle(grow_plan_id),
    calendar_year INTEGER,
    quarter INTEGER,
    room TEXT NOT NULL,
    calendar_revision BIGINT NOT NULL DEFAULT 1,
    data_revision BIGINT NOT NULL DEFAULT 1,
    published_revision BIGINT,
    state TEXT NOT NULL DEFAULT 'pending',
    available_from TIMESTAMPTZ,
    available_to TIMESTAMPTZ,
    anniversary_at TIMESTAMPTZ,
    manifest JSONB,
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_dataset_kind_check CHECK (kind IN ('grow', 'veg_quarter', 'veg_year')),
    CONSTRAINT archive_dataset_state_check
        CHECK (state IN ('pending', 'copying', 'ready', 'dirty', 'blocked', 'zip_only')),
    CONSTRAINT archive_dataset_kind_linkage CHECK (
        (kind = 'grow'
            AND grow_plan_id IS NOT NULL AND calendar_year IS NULL AND quarter IS NULL)
        OR (kind = 'veg_quarter'
            AND grow_plan_id IS NULL AND calendar_year IS NOT NULL AND quarter IS NOT NULL)
        OR (kind = 'veg_year'
            AND grow_plan_id IS NULL AND calendar_year IS NOT NULL AND quarter IS NULL)
    ),
    CONSTRAINT archive_dataset_quarter_check CHECK (quarter IS NULL OR quarter BETWEEN 1 AND 4),
    CONSTRAINT archive_dataset_revisions_check CHECK (
        calendar_revision > 0 AND data_revision > 0
        AND (published_revision IS NULL OR (published_revision > 0 AND published_revision <= data_revision))
    ),
    CONSTRAINT archive_dataset_availability_bounds CHECK (
        available_from IS NULL OR available_to IS NULL OR available_from < available_to
    )
);

COMMENT ON TABLE public.archive_dataset IS
    'Archive catalog: one grow dataset per closed grow, one dataset per Veg calendar quarter and year.';

CREATE UNIQUE INDEX IF NOT EXISTS archive_dataset_grow_unique
    ON public.archive_dataset (grow_plan_id) WHERE kind = 'grow';
CREATE UNIQUE INDEX IF NOT EXISTS archive_dataset_veg_quarter_unique
    ON public.archive_dataset (calendar_year, quarter) WHERE kind = 'veg_quarter';
CREATE UNIQUE INDEX IF NOT EXISTS archive_dataset_veg_year_unique
    ON public.archive_dataset (calendar_year) WHERE kind = 'veg_year';
CREATE INDEX IF NOT EXISTS archive_dataset_state_idx
    ON public.archive_dataset (state);
CREATE INDEX IF NOT EXISTS archive_dataset_room_idx
    ON public.archive_dataset (room, kind);

CREATE TABLE IF NOT EXISTS public.archive_window (
    dataset_id UUID NOT NULL REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE,
    calendar_revision BIGINT NOT NULL,
    event_id INTEGER NOT NULL,
    phase_type TEXT NOT NULL,
    room TEXT NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (dataset_id, calendar_revision, event_id),
    CONSTRAINT archive_window_revision_check CHECK (calendar_revision > 0),
    -- Half-open UTC bounds derived from inclusive Toronto Calendar dates.
    CONSTRAINT archive_window_half_open CHECK (start_at < end_at)
);

COMMENT ON TABLE public.archive_window IS
    'Per-phase half-open selection windows; intersecting same-grow/same-room windows merge for row selection.';

CREATE INDEX IF NOT EXISTS archive_window_scope_idx
    ON public.archive_window (room, start_at, end_at);
CREATE INDEX IF NOT EXISTS archive_window_dataset_idx
    ON public.archive_window (dataset_id, calendar_revision);

CREATE TABLE IF NOT EXISTS public.archive_artifact (
    artifact_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'staging',
    remote_instance_id UUID,
    remote_path TEXT,
    sha256 TEXT,
    byte_size BIGINT,
    verified_at TIMESTAMPTZ,
    local_path TEXT,
    local_verified_at TIMESTAMPTZ,
    last_accessed_at TIMESTAMPTZ,
    manifest_sha256 TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_artifact_state_check CHECK (state IN ('staging', 'verified', 'superseded')),
    CONSTRAINT archive_artifact_revision_check CHECK (revision > 0),
    CONSTRAINT archive_artifact_sha256_check CHECK (sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT archive_artifact_manifest_sha256_check
        CHECK (manifest_sha256 IS NULL OR manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT archive_artifact_size_check CHECK (byte_size IS NULL OR byte_size >= 0),
    CONSTRAINT archive_artifact_verified_complete CHECK (
        state <> 'verified'
        OR (remote_path IS NOT NULL AND sha256 IS NOT NULL AND byte_size IS NOT NULL AND verified_at IS NOT NULL)
    ),
    CONSTRAINT archive_artifact_local_verified CHECK (
        local_verified_at IS NULL OR local_path IS NOT NULL
    )
);

COMMENT ON TABLE public.archive_artifact IS
    'Immutable ZIP artifacts per dataset revision; only one verified artifact per dataset revision at a time.';

CREATE UNIQUE INDEX IF NOT EXISTS archive_artifact_verified_unique
    ON public.archive_artifact (dataset_id, revision) WHERE state = 'verified';
CREATE INDEX IF NOT EXISTS archive_artifact_dataset_revision_idx
    ON public.archive_artifact (dataset_id, revision, state);

CREATE TABLE IF NOT EXISTS public.archive_component (
    parent_artifact_id UUID NOT NULL REFERENCES public.archive_artifact(artifact_id) ON DELETE CASCADE,
    child_dataset_id UUID NOT NULL REFERENCES public.archive_dataset(dataset_id),
    child_revision BIGINT NOT NULL,
    member_prefix TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    observation_counts JSONB NOT NULL DEFAULT '{}'::JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (parent_artifact_id, child_dataset_id),
    CONSTRAINT archive_component_revision_check CHECK (child_revision > 0),
    CONSTRAINT archive_component_manifest_sha256_check CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$')
);

COMMENT ON TABLE public.archive_component IS
    'Annual containers pin each child dataset identity, revision, counts and hashes; not just paths.';

CREATE TABLE IF NOT EXISTS public.archive_load (
    load_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE,
    revision BIGINT NOT NULL,
    source_kind TEXT NOT NULL,
    artifact_id UUID REFERENCES public.archive_artifact(artifact_id),
    member_prefix TEXT,
    staging_path TEXT,
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_load_revision_check CHECK (revision > 0),
    CONSTRAINT archive_load_source_kind_check CHECK (source_kind IN ('zip')),
    CONSTRAINT archive_load_zip_requires_artifact CHECK (source_kind <> 'zip' OR artifact_id IS NOT NULL)
);

ALTER TABLE public.archive_load
    DROP CONSTRAINT IF EXISTS archive_load_source_kind_check;
ALTER TABLE public.archive_load
    ADD CONSTRAINT archive_load_source_kind_check CHECK (source_kind IN ('zip'));

COMMENT ON TABLE public.archive_load IS
    'Explicit Historical selection pin: one row per user-selected load, expiring after idle time.';

CREATE INDEX IF NOT EXISTS archive_load_expiry_idx
    ON public.archive_load (expires_at);
CREATE INDEX IF NOT EXISTS archive_load_artifact_idx
    ON public.archive_load (artifact_id) WHERE artifact_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.archive_job (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    dataset_id UUID NOT NULL REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    requested_revision BIGINT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    checkpoint JSONB NOT NULL DEFAULT '{}'::JSONB,
    lease_owner UUID,
    -- Internal only: never exposed through APIs, logs or manifests.
    lease_token UUID,
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_job_kind_check CHECK (
        kind IN ('export', 'annual_merge', 'load', 'expire', 'artifact_cleanup')
    ),
    CONSTRAINT archive_job_state_check
        CHECK (state IN ('pending', 'running', 'succeeded', 'blocked', 'failed')),
    CONSTRAINT archive_job_requested_revision_check CHECK (requested_revision > 0),
    CONSTRAINT archive_job_attempts_check CHECK (attempts >= 0),
    -- A claim always owns the lease owner, private token and 60 second expiry
    -- together; releasing a claim clears all three together.
    CONSTRAINT archive_job_lease_complete CHECK (
        (lease_owner IS NULL) = (lease_token IS NULL)
        AND (lease_owner IS NULL) = (lease_until IS NULL)
    ),
    CONSTRAINT archive_job_running_requires_lease CHECK (state <> 'running' OR lease_owner IS NOT NULL)
);

ALTER TABLE public.archive_job
    DROP CONSTRAINT IF EXISTS archive_job_kind_check;
ALTER TABLE public.archive_job
    ADD CONSTRAINT archive_job_kind_check CHECK (
        kind IN ('export', 'annual_merge', 'load', 'expire', 'artifact_cleanup')
    );

COMMENT ON TABLE public.archive_job IS
    'Durable archive queue with renewable 60 second leases; idempotent per dataset, kind and requested revision.';

-- One active job per (dataset, kind, requested revision); the archive queue
-- claims rows with a renewable 60 second lease and retries from checkpoints.
CREATE UNIQUE INDEX IF NOT EXISTS archive_job_active_unique
    ON public.archive_job (dataset_id, kind, requested_revision)
    WHERE state IN ('pending', 'running', 'blocked');
CREATE INDEX IF NOT EXISTS archive_job_due_idx
    ON public.archive_job (next_attempt_at) WHERE state IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS public.archive_availability (
    availability_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dataset_id UUID REFERENCES public.archive_dataset(dataset_id) ON DELETE CASCADE,
    revision BIGINT,
    source_table TEXT NOT NULL,
    room TEXT,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL,
    source_rows_removed BOOLEAN NOT NULL DEFAULT FALSE,
    source_removed_at TIMESTAMPTZ,
    aggregates_refreshed BOOLEAN NOT NULL DEFAULT FALSE,
    aggregates_refreshed_at TIMESTAMPTZ,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_availability_state_check CHECK (state IN ('captured', 'expired')),
    CONSTRAINT archive_availability_bounds CHECK (start_at < end_at),
    -- Captured intervals belong to a dataset revision; expired nongrow
    -- intervals have no dataset. A room of NULL marks shared context captured
    -- for every room inside the dataset window.
    CONSTRAINT archive_availability_dataset_binding CHECK (
        (state = 'captured' AND dataset_id IS NOT NULL AND revision IS NOT NULL AND revision > 0)
        OR (state = 'expired' AND dataset_id IS NULL AND revision IS NULL)
    ),
    -- Removal and aggregate-refresh flags only describe work that really
    -- happened: each flag requires its own timestamp, and aggregate windows
    -- are refreshed only after the matching source rows were removed.
    CONSTRAINT archive_availability_removal_status CHECK (
        source_rows_removed = (source_removed_at IS NOT NULL)
    ),
    CONSTRAINT archive_availability_aggregate_status CHECK (
        aggregates_refreshed = (aggregates_refreshed_at IS NOT NULL)
    ),
    CONSTRAINT archive_availability_refresh_order CHECK (
        NOT aggregates_refreshed OR source_rows_removed
    )
);

COMMENT ON TABLE public.archive_availability IS
    'Per source table/room intervals that a dataset captured (or that expired): the local-history guard reads these instead of trusting closure alone.';

CREATE UNIQUE INDEX IF NOT EXISTS archive_availability_scope_unique
    ON public.archive_availability (dataset_id, revision, source_table, room, start_at, end_at)
    NULLS NOT DISTINCT;
CREATE INDEX IF NOT EXISTS archive_availability_lookup_idx
    ON public.archive_availability (source_table, state, start_at, end_at);

-- ============================================================================
-- 3. Explicit identity scope overrides
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.archive_scope_override (
    override_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_table TEXT NOT NULL,
    source_identity JSONB NOT NULL,
    start_at TIMESTAMPTZ NOT NULL,
    end_at TIMESTAMPTZ NOT NULL,
    room TEXT NOT NULL,
    confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT archive_scope_override_source_table_check
        CHECK (source_table IN ('measurement', 'actuator_events')),
    CONSTRAINT archive_scope_override_bounds CHECK (start_at < end_at),
    CONSTRAINT archive_scope_override_room_check CHECK (btrim(room) <> ''),
    -- Exactly one real integer identity: sensor_id for measurement,
    -- device_id for actuator_events; no extra keys, no string values.
    CONSTRAINT archive_scope_override_identity_shape CHECK (
        (source_table = 'measurement'
            AND source_identity ? 'sensor_id'
            AND jsonb_typeof(source_identity -> 'sensor_id') = 'number'
            AND (source_identity - 'sensor_id') = '{}'::JSONB)
        OR (source_table = 'actuator_events'
            AND source_identity ? 'device_id'
            AND jsonb_typeof(source_identity -> 'device_id') = 'number'
            AND (source_identity - 'device_id') = '{}'::JSONB)
    ),
    CONSTRAINT archive_scope_override_identity_positive CHECK (
        CASE source_table
            WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT > 0
            WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT > 0
        END
    ),
    -- Date-bounded and exactly non-overlapping per identity; partial overlap of
    -- another correction for the same identity is rejected.
    CONSTRAINT archive_scope_override_no_overlap EXCLUDE USING gist (
        source_table WITH =,
        (CASE source_table
            WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT
            WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT
        END) WITH =,
        tstzrange(start_at, end_at, '[)') WITH &&
    )
);

COMMENT ON TABLE public.archive_scope_override IS
    'Owner-confirmed dated room assignment for unresolved measurement/actuator identities; never a general room-history editor.';

CREATE INDEX IF NOT EXISTS archive_scope_override_identity_idx
    ON public.archive_scope_override (
        source_table,
        (CASE source_table
            WHEN 'measurement' THEN (source_identity ->> 'sensor_id')::BIGINT
            WHEN 'actuator_events' THEN (source_identity ->> 'device_id')::BIGINT
        END),
        start_at,
        end_at
    );

-- ============================================================================
-- 4. Helper functions
-- ============================================================================

-- Advisory lock namespace shared with the repository layer:
-- hashtextextended('sj-history-room:' || room, 0), acquired sorted by room.
CREATE OR REPLACE FUNCTION public.sj_history_room_lock_key(room TEXT)
RETURNS BIGINT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
SET search_path = pg_catalog, public
AS $function$
    SELECT pg_catalog.hashtextextended('sj-history-room:' || room, 0)
$function$;


-- Calendar outbound-sync state. This extends the existing Calendar tables;
-- it neither enables a connection nor changes event retention/policies.
ALTER TABLE public.calendar_sync_connection
    ADD COLUMN IF NOT EXISTS instance_id UUID NOT NULL DEFAULT gen_random_uuid(),
    ADD COLUMN IF NOT EXISTS generation BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS files_root_url TEXT;

DO $sync_connection_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_sync_connection'::regclass
          AND conname = 'calendar_sync_connection_generation_positive'
    ) THEN
        ALTER TABLE public.calendar_sync_connection
            ADD CONSTRAINT calendar_sync_connection_generation_positive
            CHECK (generation > 0);
    END IF;
END
$sync_connection_constraints$;

ALTER TABLE public.calendar_event
    ADD COLUMN IF NOT EXISTS local_revision BIGINT NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS sync_connection_id INTEGER,
    ADD COLUMN IF NOT EXISTS sync_generation BIGINT,
    ADD COLUMN IF NOT EXISTS sync_error_code TEXT,
    ADD COLUMN IF NOT EXISTS sync_next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS sync_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sync_lease_owner UUID,
    ADD COLUMN IF NOT EXISTS sync_lease_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS sync_mirror_all BOOLEAN NOT NULL DEFAULT false;

DO $sync_event_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_event'::regclass
          AND conname = 'calendar_event_local_revision_positive'
    ) THEN
        ALTER TABLE public.calendar_event
            ADD CONSTRAINT calendar_event_local_revision_positive
            CHECK (local_revision > 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_event'::regclass
          AND conname = 'calendar_event_sync_attempts_nonnegative'
    ) THEN
        ALTER TABLE public.calendar_event
            ADD CONSTRAINT calendar_event_sync_attempts_nonnegative
            CHECK (sync_attempts >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_event'::regclass
          AND conname = 'calendar_event_sync_lease_pair'
    ) THEN
        ALTER TABLE public.calendar_event
            ADD CONSTRAINT calendar_event_sync_lease_pair
            CHECK ((sync_lease_owner IS NULL) = (sync_lease_until IS NULL));
    END IF;
END
$sync_event_constraints$;

DO $sync_event_fk$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.calendar_event'::regclass
          AND conname = 'calendar_event_sync_connection_fk'
    ) THEN
        ALTER TABLE public.calendar_event
            ADD CONSTRAINT calendar_event_sync_connection_fk
            FOREIGN KEY (sync_connection_id)
            REFERENCES public.calendar_sync_connection(id);
    END IF;
END
$sync_event_fk$;

CREATE INDEX IF NOT EXISTS calendar_event_sync_due_idx
    ON public.calendar_event (sync_status, sync_next_attempt_at, sync_lease_until)
    WHERE sync_status IN ('pending_push', 'pending_delete');

-- Event business writes, including direct repository callers, bind their
-- pending operation to one enabled, freshly verified connection generation.
-- Sync acknowledgements/claims touch only sync fields and never re-enqueue.
CREATE OR REPLACE FUNCTION public.sj_calendar_bind_pending()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $bind$
DECLARE
    v_changed BOOLEAN;
    v_active RECORD;
BEGIN
    IF TG_OP='INSERT' THEN
        v_changed := TRUE;
        NEW.local_revision := 1;
    ELSE
        v_changed := ROW(NEW.location,NEW.cluster,NEW.event_type,NEW.title,NEW.start_date,NEW.end_date,
            NEW.all_day,NEW.notes,NEW.metadata,NEW.crop_batch_id,NEW.grow_plan_id,NEW.ical_uid,
            NEW.recurrence_rule,NEW.recurrence_parent_id,NEW.deleted_at)
          IS DISTINCT FROM ROW(OLD.location,OLD.cluster,OLD.event_type,OLD.title,OLD.start_date,OLD.end_date,
            OLD.all_day,OLD.notes,OLD.metadata,OLD.crop_batch_id,OLD.grow_plan_id,OLD.ical_uid,
            OLD.recurrence_rule,OLD.recurrence_parent_id,OLD.deleted_at);
        v_changed := v_changed OR (NEW.updated_at IS DISTINCT FROM OLD.updated_at
            AND NEW.sync_status IN ('pending_push','pending_delete'));
        IF NOT v_changed THEN
            RETURN NEW;
        END IF;
        NEW.local_revision := OLD.local_revision+1;
    END IF;
    SELECT id,generation INTO v_active FROM public.calendar_sync_connection
    WHERE enabled AND files_root_url IS NOT NULL ORDER BY id DESC LIMIT 1;
    IF FOUND THEN
        IF TG_OP='UPDATE' THEN
            IF OLD.sync_connection_id IS DISTINCT FROM v_active.id OR OLD.sync_generation IS DISTINCT FROM v_active.generation THEN
                NEW.external_provider := NULL;
                NEW.external_event_id := NULL;
                NEW.external_etag := NULL;
                NEW.external_calendar_id := NULL;
            END IF;
        END IF;
        NEW.sync_connection_id := v_active.id;
        NEW.sync_generation := v_active.generation;
        NEW.sync_status := CASE WHEN NEW.deleted_at IS NULL THEN 'pending_push' ELSE 'pending_delete' END;
        NEW.sync_error_code := NULL;
    ELSE
        NEW.sync_connection_id := NULL;
        NEW.sync_generation := NULL;
        NEW.sync_status := 'error';
        NEW.sync_error_code := 'calendar_connection_unavailable';
    END IF;
    NEW.sync_lease_owner := NULL;
    NEW.sync_lease_until := NULL;
    NEW.sync_mirror_all := false;
    NEW.sync_attempts := 0;
    NEW.sync_next_attempt_at := clock_timestamp();
    RETURN NEW;
END;
$bind$;

DO $bind_trigger$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgrelid='public.calendar_event'::regclass
        AND tgname='sj_calendar_bind_pending' AND NOT tgisinternal) THEN
        CREATE TRIGGER sj_calendar_bind_pending BEFORE INSERT OR UPDATE ON public.calendar_event
        FOR EACH ROW EXECUTE FUNCTION public.sj_calendar_bind_pending();
    END IF;
END;
$bind_trigger$;

-- A legacy confirmation is not confirmation in a verified current instance.
UPDATE public.calendar_event SET sync_status='error',sync_error_code='calendar_connection_unavailable'
WHERE sync_connection_id IS NULL AND sync_status='synced';

COMMIT;
