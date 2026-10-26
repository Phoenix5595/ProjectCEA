-- Empties all history from a restored disposable copy so it can become the
-- fresh-install schema baseline. Keeps schema, Timescale catalog, jobs and
-- this farm's configuration; removes credentials.
-- Run only by Infrastructure/scripts/generate_schema_baseline.sh as postgres.
\set ON_ERROR_STOP on

DO $$
BEGIN
  IF current_database() !~ '^monitoring_test_[a-z0-9_]+$' THEN
    RAISE EXCEPTION 'clear_history refuses %', current_database();
  END IF;
END
$$;

CREATE TEMP TABLE baseline_cleared(name text PRIMARY KEY);
INSERT INTO baseline_cleared(name) VALUES
  ('measurement'), ('automation_state'), ('effective_setpoints'), ('control_history'),
  ('relay_observation'), ('monitoring_relay_duration_1hour'),
  ('device_states'), ('pid_parameter_history'), ('pid_adjustments'), ('pid_autotune_state'),
  ('config_versions'), ('mode_transition_history'), ('setpoint_history'),
  ('light_state_history'), ('rollout_events'),
  ('monitoring_room_photoperiod'), ('monitoring_photoperiod_coverage'),
  ('calendar_mode_application'), ('calendar_sync_connection'), ('grow_plan_idempotency'),
  ('archive_artifact'), ('archive_availability'), ('archive_component'), ('archive_dataset'),
  ('archive_expiry_cursor'), ('archive_job'), ('archive_load'), ('archive_scope_override'),
  ('archive_window');

CREATE TEMP TABLE baseline_kept(name text PRIMARY KEY);
INSERT INTO baseline_kept(name) VALUES
  ('alembic_version'), ('schema_migration'), ('migration_0012_state'),
  ('room'), ('rack'), ('device'), ('sensor'), ('sensor_registry'), ('sensor_address_history'),
  ('device_registry'), ('device_mappings'),
  ('climate_periods'), ('crop_batch'), ('calendar_event'), ('calendar_room_profile'),
  ('grow_lifecycle'),
  ('room_modes'), ('flower_submodes'), ('mode_parameters'), ('mode_notes'), ('room_active_mode'),
  ('light_presets'), ('light_programs'), ('light_target_intensity'), ('room_light_schedule'),
  ('schedules'), ('rules'), ('setpoints'), ('pid_parameters'), ('service_setting'), ('notes'),
  ('soil_bed_profile'), ('soil_phase_target'), ('soil_probe_placement'), ('water_tank_monitor_config'),
  ('monitoring_job_snapshot_0007'), ('monitoring_refresh_policy_snapshot_0007'),
  ('monitoring_index_snapshot_0007'), ('monitoring_dependent_view_snapshot_0007'),
  ('monitoring_job_snapshot_0008'), ('monitoring_toolkit_snapshot_0008'),
  ('monitoring_relay_overview_progress'),
  -- The overview checkpoint jobs require one coverage row per model and pause
  -- themselves when a row is missing; keep rows and their backfill markers.
  ('monitoring_overview_coverage'), ('monitoring_cagg_backfill_marker');

-- Completeness guard: every public base table is either cleared or kept.
-- Rule for a new table: rows recording something that happened -> cleared;
-- rows describing how the farm is set up -> kept.
DO $$
DECLARE
  unclassified text;
BEGIN
  SELECT t.tablename INTO unclassified
  FROM pg_tables t
  WHERE t.schemaname = 'public'
    AND t.tablename NOT IN (SELECT name FROM baseline_cleared)
    AND t.tablename NOT IN (SELECT name FROM baseline_kept)
  ORDER BY t.tablename
  LIMIT 1;
  IF unclassified IS NOT NULL THEN
    RAISE EXCEPTION 'unclassified table %', unclassified;
  END IF;
END
$$;

-- Pause every Timescale job and wait for running workers to drain.
CREATE TEMP TABLE baseline_job_state AS
  SELECT job_id, scheduled FROM timescaledb_information.jobs;
SELECT alter_job(job_id, scheduled => false) FROM baseline_job_state;

DO $$
DECLARE
  attempt integer := 0;
BEGIN
  WHILE (SELECT count(*) FROM timescaledb_information.job_stats WHERE job_status = 'Running') > 0 LOOP
    attempt := attempt + 1;
    IF attempt > 60 THEN
      RAISE EXCEPTION 'baseline_jobs_still_running';
    END IF;
    PERFORM pg_sleep(1);
  END LOOP;
END
$$;

-- Credentials: calendar_event (kept) references calendar_sync_connection, so
-- detach the events and DELETE the connection rows instead of truncating.
UPDATE public.calendar_event SET sync_connection_id = NULL WHERE sync_connection_id IS NOT NULL;
DELETE FROM public.calendar_sync_connection;

-- Clear history tables (no CASCADE: a kept table referencing a cleared one fails loudly).
DO $$
DECLARE
  targets text;
BEGIN
  SELECT string_agg(format('public.%I', c.name), ', ' ORDER BY c.name) INTO targets
  FROM baseline_cleared c
  WHERE c.name <> 'calendar_sync_connection'
    AND to_regclass(format('public.%I', c.name)) IS NOT NULL;
  EXECUTE 'TRUNCATE ' || targets;
END
$$;

-- Clear every continuous aggregate. TRUNCATE on the view empties its
-- materialization; Timescale rejects truncating the materialized hypertable
-- directly when a hierarchical aggregate reads it.
DO $$
DECLARE
  cagg record;
BEGIN
  FOR cagg IN
    SELECT ca.user_view_schema, ca.user_view_name
    FROM _timescaledb_catalog.continuous_agg ca
    ORDER BY ca.mat_hypertable_id
  LOOP
    EXECUTE format('TRUNCATE %I.%I', cagg.user_view_schema, cagg.user_view_name);
  END LOOP;
END
$$;

-- Reset the relay-overview singleton to its initial state.
UPDATE public.monitoring_relay_overview_progress
SET covered_from = NULL,
    covered_through = NULL,
    dirty_from = NULL,
    dirty_through = NULL,
    claimed_from = NULL,
    claimed_through = NULL,
    dirty_during_claim_from = NULL,
    dirty_generation = 0,
    last_error = NULL;

-- Redact remaining credentials (calendar_sync_connection rows are deleted above).
UPDATE public.calendar_room_profile SET caldav_sync_token = NULL;

-- Restore each job's original schedule.
SELECT alter_job(s.job_id, scheduled => s.scheduled) FROM baseline_job_state s;
DROP TABLE baseline_job_state;
DROP TABLE baseline_cleared;
DROP TABLE baseline_kept;
