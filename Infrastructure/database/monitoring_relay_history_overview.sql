-- Relay duration sources for the bounded all-history monitoring overview.
--
-- TRANSACTION FRAGMENT: this file is included by \ir from
-- Infrastructure/database/monitoring_history_overview.sql AFTER that entry
-- consumed the database authorization markers
-- (app.grow_cycle_history_disposable / app.grow_cycle_history_authorized),
-- asserted PostgreSQL 15 + TimescaleDB 2.28.3, opened the single migration
-- transaction, and created the shared failure gate
-- (public.monitoring_overview_unacknowledged_failures) and the coverage
-- table (public.monitoring_overview_coverage). Everything here runs INSIDE
-- that open transaction: no BEGIN, no COMMIT, no \set, no authorization
-- re-checks; every relation and function is referenced with an explicit
-- schema and this file aborts on drift instead of repairing.
--
-- Owned objects (additive and idempotent; nothing is dropped, and no
-- unrelated policy or job is touched):
--
--   public.monitoring_relay_epoch_us(TIMESTAMPTZ)  exact epoch microseconds
--   public.monitoring_relay_ts_at_us(BIGINT)       exact inverse conversion
--   public.monitoring_relay_duration_slices(range_start TIMESTAMPTZ,
--       range_end TIMESTAMPTZ, watermark BIGINT): STABLE read-only port of
--       the canonical relay support state machine
--       (features/relay-timeline/intervals.ts buildRelayLaneIntervals).
--   public.monitoring_relay_duration_1hour: derived hypertable keyed
--       (bucket, location, cluster, channel, device_id, device_name,
--       device_type), written ONLY by the rebuild job (additively: a
--       claimed day is replaced by marking it inactive then upserting
--       the verified rows back to active; readers filter active).
--   public.monitoring_relay_duration_1day: materialized-only continuous
--       aggregate of the hourly rows with the same identity (inactive
--       hourly rows excluded).
--   public.monitoring_relay_overview_progress: one-row dirty / claim /
--       coverage state machine fed by statement-level transition-table
--       triggers on relay_observation and by the rebuild checkpoints.
--   triggers monitoring_relay_overview_progress_insert_dirty /
--       ..._update_dirty on public.relay_observation.
--   custom paused job public.monitoring_rebuild_relay_overview and the
--       paused refresh policy job for monitoring_relay_duration_1day.
--   custom paused job public.monitoring_refresh_relay_overview_hourly and
--       its SQL/PLpgSQL implementation public...
--       _refresh_relay_overview_hourly_command: the ongoing bounded
--       verified hourly refresh workflow, paused until owner enablement.
--
-- Interfaces handed to the parent / API integration:
--   * monitoring_relay_duration_slices(range_start, range_end, watermark):
--     exact intervals for partial relay windows under one request watermark.
--   * monitoring_relay_overview_hourly_ready(required_from,
--     required_through): verified-coverage/pending-dirty readiness gate.
--   * coverage rows 'monitoring_relay_duration_1hour' and
--     'monitoring_relay_duration_1day' in monitoring_overview_coverage,
--     advanced exclusively by verified checkpoints below.
--   * monitoring_relay_overview_progress for claim/dirty/generation/error
--     introspection.
--
-- Cross-cutting invariants (sections repeat the ones they rely on):
--   1. The support machine lives on recorded rows only: no liveEdge, no
--      registry/current snapshot, no state derived outside the supplied
--      observation_id watermark. Known state expires 60 s after the newest
--      support record; heartbeats after expiry never resurrect intervals; a
--      supported baseline may cross an hour boundary exactly while its
--      support is fresh (<= 60 s) and never across a heartbeat gap.
--   2. Hourly rows retain known durations and actually observed lane identities.
--      A recorded unknown-only lane has zero ON/OFF; absent rows also mean
--      unknown time, never a registry-derived owner.
--   3. Coverage advances only through verified checkpoints: one UTC day is
--      claimed, built from the pinned snapshot and committed together with
--      its coverage upserts in one transaction.
--   4. Dirty propagation is statement-level aggregate min/max over the
--      trigger's transition tables — no global MAX() of relay_observation
--      and no per-row auxiliary write.
--   5. Mutating procedures are SECURITY INVOKER without a procedure-level
--      SET clause (a SET clause would forbid the COMMITs this machinery
--      needs); PUBLIC execution of the mutating procedure is revoked. The
--      statement-level dirty trigger function is the exception: SECURITY
--      DEFINER with a fixed pg_catalog-first search_path and fully
--      qualified references, so raw relay writer roles keep only their
--      existing relay_observation grants and never receive direct write
--      rights on the progress singleton (the trigger alone performs the
--      guarded upsert).
--   6. Writer privilege note: raw relay writers (roles that
--      INSERT/UPDATE relay_observation) exercise the definer-restricted
--      statement-level dirty trigger, which performs the guarded progress
--      write so they need no additional grants on the singleton, the
--      coverage table or the claim machinery. Retention DELETEs on
--      relay_observation are intentionally NOT dirty: the hourly and daily
--      archives exist precisely to outlive raw retention, and deletion is
--      not a new observable fact.

---------------------------------------------------------------------
-- §A. Exact microsecond conversion helpers.
--
-- extract(epoch from timestamptz) degrades to double precision, whose
-- significand pins 2^53 integer space: a 2026 timestamp expressed in
-- seconds needs ~16 significant digits and IEEE rounding exceeds the
-- microsecond. Splitting at the whole second keeps both halves exactly
-- representable: the whole-second part is an integer < 2^53 (exact as a
-- double), and the sub-second part is an interval with magnitude < 1 s
-- (double error far below a microsecond, so round(...*1e6) is exact).
-- The inverse allocates through bigint * interval '1 microsecond':
-- exact int64 microsecond interval arithmetic.
---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.monitoring_relay_epoch_us(ts TIMESTAMPTZ)
RETURNS BIGINT
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog
AS $function$
    SELECT
        (extract(epoch FROM date_trunc('second', ts)))::BIGINT * 1000000
        + round(extract(epoch
              FROM (ts - date_trunc('second', ts))) * 1000000)::BIGINT
$function$;

CREATE OR REPLACE FUNCTION public.monitoring_relay_ts_at_us(epoch_us BIGINT)
RETURNS TIMESTAMPTZ
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
SET search_path = pg_catalog
AS $function$
    SELECT TIMESTAMPTZ '1970-01-01 00:00:00+00'
         + (epoch_us * INTERVAL '1 microsecond')
$function$;

---------------------------------------------------------------------
-- §B. public.monitoring_relay_duration_slices
--
-- STABLE read-only port of buildRelayLaneIntervals from
-- Infrastructure/frontend/src/features/relay-timeline/intervals.ts
-- (lines 147-427) with the live-edge branch and every registry/current
-- snapshot dependency removed. Contract:
--   * Half-open [range_start, range_end); empty/malformed windows return no
--     rows instead of inventing spans.
--   * watermark is the observation_id snapshot bound ("claim watermark"):
--     rows with observation_id > watermark do not exist for this call.
--     Rows committed later belong to the dirty machinery, never to this
--     verification.
--   * Lanes are discovered strictly from the snapshot: distinct identities
--     (location, cluster, channel, device_id, device_name, device_type)
--     from full in-window non-heartbeat rows, plus the identities of the
--     latest pre-window row of each channel when that row's identity is
--     complete and its device_type is not 'light' (that path is exactly
--     how a supported baseline is allowed to cross the window opening).
--     Heartbeats and rows with NULL identity columns never form lanes;
--     light-channel rows and partial identities still act as owner-mismatch
--     transitions inside lanes sharing their channel, exactly as
--     belongsToLane treats them in the TS engine.
--   * Per-lane anchors use the latest pre-window channel observation.
--     Its baseline survives the opening only if every following heartbeat
--     stays in that session with gaps <=60 s and support reaches the opening.
--     A recent heartbeat cannot resurrect a baseline that previously expired.
--     Fresh channel observations themselves support the next 60 seconds, so
--     supported state crosses hour/day boundaries without a fabricated reset.
--   * Rows stream in the global (observed_at, observation_id) order that
--     the TS compareTransition insists on, so same-timestamp rows apply
--     strictly by id.
--   * All comparisons run on integer microseconds from
--     monitoring_relay_epoch_us; outputs convert back through
--     monitoring_relay_ts_at_us as half-open [start_at, end_at) exact to
--     the microsecond.
--   * Adjacent unknown intervals of a lane merge into one row (TS
--     append() merges unknown runs); known-state runs split at every
--     change instant. Chart partial flags and reason strings are not part
--     of this SQL contract.
--   * Pending-interval shape: each append flushes the lane's previously
--     open interval (start = its recorded open instant, end = the cursor
--     before this append) and opens the next one at that boundary, which
--     reproduces the TS append/merge exactly. Unknown-only exact items
--     emerge from the baseline-entry flush ordering (see the code).
--   * A lane with unsupported state and no data emits nothing known:
--     absence is honest, never fabricated.
---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.monitoring_relay_duration_slices(
    range_start TIMESTAMPTZ,
    range_end   TIMESTAMPTZ,
    watermark   BIGINT
)
RETURNS TABLE (
    location    TEXT,
    cluster     TEXT,
    channel     SMALLINT,
    device_id   INTEGER,
    device_name TEXT,
    device_type TEXT,
    start_at    TIMESTAMPTZ,
    end_at      TIMESTAMPTZ,
    state       TEXT
)
LANGUAGE plpgsql
STABLE
SET search_path = pg_catalog
AS $slices$
DECLARE
    v_start_us   BIGINT := COALESCE(public.monitoring_relay_epoch_us(range_start), NULL);
    v_end_us     BIGINT := COALESCE(public.monitoring_relay_epoch_us(range_end), NULL);
    v_wm         BIGINT := COALESCE(watermark, 0);
    v_gap_us     BIGINT := 60000000;  -- HEARTBEAT_MAX_GAP_MS = 60 s
    r            RECORD;
    v_t_us       BIGINT;
    i            INTEGER;
    ci           INTEGER;
    v_clipped_us BIGINT;
    v_state_int  TEXT;
    lane_count   INTEGER := 0;

    lane_location    TEXT[]     := '{}';
    lane_cluster     TEXT[]     := '{}';
    lane_channel     SMALLINT[] := '{}';
    lane_device_id   INTEGER[]  := '{}';
    lane_device_name TEXT[]     := '{}';
    lane_device_type TEXT[]     := '{}';
    lane_session     UUID[]     := '{}';  -- null: no session recorded yet
    lane_state       BOOLEAN[]  := '{}';  -- null means unknown
    lane_support     BIGINT[]   := '{}';  -- newest support instant (us)
    lane_cursor      BIGINT[]   := '{}';
    lane_pend_open   BOOLEAN[]  := '{}';  -- an un-emitted interval is pending
    lane_pend_start  BIGINT[]   := '{}';
    lane_pend_state  TEXT[]     := '{}';

    -- Per-channel anchor cache. ck_relay_observation_channel constrains
    -- channels to 0..15, so index 1..16 holds channel + 1.
    a_exists         BOOLEAN[16] := ARRAY_FILL(FALSE, ARRAY[16]);
    a_id             BIGINT[16]  := ARRAY_FILL(NULL::BIGINT, ARRAY[16]);
    a_us             BIGINT[16]  := ARRAY_FILL(NULL::BIGINT, ARRAY[16]);
    a_session        UUID[16]    := ARRAY_FILL(NULL::UUID, ARRAY[16]);
    a_state          BOOLEAN[16] := ARRAY_FILL(NULL::BOOLEAN, ARRAY[16]);
    a_reason         TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_loc            TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_cluster        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_devid          INTEGER[16] := ARRAY_FILL(NULL::INTEGER, ARRAY[16]);
    a_devname        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    a_devtype        TEXT[16]    := ARRAY_FILL(NULL::TEXT, ARRAY[16]);
    v_last_support   BIGINT;
    v_hb_session     UUID;
    v_anchor_matches BOOLEAN;
    v_supports       BOOLEAN;
BEGIN
    IF v_start_us IS NULL OR v_end_us IS NULL OR v_start_us >= v_end_us THEN
        RETURN;  -- empty/malformed window: no fabricated spans
    END IF;

    -----------------------------------------------------------------
    -- Heartbeat anchor: the global latest heartbeat strictly before the
    -- window start, bounded to the snapshot watermark (the service's
    -- anchors payload delivers exactly this row to the TS engine).
    -----------------------------------------------------------------
    SELECT o.session_id
      INTO v_hb_session
      FROM public.relay_observation o
     WHERE o.observation_id <= v_wm
       AND o.reason = 'heartbeat'
       AND o.observed_at < range_start
     ORDER BY o.observed_at DESC, o.observation_id DESC
     LIMIT 1;

    -----------------------------------------------------------------
    -- Per-channel anchors: the true newest pre-window row per channel.
    -- Matching identity per lane below reproduces the service's
    -- location/cluster/'main'/not-'light' anchor filtering exactly: when a
    -- channel's newest pre-window row is another room's, a light's, or an
    -- unattributed row, every lane on that channel loses its baseline,
    -- which is what an absent filtered anchor produced in TS anyway.
    -----------------------------------------------------------------
    FOR r IN
        SELECT DISTINCT ON (o.channel)
               o.channel, o.observation_id, o.observed_at, o.session_id,
               o.observed_state, o.reason,
               o.device_id, o.device_name, o.device_type,
               o.location, o.cluster
          FROM public.relay_observation o
         WHERE o.observation_id <= v_wm
           AND o.channel IS NOT NULL
           AND o.observed_at < range_start
         ORDER BY o.channel, o.observed_at DESC, o.observation_id DESC
    LOOP
        ci := (r.channel)::INT + 1;
        a_exists[ci]  := TRUE;
        a_id[ci]      := r.observation_id;
        a_us[ci]      := public.monitoring_relay_epoch_us(r.observed_at);
        a_session[ci] := r.session_id;
        a_state[ci]   := r.observed_state;
        a_reason[ci]  := r.reason;
        a_loc[ci]     := r.location;
        a_cluster[ci] := r.cluster;
        a_devid[ci]   := r.device_id;
        a_devname[ci] := r.device_name;
        a_devtype[ci] := r.device_type;
    END LOOP;

    -----------------------------------------------------------------
    -- Lane enumeration. (a) any in-window full non-heartbeat identity;
    -- (b) the per-channel anchor identity when complete and not 'light'.
    -- Lanes dedupe so each identity owns exactly one machine.
    -----------------------------------------------------------------
    FOR r IN
        SELECT DISTINCT ro.location, ro.cluster, ro.channel,
               ro.device_id, ro.device_name, ro.device_type
          FROM public.relay_observation ro
         WHERE ro.observation_id <= v_wm
           AND ro.observed_at >= range_start
           AND ro.observed_at < range_end
           AND ro.reason <> 'heartbeat'
           AND ro.channel IS NOT NULL
           AND ro.location IS NOT NULL
           AND ro.cluster IS NOT NULL
           AND ro.device_id IS NOT NULL
           AND ro.device_name IS NOT NULL
           AND ro.device_type IS NOT NULL
           AND ro.device_type <> 'light'
    LOOP
        lane_count := lane_count + 1;
        lane_location[lane_count]    := r.location;
        lane_cluster[lane_count]     := r.cluster;
        lane_channel[lane_count]     := r.channel;
        lane_device_id[lane_count]   := r.device_id;
        lane_device_name[lane_count] := r.device_name;
        lane_device_type[lane_count] := r.device_type;
    END LOOP;

    FOR ci IN 1..16 LOOP
        IF a_exists[ci]
           AND a_loc[ci] IS NOT NULL
           AND a_cluster[ci] IS NOT NULL
           AND a_devid[ci] IS NOT NULL
           AND a_devname[ci] IS NOT NULL
           AND a_devtype[ci] IS NOT NULL
           AND a_devtype[ci] <> 'light'
           AND NOT EXISTS (
                SELECT 1
                  FROM generate_series(1, lane_count) AS known(idx)
                 WHERE lane_location[idx]    = a_loc[ci]
                   AND lane_cluster[idx]     = a_cluster[ci]
                   AND lane_channel[idx]     = (ci - 1)::SMALLINT
                   AND lane_device_id[idx]   = a_devid[ci]
                   AND lane_device_name[idx] = a_devname[ci]
                   AND lane_device_type[idx] = a_devtype[ci])
        THEN
            lane_count := lane_count + 1;
            lane_location[lane_count]    := a_loc[ci];
            lane_cluster[lane_count]     := a_cluster[ci];
            lane_channel[lane_count]     := (ci - 1)::SMALLINT;
            lane_device_id[lane_count]   := a_devid[ci];
            lane_device_name[lane_count] := a_devname[ci];
            lane_device_type[lane_count] := a_devtype[ci];
        END IF;
    END LOOP;

    IF lane_count = 0 THEN
        RETURN;  -- no recorded owners in the snapshot for this window
    END IF;

    -----------------------------------------------------------------
    -- Anchor seed per lane (the TS initial assignment), integer microseconds.
    -----------------------------------------------------------------
    FOR i IN 1..lane_count LOOP
        ci := (lane_channel[i])::INT + 1;
        v_anchor_matches := a_exists[ci]
            AND a_loc[ci]     = lane_location[i]
            AND a_cluster[ci] = lane_cluster[i]
            AND a_devid[ci]   = lane_device_id[i]
            AND a_devname[ci] = lane_device_name[i]
            AND a_devtype[ci] = lane_device_type[i]
            AND a_state[ci] IS NOT NULL
            AND a_reason[ci] IN ('initial', 'state_changed', 'recovered',
                                 'assignment_changed');
        v_supports := FALSE;
        v_last_support := NULL;
        IF v_anchor_matches THEN
            WITH heartbeats AS (
                SELECT o.session_id,
                       public.monitoring_relay_epoch_us(o.observed_at) AS at_us,
                       lag(public.monitoring_relay_epoch_us(o.observed_at),
                           1, a_us[ci]) OVER (
                           ORDER BY o.observed_at, o.observation_id) AS previous_us
                  FROM public.relay_observation AS o
                 WHERE o.observation_id <= v_wm
                   AND o.reason = 'heartbeat'
                   AND (o.observed_at, o.observation_id) >
                       (public.monitoring_relay_ts_at_us(a_us[ci]), a_id[ci])
                   AND o.observed_at < range_start
            )
            SELECT COALESCE(bool_and(
                       session_id = a_session[ci] AND at_us - previous_us <= v_gap_us),
                       TRUE),
                   COALESCE(max(at_us), a_us[ci])
              INTO v_supports, v_last_support FROM heartbeats;
            v_supports := v_supports
                AND v_start_us - v_last_support <= v_gap_us;
        END IF;
        lane_state[i]      := CASE WHEN v_supports THEN a_state[ci] ELSE NULL END;
        lane_session[i]    := CASE WHEN v_supports THEN a_session[ci]
                                  ELSE COALESCE(v_hb_session, a_session[ci]) END;
        lane_support[i]    := CASE WHEN v_supports THEN v_last_support ELSE NULL END;
        lane_cursor[i]     := v_start_us;
        lane_pend_open[i]  := FALSE;
        lane_pend_start[i] := NULL;
        lane_pend_state[i] := NULL;
    END LOOP;

    -----------------------------------------------------------------
    -- Row stream in the global (observed_at, observation_id) order.
    -- Heartbeats apply to every lane; non-heartbeat rows apply to lanes on
    -- that row's channel (the TS filter reason='heartbeat' OR channel
    -- match). ck_relay_observation_channel_reason guarantees non-heartbeat
    -- rows carry a nonnull channel, so the lane filter is total.
    -----------------------------------------------------------------
    FOR r IN
        SELECT o.observed_at, o.observation_id, o.session_id, o.reason,
               o.channel, o.observed_state,
               o.device_id, o.device_name, o.device_type,
               o.location, o.cluster
          FROM public.relay_observation o
         WHERE o.observation_id <= v_wm
           AND o.observed_at >= range_start
           AND o.observed_at < range_end
         ORDER BY o.observed_at, o.observation_id
    LOOP
        v_t_us := public.monitoring_relay_epoch_us(r.observed_at);
        IF v_t_us IS NULL OR v_t_us >= v_end_us THEN
            CONTINUE;  -- half-open window guard
        END IF;

        FOR i IN 1..lane_count LOOP
            IF r.reason <> 'heartbeat' AND lane_channel[i] <> r.channel THEN
                CONTINUE;
            END IF;

            -----------------------------------------------------------------
            -- expireBefore (reproduced identically at every enterUnknown)
            -- runs first: a known state expires exactly 60 s after the
            -- newest support record; unknown stays unknown — heartbeats
            -- after expiry never resurrect a state.
            -----------------------------------------------------------------
            IF lane_state[i] IS NOT NULL AND lane_support[i] IS NOT NULL
               AND v_t_us > lane_support[i] + v_gap_us THEN
                v_clipped_us := LEAST(lane_support[i] + v_gap_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
            END IF;

            -----------------------------------------------------------------
            -- Session tracking precedes every other branch (the TS runs it
            -- for heartbeats and payload rows alike), so a session change
            -- always ends the current run even from supported state.
            -----------------------------------------------------------------
            IF lane_session[i] IS NOT NULL
               AND r.session_id IS DISTINCT FROM lane_session[i] THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                lane_session[i] := r.session_id;
            ELSIF lane_session[i] IS NULL THEN
                lane_session[i] := r.session_id;
            END IF;

            IF r.reason = 'heartbeat' THEN
                IF lane_state[i] IS NOT NULL THEN
                    lane_support[i] := v_t_us;  -- support refresh while known
                END IF;
                CONTINUE;
            END IF;

            IF r.reason = 'stale' OR r.reason = 'recording_gap' THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                CONTINUE;
            END IF;

            -----------------------------------------------------------------
            -- Ownership: exact identity match across the whole six-tuple.
            -- A row with any NULL identity can never match (exactly like
            -- belongsToLane against a concrete identity), and 'light' rows
            -- disown every non-light lane sharing their channel instead of
            -- forming a lane themselves.
            -----------------------------------------------------------------
            IF r.location    IS DISTINCT FROM lane_location[i]
               OR r.cluster  IS DISTINCT FROM lane_cluster[i]
               OR r.device_id    IS DISTINCT FROM lane_device_id[i]
               OR r.device_name  IS DISTINCT FROM lane_device_name[i]
               OR r.device_type  IS DISTINCT FROM lane_device_type[i] THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);  -- assignment changed
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
                CONTINUE;
            END IF;

            -- Owner row reached. assignment_changed / initial / recovered
            -- with a nonnull observed_state enter a baseline and with a
            -- null observed_state end the run; state_changed either
            -- refreshes support or switches the run.
            IF r.observed_state IS NULL THEN
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := NULL;
                lane_support[i] := NULL;
            ELSIF lane_state[i] IS NULL THEN
                -- enterBaseline with unknown state: FIRST the TS appends
                -- the unknown run from the cursor to the baseline instant
                -- (flushing any pending interval), THEN it moves the cursor
                -- forward (never backward) and adopts the new state. The
                -- pending unknown run stays open for the same merge/
                -- flush rule so the next append cuts it cleanly.
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := 'unknown';
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_cursor[i]  := GREATEST(lane_cursor[i], v_t_us);
                lane_state[i]   := r.observed_state;
                lane_support[i] := v_t_us;
            ELSIF lane_state[i] = r.observed_state THEN
                lane_support[i] := v_t_us;  -- same-state support refresh
            ELSE
                v_clipped_us := LEAST(v_t_us, v_end_us);
                IF v_clipped_us > lane_cursor[i] THEN
                    v_state_int := CASE
                        WHEN lane_state[i] IS NULL THEN 'unknown'
                        WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                    IF NOT (lane_pend_open[i]
                            AND lane_pend_state[i] = 'unknown'
                            AND v_state_int = 'unknown') THEN
                        IF lane_pend_open[i] THEN
                            RETURN QUERY
                               SELECT lane_location[i], lane_cluster[i],
                                      lane_channel[i], lane_device_id[i],
                                      lane_device_name[i], lane_device_type[i],
                                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                      lane_pend_state[i];
                        END IF;
                        lane_pend_open[i]  := TRUE;
                        lane_pend_start[i] := lane_cursor[i];
                        lane_pend_state[i] := v_state_int;
                    END IF;
                    lane_cursor[i] := v_clipped_us;
                END IF;
                lane_state[i]   := r.observed_state;
                lane_support[i] := v_t_us;
            END IF;
        END LOOP;
    END LOOP;

    -----------------------------------------------------------------
    -- Tail: expire then clip at range_end. dataEnd equals range_end here
    -- (the caller reads its own bounded snapshot; there is no live edge),
    -- so the trailing "outside queried history" unknown run has zero
    -- width and no row.
    -----------------------------------------------------------------
    FOR i IN 1..lane_count LOOP
        IF lane_state[i] IS NOT NULL AND lane_support[i] IS NOT NULL
           AND v_end_us > lane_support[i] + v_gap_us THEN
            v_clipped_us := LEAST(lane_support[i] + v_gap_us, v_end_us);
            IF v_clipped_us > lane_cursor[i] THEN
                v_state_int := CASE
                    WHEN lane_state[i] IS NULL THEN 'unknown'
                    WHEN lane_state[i] THEN 'on' ELSE 'off' END;
                IF NOT (lane_pend_open[i]
                        AND lane_pend_state[i] = 'unknown'
                        AND v_state_int = 'unknown') THEN
                    IF lane_pend_open[i] THEN
                        RETURN QUERY
                           SELECT lane_location[i], lane_cluster[i],
                                  lane_channel[i], lane_device_id[i],
                                  lane_device_name[i], lane_device_type[i],
                                  public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                                  public.monitoring_relay_ts_at_us(lane_cursor[i]),
                                  lane_pend_state[i];
                    END IF;
                    lane_pend_open[i]  := TRUE;
                    lane_pend_start[i] := lane_cursor[i];
                    lane_pend_state[i] := v_state_int;
                END IF;
                lane_cursor[i] := v_clipped_us;
            END IF;
            lane_state[i]   := NULL;
            lane_support[i] := NULL;
        END IF;

        v_clipped_us := v_end_us;
        IF v_clipped_us > lane_cursor[i] THEN
            v_state_int := CASE
                WHEN lane_state[i] IS NULL THEN 'unknown'
                WHEN lane_state[i] THEN 'on' ELSE 'off' END;
            IF NOT (lane_pend_open[i]
                    AND lane_pend_state[i] = 'unknown'
                    AND v_state_int = 'unknown') THEN
                IF lane_pend_open[i] THEN
                    RETURN QUERY
                       SELECT lane_location[i], lane_cluster[i],
                              lane_channel[i], lane_device_id[i],
                              lane_device_name[i], lane_device_type[i],
                              public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                              public.monitoring_relay_ts_at_us(lane_cursor[i]),
                              lane_pend_state[i];
                END IF;
                lane_pend_open[i]  := TRUE;
                lane_pend_start[i] := lane_cursor[i];
                lane_pend_state[i] := v_state_int;
            END IF;
            lane_cursor[i] := v_clipped_us;
        END IF;

        IF lane_pend_open[i] THEN
            RETURN QUERY
               SELECT lane_location[i], lane_cluster[i],
                      lane_channel[i], lane_device_id[i],
                      lane_device_name[i], lane_device_type[i],
                      public.monitoring_relay_ts_at_us(lane_pend_start[i]),
                      public.monitoring_relay_ts_at_us(lane_cursor[i]),
                      lane_pend_state[i];
            lane_pend_open[i] := FALSE;
        END IF;
    END LOOP;
END;
$slices$;

COMMENT ON FUNCTION public.monitoring_relay_duration_slices(TIMESTAMPTZ, TIMESTAMPTZ, BIGINT) IS
'Exact SQL port of the canonical relay support machine
(features/relay-timeline/intervals.ts): half-open windows, integer
microsecond arithmetic, caller-supplied observation_id snapshot watermark,
60 s heartbeat expiry without resurrection, exact six-column ownership; no
live edge, registry or current-snapshot dependency.';

---------------------------------------------------------------------
-- §C. Hourly derived hypertable: rows are produced and trusted only by
-- public.monitoring_rebuild_relay_overview. One chunk per UTC day matches
-- the rebuild's day claim, so a day's replace cycle touches one chunk.
-- (Chunk interval drift after external alterations is out of this file's
-- mutation scope; columns, nullability and PK shape are drift-checked.)
--
-- Derived-row replacement inside a claimed day is ADDITIVE (never a
-- DELETE, which the release's destructive-SQL guard forbids): the rebuild
-- marks the claimed day's prior rows inactive (active = FALSE), then
-- upserts the verified snapshot's own row identities back to active.
-- Inactive rows carry their historical payload but are ignored by the
-- daily continuous aggregate, the checkpoint's daily parity walk and the
-- parent's API hourly readers (filter active); inactive means "this
-- identity is not a recorded fact in the verified snapshot" — unknown
-- time, never an invented zero — and unknown-only legitimate lanes stay
-- active exactly as before.
---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.monitoring_relay_duration_1hour (
    bucket           TIMESTAMPTZ NOT NULL,
    location         TEXT        NOT NULL,
    cluster          TEXT        NOT NULL,
    channel          SMALLINT    NOT NULL,
    device_id        INTEGER     NOT NULL,
    device_name      TEXT        NOT NULL,
    device_type      TEXT        NOT NULL,
    on_microseconds  BIGINT      NOT NULL DEFAULT 0,
    off_microseconds BIGINT      NOT NULL DEFAULT 0,
    last_observed_at TIMESTAMPTZ NOT NULL,
    source_watermark BIGINT      NOT NULL,
    active           BOOLEAN     NOT NULL DEFAULT TRUE,
    CONSTRAINT pk_monitoring_relay_duration_1hour
        PRIMARY KEY (bucket, location, cluster, channel, device_id,
                     device_name, device_type),
    CONSTRAINT ck_monitoring_relay_duration_1hour_nonnegative
        CHECK (on_microseconds >= 0 AND off_microseconds >= 0)
);

DO $hourly_drift$
BEGIN
    IF to_regclass('public.monitoring_relay_duration_1hour') IS NULL THEN
        RAISE EXCEPTION 'monitoring_relay_duration_1hour is missing';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
            ('bucket',           'timestamp with time zone', TRUE),
            ('location',         'text',                     TRUE),
            ('cluster',          'text',                     TRUE),
            ('channel',          'smallint',                 TRUE),
            ('device_id',        'integer',                  TRUE),
            ('device_name',      'text',                     TRUE),
            ('device_type',      'text',                     TRUE),
            ('on_microseconds',  'bigint',                   TRUE),
            ('off_microseconds', 'bigint',                   TRUE),
            ('last_observed_at', 'timestamp with time zone', TRUE),
            ('source_watermark', 'bigint',                   TRUE),
            ('active',           'boolean',                  TRUE)
          ) AS want(column_name, type_name, required)
          LEFT JOIN pg_catalog.pg_attribute AS col
            ON col.attrelid = 'public.monitoring_relay_duration_1hour'::regclass
           AND col.attname = want.column_name
           AND col.attnum > 0
           AND NOT col.attisdropped
         WHERE col.attname IS NULL
            OR format_type(col.atttypid, col.atttypmod) <> want.type_name
            OR col.attnotnull <> want.required
    ) THEN
        RAISE EXCEPTION 'monitoring_relay_duration_1hour column drift; no repair performed';
    END IF;
END
$hourly_drift$;

SELECT create_hypertable('monitoring_relay_duration_1hour'::regclass,
                         by_range('bucket', INTERVAL '1 day'),
                         if_not_exists => TRUE);

COMMENT ON TABLE public.monitoring_relay_duration_1hour IS
'Derived per-hour relay duration rollups keyed by recorded lane identity.';
COMMENT ON COLUMN public.monitoring_relay_duration_1hour.on_microseconds IS
'Exact known ON time of the bucket in integer microseconds.';
COMMENT ON COLUMN public.monitoring_relay_duration_1hour.off_microseconds IS
'Exact known OFF time in integer microseconds; bucket microseconds not
accounted for by on+off are unknown time, never fabricated.';
COMMENT ON COLUMN public.monitoring_relay_duration_1hour.last_observed_at IS
'observed_at of the newest event-time owner row for this lane with
observation_id <= source_watermark and observed_at < bucket end.';
COMMENT ON COLUMN public.monitoring_relay_duration_1hour.source_watermark IS
'relay_observation.observation_id snapshot the row was verified with.';
COMMENT ON COLUMN public.monitoring_relay_duration_1hour.active IS
'Derived-row replacement marker, not a lane state: the rebuild marks the
claimed day''s prior rows inactive, then upserts only the verified
snapshot''s own rows back to active, so no row or index is ever deleted.
The daily continuous aggregate and the API readers ignore inactive rows:
inactive means "this identity is not a recorded fact in the verified
snapshot" (unknown time), never an invented zero, and unknown-only
legitimate lanes stay active exactly as before.';

---------------------------------------------------------------------
-- §D. Daily continuous aggregate: materialized-only, so queries ride the
-- hourly materialization and never raw rows. time_bucket day boundaries are
-- UTC, matching the rebuild's UTC day claims. Inactive hourly rows are
-- excluded here (see §C: a rebuild's mark-then-upsert leaves abandoned
-- identities inactive, and a daily day must never count them).
---------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS public.monitoring_relay_duration_1day
WITH (timescaledb.continuous, timescaledb.materialized_only = true) AS
SELECT
    time_bucket(INTERVAL '1 day', hour.bucket) AS bucket,
    hour.location,
    hour.cluster,
    hour.channel,
    hour.device_id,
    hour.device_name,
    hour.device_type,
    sum(hour.on_microseconds)::BIGINT  AS on_microseconds,
    sum(hour.off_microseconds)::BIGINT AS off_microseconds,
    max(hour.last_observed_at)         AS last_observed_at,
    max(hour.source_watermark)         AS source_watermark
FROM public.monitoring_relay_duration_1hour AS hour
WHERE hour.active
GROUP BY 1, 2, 3, 4, 5, 6, 7
WITH NO DATA;

DO $cagg_drift$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
         WHERE view_schema = 'public'
           AND view_name = 'monitoring_relay_duration_1day'
    ) THEN
        RAISE EXCEPTION 'monitoring_relay_duration_1day must exist as a continuous aggregate';
    END IF;
    IF EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
         WHERE view_schema = 'public'
           AND view_name = 'monitoring_relay_duration_1day'
           AND NOT materialized_only
    ) THEN
        RAISE EXCEPTION 'monitoring_relay_duration_1day must be materialized-only; no repair performed';
    END IF;
END
$cagg_drift$;

COMMENT ON VIEW public.monitoring_relay_duration_1day IS
'Materialized-only daily rollup of the relay duration hourly tier; the daily
readiness claim is verified per day against the hourly derive by the rebuild
checkpoints and lives in monitoring_overview_coverage.';

---------------------------------------------------------------------
-- §E. Progress singleton: the shared dirty / claim / coverage state.
--
--   * id must be TRUE (CHECK), so at most one row can exist; the migration
--     seeds the row so triggers never find the table bare.
--   * covered_from / covered_through form the verified contiguous hourly
--     coverage [from, through); NULL together before the first verified
--     checkpoint, set together forever after.
--   * dirty_from / dirty_through are the pending re-verification window
--     bounds; NULL together when nothing is pending. The bounds are one
--     interval, not hole-sets: the covered frontier participates on the
--     greatest side so a late write into already-verified history
--     re-verifies forward to the frontier, and both ends pad 60 s so
--     support windows reach safely into pending work.
--   * claimed_from / claimed_through hold one in-flight UTC day claim;
--     NULL together outside a claim.
--   * dirty_during_claim_from records the earliest newly-dirtied instant
--     observed while the claim owns the row; it prevents coverage
--     advancement until the claim resolves and merges back into dirty.
--   * dirty_generation increments once per dirtying statement (never per
--     row).
--   * last_error carries the latest fault/pause text; nulled at the next
--     successful checkpoint.
---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.monitoring_relay_overview_progress (
    id                      BOOLEAN     PRIMARY KEY CHECK (id),
    covered_from            TIMESTAMPTZ,
    covered_through         TIMESTAMPTZ,
    dirty_from              TIMESTAMPTZ,
    dirty_through           TIMESTAMPTZ,
    claimed_from            TIMESTAMPTZ,
    claimed_through         TIMESTAMPTZ,
    dirty_during_claim_from TIMESTAMPTZ,
    dirty_generation        BIGINT      NOT NULL DEFAULT 0,
    last_error              TEXT,
    CONSTRAINT ck_relay_overview_progress_covered_pair
        CHECK ((covered_from IS NULL) = (covered_through IS NULL)
               AND (covered_from IS NULL OR covered_from <= covered_through)),
    CONSTRAINT ck_relay_overview_progress_dirty_pair
        CHECK ((dirty_from IS NULL) = (dirty_through IS NULL)
               AND (dirty_from IS NULL OR dirty_from < dirty_through)),
    CONSTRAINT ck_relay_overview_progress_claim_pair
        CHECK ((claimed_from IS NULL) = (claimed_through IS NULL)
               AND (claimed_from IS NULL OR claimed_from < claimed_through))
);

INSERT INTO public.monitoring_relay_overview_progress (
    id, covered_from, covered_through,
    dirty_from, dirty_through,
    claimed_from, claimed_through,
    dirty_during_claim_from, dirty_generation, last_error
)
SELECT TRUE, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL
 WHERE NOT EXISTS (SELECT 1 FROM public.monitoring_relay_overview_progress);

COMMENT ON TABLE public.monitoring_relay_overview_progress IS
'One-row dirty/claim/coverage state machine for the relay duration rebuild
and its statement-level dirty triggers; NULL pairs are legal only while the
corresponding concept has no work yet.';

---------------------------------------------------------------------
-- §F. Dirty propagation: separate AFTER INSERT and AFTER UPDATE
-- statement-level triggers with transition tables. Each statement
-- aggregates its OWN min/max observed_at over its transition tables once;
-- there is no global MAX() of relay_observation and no per-row auxiliary
-- write, and recorder INSERT/COPY pacing is unchanged. The trigger
-- function is SECURITY DEFINER (least-privilege bridge for writer roles;
-- see the §G header invariant below for the hardened referenced relations
-- and pg_catalog-first fixed search_path) so raw relay writer roles never
-- need direct INSERT/UPDATE grants on the progress singleton, the coverage
-- table or the claim machinery. The -60 s / +60 s pads carry a changed
-- row's support reach into the pending window and the covered frontier
-- participates (greatest side) so late writes into verified history
-- re-verify forward to the frontier. Retention DELETEs are intentionally
-- NOT dirty: the archives exist to outlive raw retention, and a deleted
-- fact is not a new observable one.
---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.monitoring_relay_overview_progress_dirty()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $trigger$
DECLARE
    v_changed_min  TIMESTAMPTZ;
    v_changed_max  TIMESTAMPTZ;
    v_mark_from    TIMESTAMPTZ;
    v_mark_through TIMESTAMPTZ;
BEGIN
    IF TG_RELID <> 'public.relay_observation'::regclass THEN
        RAISE EXCEPTION 'relay dirty trigger may only observe public.relay_observation';
    END IF;
    IF TG_OP = 'INSERT' THEN
        SELECT min(observed_at), max(observed_at)
          INTO v_changed_min, v_changed_max
          FROM relay_observation_inserted;
    ELSIF TG_OP = 'UPDATE' THEN
        -- A mutated row owns both its old and new instants so neither side
        -- of a displaced identity ever drops out of the pending window.
        SELECT min(observed_at), max(observed_at)
          INTO v_changed_min, v_changed_max
          FROM (
              SELECT observed_at FROM relay_observation_update_old
              UNION ALL
              SELECT observed_at FROM relay_observation_update_new
          ) AS changed;
    ELSE
        RETURN NULL;  -- unreachable: only INSERT/UPDATE triggers exist
    END IF;

    IF v_changed_min IS NULL OR v_changed_max IS NULL THEN
        RETURN NULL;  -- empty statement: nothing to mark
    END IF;

    v_mark_from := v_changed_min - INTERVAL '60 seconds';
    v_mark_through := GREATEST(v_changed_max + INTERVAL '60 seconds',
                               now() + INTERVAL '60 seconds');

    -- One upsert: the ON CONFLICT branch reads the CURRENT singleton row
    -- under the row lock, so concurrent statements serialize. Columns
    -- omitted from the SET list (covered_*, claim columns, last_error)
    -- pass through unchanged; only dirt bounds and the marker move.
    INSERT INTO public.monitoring_relay_overview_progress (
        id, covered_from, covered_through,
        dirty_from, dirty_through,
        claimed_from, claimed_through,
        dirty_during_claim_from, dirty_generation, last_error
    )
    VALUES (
        TRUE, NULL, NULL,
        v_mark_from, v_mark_through, NULL, NULL,
        NULL, 1, NULL
    )
    ON CONFLICT (id) DO UPDATE SET
        dirty_from = LEAST(
            COALESCE(monitoring_relay_overview_progress.dirty_from,
                     'infinity'::timestamptz),
            COALESCE(EXCLUDED.dirty_from, 'infinity'::timestamptz)),
        dirty_through = GREATEST(
            COALESCE(monitoring_relay_overview_progress.dirty_through,
                     '-infinity'::timestamptz),
            COALESCE(EXCLUDED.dirty_through, '-infinity'::timestamptz),
            COALESCE(monitoring_relay_overview_progress.covered_through,
                     '-infinity'::timestamptz)),
        dirty_during_claim_from = CASE
            WHEN monitoring_relay_overview_progress.claimed_from IS NOT NULL
                 AND monitoring_relay_overview_progress.claimed_through IS NOT NULL
            THEN LEAST(
                COALESCE(monitoring_relay_overview_progress.dirty_during_claim_from,
                         'infinity'::timestamptz),
                COALESCE(EXCLUDED.dirty_from, 'infinity'::timestamptz))
            ELSE NULL END,
        dirty_generation = monitoring_relay_overview_progress.dirty_generation + 1;
    RETURN NULL;  -- AFTER statement trigger return
END;
$trigger$;

DO $trigger_ddl$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trg
         WHERE trg.tgrelid = 'public.relay_observation'::regclass
           AND trg.tgname = 'monitoring_relay_overview_progress_insert_dirty'
           AND NOT trg.tgisinternal
    ) THEN
        CREATE TRIGGER monitoring_relay_overview_progress_insert_dirty
        AFTER INSERT ON public.relay_observation
        REFERENCING NEW TABLE AS relay_observation_inserted
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.monitoring_relay_overview_progress_dirty();
    ELSE
        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger AS trg
             WHERE trg.tgrelid = 'public.relay_observation'::regclass
               AND trg.tgname = 'monitoring_relay_overview_progress_insert_dirty'
               AND NOT trg.tgisinternal
               AND (  trg.tgfoid <> 'public.monitoring_relay_overview_progress_dirty'::regproc
                   OR trg.tgtype <> 4  -- AFTER INSERT, statement-level only
                   OR trg.tgnewtable IS DISTINCT FROM 'relay_observation_inserted' )
        ) THEN
            RAISE EXCEPTION 'monitoring_relay_overview_progress_insert_dirty drifted';
        END IF;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trg
         WHERE trg.tgrelid = 'public.relay_observation'::regclass
           AND trg.tgname = 'monitoring_relay_overview_progress_update_dirty'
           AND NOT trg.tgisinternal
    ) THEN
        CREATE TRIGGER monitoring_relay_overview_progress_update_dirty
        AFTER UPDATE ON public.relay_observation
        REFERENCING OLD TABLE AS relay_observation_update_old
                    NEW TABLE AS relay_observation_update_new
        FOR EACH STATEMENT
        EXECUTE FUNCTION public.monitoring_relay_overview_progress_dirty();
    ELSE
        IF EXISTS (
            SELECT 1 FROM pg_catalog.pg_trigger AS trg
             WHERE trg.tgrelid = 'public.relay_observation'::regclass
               AND trg.tgname = 'monitoring_relay_overview_progress_update_dirty'
               AND NOT trg.tgisinternal
               AND (  trg.tgfoid <> 'public.monitoring_relay_overview_progress_dirty'::regproc
                   OR trg.tgtype <> 16 -- AFTER UPDATE, statement-level only
                   OR trg.tgoldtable IS DISTINCT FROM 'relay_observation_update_old'
                   OR trg.tgnewtable IS DISTINCT FROM 'relay_observation_update_new' )
        ) THEN
            RAISE EXCEPTION 'monitoring_relay_overview_progress_update_dirty drifted';
        END IF;
    END IF;
END
$trigger_ddl$;

---------------------------------------------------------------------
-- §G. public.monitoring_rebuild_relay_overview(job_id INTEGER, config JSONB)
--
-- SECURITY INVOKER, no procedure-level SET clause; TimescaleDB custom jobs
-- call procedures autocommit, so the explicit COMMITs are the checkpoint
-- boundaries the machinery requires. Phases:
--   0. Owner enable flag (malformed => FALSE, fail-closed silent no-op).
--   1. Failure gate via the shared helper. On trip: pause ONLY this job
--      (alter_job scheduled=false; other jobs and policies untouched),
--      record last_error, COMMIT, then raise so scheduler evidence keeps
--      the refusal. The owner's enabled / acknowledged state in config is
--      never rewritten by the procedure.
--   2. Session advisory lock excludes overlapping invocations. It is
--      explicitly released on every catchable exit; backend death clears
--      it automatically (termination-safe).
--   3. Short claim transaction: abandon any stale claim (the exclusive
--      lock proves no invocation is alive) while keeping every dirty
--      bound; pick the claim day = the UTC day of LEAST(dirty_from,
--      covered_through). When pending dirty work sits beyond the covered
--      frontier the frontier day is claimed first, so unverified coverage
--      days are never jumped over; otherwise the earliest dirty instant's
--      day is claimed (re-verification inside/below coverage). Record the
--      claim, pin the source watermark (COALESCE(MAX(observation_id), 0))
--      INSIDE the claim transaction, COMMIT — releasing the singleton row
--      lock so recorder INSERT/COPY writes stay unblocked for the build.
--   4. Build transaction: for each of the 24 UTC hours the exact slices
--      machine runs at the pinned watermark; prior identities become
--      inactive and verified identities are upserted active, including
--      hours becoming all-unknown, without deleting any row.
--      The final short checkpoint re-reads the progress row
--      inside the same transaction; the hour rows, dirty bounds, claim
--      release and coverage upserts commit atomically.
--   5. A catchable build error (including query_canceled 57014) rolls
--      back the subtransaction; the fault/pause path runs OUTSIDE the
--      failed subtransaction so the pause/fault state can commit while
--      dirty and covered state stay exactly as they stood, the claim is
--      released, the job pauses, and the ORIGINAL error re-raises with
--      its SQLSTATE preserved. The session advisory lock is held until
--      BOTH fault checkpoints commit, so a concurrent invocation can
--      never inherit a half-cleared ownership state; on refusal (busy
--      checkpoint or backend teardown) the lock still releases before
--      re-raising. Native max_runtime termination may prevent that
--      path from running — the ack gate on the next invocation
--      handles the ledger instead.
--   6. Worker death after a committed claim leaves a stale claim, which
--      step 3 abandons; no dirty work is ever dropped.
--   7. No CAGG refresh occurs inside the build transaction; the daily
--      materialization keeps its own refresh pacing via the paused policy.
---------------------------------------------------------------------
-- A bulk empty-prefix checkpoint is allowed only when no source observation
-- occurs within 60 seconds of its opening or anywhere before its end, and no
-- old derived row would be skipped. The earliest fact/derived row bounds the
-- proof; this never scans/aggregates an unbounded raw window to invent data.
CREATE OR REPLACE FUNCTION public.monitoring_relay_empty_span_end(
    range_start TIMESTAMPTZ, range_end TIMESTAMPTZ, watermark BIGINT
)
RETURNS TIMESTAMPTZ
LANGUAGE sql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $empty_span$
    WITH source_end AS (
        SELECT date_trunc('day', LEAST(range_end, COALESCE((
            SELECT observed_at - INTERVAL '60 seconds'
              FROM public.relay_observation
             WHERE observation_id <= watermark
               AND observed_at >= range_start - INTERVAL '60 seconds'
             ORDER BY observed_at, observation_id LIMIT 1
        ), range_end)) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' AS through
    )
    SELECT CASE WHEN through <= range_start + INTERVAL '1 day' THEN through
        ELSE date_trunc('day', LEAST(through, COALESCE((
            SELECT bucket FROM public.monitoring_relay_duration_1hour
             WHERE active
               AND bucket >= range_start ORDER BY bucket LIMIT 1
        ), through)) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
    END FROM source_end
$empty_span$;

CREATE OR REPLACE PROCEDURE public.monitoring_rebuild_relay_overview(
    job_id INTEGER,
    config JSONB
)
LANGUAGE plpgsql
SECURITY INVOKER
AS $procedure$
DECLARE
    v_cfg            JSONB := COALESCE(config, '{}'::JSONB);
    v_enabled        BOOLEAN := FALSE;
    v_locked         BOOLEAN;
    v_prior          public.monitoring_relay_overview_progress%ROWTYPE;
    v_watermark      BIGINT;
    v_claim_from     TIMESTAMPTZ;
    v_claim_through  TIMESTAMPTZ;
    v_empty_through  TIMESTAMPTZ;
    v_hour_start     TIMESTAMPTZ;
    v_hour_end       TIMESTAMPTZ;
    v_hour_index     INTEGER;
    v_cvg_from       TIMESTAMPTZ;
    v_cvg_through    TIMESTAMPTZ;
    v_daily_from     TIMESTAMPTZ;
    v_daily_through  TIMESTAMPTZ;
    v_daily_front    TIMESTAMPTZ;
    v_daily_ok       BOOLEAN;
    v_walk_i         INTEGER;
    v_build_failed   BOOLEAN := FALSE;
    v_fault_sqlstate TEXT;
    v_fault_message  TEXT;

    v_lock_key BIGINT := hashtextextended('projectcea.relay-overview-rebuild', 0);
BEGIN
    IF job_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM timescaledb_information.jobs AS owned
        WHERE owned.job_id = monitoring_rebuild_relay_overview.job_id
          AND owned.proc_schema = 'public'
          AND owned.proc_name = 'monitoring_rebuild_relay_overview'
    ) THEN
        RAISE EXCEPTION 'relay rebuild job identity does not match this procedure';
    END IF;
    -----------------------------------------------------------------
    -- 0. Owner enable flag.
    -----------------------------------------------------------------
    IF jsonb_typeof(v_cfg -> 'enabled') = 'boolean' THEN
        v_enabled := (v_cfg -> 'enabled')::BOOLEAN;
    END IF;
    IF v_enabled IS NOT TRUE THEN
        RETURN;  -- owner-disabled: silent no-op, no fault, no pause
    END IF;

    -----------------------------------------------------------------
    -- 1. Failure gate (shared helper).
    -----------------------------------------------------------------
    IF public.monitoring_overview_unacknowledged_failures(v_cfg) THEN
        IF job_id IS NOT NULL THEN
            PERFORM alter_job(job_id, scheduled => FALSE);
        END IF;
        UPDATE public.monitoring_relay_overview_progress
           SET last_error = 'unacknowledged job failures: paused pending owner acknowledgement'
         WHERE id;
        COMMIT;
        RAISE EXCEPTION
            'monitoring_rebuild_relay_overview: unacknowledged job failures; paused pending owner acknowledgement';
    END IF;

    -----------------------------------------------------------------
    -- 2. Exclusive session advisory lock.
    --    The invocation holds it until BOTH fault checkpoints commit or
    --    the successful path releases it, so a concurrent invocation can
    --    never inherit a half-cleared claim while a previous one's
    --    cleanup transaction is still pending release.
    -----------------------------------------------------------------
    v_locked := pg_try_advisory_lock(v_lock_key);
    IF NOT v_locked THEN
        RETURN;  -- another invocation owns the rebuild path
    END IF;

    -----------------------------------------------------------------
    -- 3. Short claim transaction.
    -----------------------------------------------------------------
    SET LOCAL lock_timeout = '1s';

    BEGIN
        SELECT * INTO v_prior
          FROM public.monitoring_relay_overview_progress WHERE id FOR UPDATE;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'monitoring_relay_overview_progress singleton is missing';
        END IF;

        IF v_prior.claimed_through IS NOT NULL THEN
            -- The session lock proves the previous invocation is gone.
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL, claimed_through = NULL,
                   dirty_during_claim_from = NULL
             WHERE id;
        END IF;

        IF v_prior.dirty_from IS NOT NULL
           AND v_prior.dirty_through IS NOT NULL
           AND v_prior.dirty_from < v_prior.dirty_through THEN
            v_claim_from := date_trunc('day',
                LEAST(v_prior.dirty_from,
                      COALESCE(v_prior.covered_through, v_prior.dirty_from))
                AT TIME ZONE 'UTC') AT TIME ZONE 'UTC';
            SELECT COALESCE(MAX(observation_id), 0) INTO v_watermark
              FROM public.relay_observation;
            v_empty_through := public.monitoring_relay_empty_span_end(
                v_claim_from, v_prior.dirty_through, v_watermark);
            IF v_empty_through > v_claim_from + INTERVAL '1 day' THEN
                IF v_prior.covered_from IS NOT NULL
                   AND v_empty_through < v_prior.covered_from THEN
                    v_cvg_from := v_claim_from;
                    v_cvg_through := v_empty_through;
                    v_prior.dirty_through := GREATEST(
                        v_prior.dirty_through, v_prior.covered_through);
                ELSE
                    v_cvg_from := LEAST(v_prior.covered_from, v_claim_from);
                    v_cvg_through := GREATEST(v_prior.covered_through, v_empty_through);
                END IF;
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from, covered_through = v_cvg_through,
                       dirty_from = CASE WHEN v_prior.dirty_through > v_empty_through
                                         THEN v_empty_through END,
                       dirty_through = CASE WHEN v_prior.dirty_through > v_empty_through
                                            THEN v_prior.dirty_through END,
                       last_error = NULL
                 WHERE id;
                INSERT INTO public.monitoring_overview_coverage
                    (model_name, covered_from, covered_through)
                VALUES ('monitoring_relay_duration_1hour', v_cvg_from, v_cvg_through)
                ON CONFLICT (model_name) DO UPDATE
                    SET covered_from = EXCLUDED.covered_from,
                        covered_through = EXCLUDED.covered_through;
                -- Verified empty coverage, not a multi-day build claim.
                v_claim_from := NULL;
            ELSIF v_claim_from + INTERVAL '24 hours' <=
                  date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' THEN
                v_claim_through := v_claim_from + INTERVAL '24 hours';
                UPDATE public.monitoring_relay_overview_progress
                   SET claimed_from = v_claim_from, claimed_through = v_claim_through,
                       dirty_during_claim_from = NULL
                 WHERE id;
            ELSE
                -- Leave the unfinished UTC day to the exact bounded raw tail.
                -- Heartbeats inside it must not dirty materialized future time.
                v_claim_from := NULL;
                v_cvg_from := v_prior.covered_from;
                v_cvg_through := v_prior.covered_through;
            END IF;
        ELSE
            -- A separate daily refresh may have finished after the last build.
            v_cvg_from := v_prior.covered_from;
            v_cvg_through := v_prior.covered_through;
        END IF;
    EXCEPTION WHEN query_canceled OR OTHERS THEN
        GET STACKED DIAGNOSTICS v_fault_sqlstate = RETURNED_SQLSTATE,
                                v_fault_message = MESSAGE_TEXT;
        v_build_failed := TRUE;
    END;

    IF v_build_failed THEN
        -- Failure checkpoint with ownership retained until both commits
        -- have landed: the job pause and the fault metadata write happen
        -- BEFORE the session advisory lock is released, so a concurrent
        -- next invocation never observes a stale claim mid-cleanup. If a
        -- commit is refused (e.g. a busy row or external termination) the
        -- lock releases before the ORIGINAL SQLSTATE re-raises, matching
        -- the previous exception-safe unlock semantics.
        BEGIN
            IF job_id IS NOT NULL THEN
                PERFORM alter_job(job_id, scheduled => FALSE);
            END IF;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- outside the exception subtransaction; ownership retained
        BEGIN
            SET LOCAL lock_timeout = '1s';
            UPDATE public.monitoring_relay_overview_progress
               SET last_error = v_fault_sqlstate || ': ' || v_fault_message
             WHERE id;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- fault metadata landed before ownership drops
        PERFORM pg_advisory_unlock(v_lock_key);
        RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                              MESSAGE = v_fault_message;
    END IF;

    COMMIT;  -- claim committed; the singleton row lock releases here

    -----------------------------------------------------------------
    -- 4. Build transaction (one claim day).
    -----------------------------------------------------------------
    SET LOCAL lock_timeout = '1s';

    BEGIN  -- subtransaction: the whole day rolls back on any catchable error
        IF v_claim_from IS NOT NULL THEN
        -- Additive rewrite of the claimed day: mark the claimed span's
        -- prior rows inactive first, then upsert the verified snapshot's
        -- own hour rows back to active, so no derived row or index is ever
        -- deleted. Inactive rows are ignored by the daily continuous
        -- aggregate and by the parent's API readers (unknown time), and
        -- unknown-only legitimate lanes stay active exactly as before.
        UPDATE public.monitoring_relay_duration_1hour AS prior_row
           SET active = FALSE
         WHERE prior_row.bucket >= v_claim_from
           AND prior_row.bucket < v_claim_through;

        FOR v_hour_index IN 0..23 LOOP
            v_hour_start := v_claim_from + make_interval(hours => v_hour_index);
            v_hour_end := v_hour_start + INTERVAL '1 hour';

            -- Sum integer-microsecond slice lengths. Unknown-only identities
            -- survive only when a qualified owner was actually recorded in
            -- this hour; no inactive owner is filled across unrecorded years.
            INSERT INTO public.monitoring_relay_duration_1hour AS rebuilt
                (bucket, location, cluster, channel, device_id, device_name,
                 device_type, on_microseconds, off_microseconds,
                 last_observed_at, source_watermark, active)
            SELECT derived.bucket,
                   derived.location, derived.cluster, derived.channel,
                   derived.device_id, derived.device_name, derived.device_type,
                   derived.on_us, derived.off_us,
                   owner_at.last_observed_at, v_watermark, TRUE
              FROM (
                SELECT v_hour_start AS bucket,
                       slices.location, slices.cluster, slices.channel,
                       slices.device_id, slices.device_name, slices.device_type,
                       COALESCE(sum(
                           (extract(epoch FROM slices.end_at - slices.start_at)
                            * 1000000)::BIGINT)
                           FILTER (WHERE slices.state = 'on'), 0)::BIGINT AS on_us,
                       COALESCE(sum(
                           (extract(epoch FROM slices.end_at - slices.start_at)
                            * 1000000)::BIGINT)
                           FILTER (WHERE slices.state = 'off'), 0)::BIGINT AS off_us
                  FROM public.monitoring_relay_duration_slices(
                           v_hour_start, v_hour_end, v_watermark) AS slices
                 GROUP BY 1, 2, 3, 4, 5, 6, 7
              ) AS derived
              JOIN LATERAL (
                SELECT max(ro.observed_at) AS last_observed_at
                  FROM public.relay_observation AS ro
                 WHERE ro.observation_id <= v_watermark
                   AND ro.observed_at < v_hour_end
                   AND ro.location = derived.location
                   AND ro.cluster = derived.cluster
                   AND ro.channel = derived.channel
                   AND ro.device_id = derived.device_id
                   AND ro.device_name = derived.device_name
                   AND ro.device_type = derived.device_type
              ) AS owner_at ON TRUE
             WHERE derived.on_us > 0 OR derived.off_us > 0
                OR owner_at.last_observed_at >= v_hour_start
             ON CONFLICT (bucket, location, cluster, channel, device_id,
                          device_name, device_type) DO UPDATE SET
                    on_microseconds  = EXCLUDED.on_microseconds,
                    off_microseconds = EXCLUDED.off_microseconds,
                    last_observed_at = EXCLUDED.last_observed_at,
                    source_watermark = EXCLUDED.source_watermark,
                    active           = TRUE;
        END LOOP;

        -----------------------------------------------------------------
        -- Final short checkpoint (same transaction as the hour rows).
        -----------------------------------------------------------------
        SELECT * INTO v_prior FROM public.monitoring_relay_overview_progress WHERE id FOR UPDATE;

        IF v_prior.claimed_from IS NULL
           OR v_prior.claimed_through IS NULL
           OR v_prior.claimed_from <> v_claim_from
           OR v_prior.claimed_through <> v_claim_through THEN
            RAISE EXCEPTION 'relay overview claim state changed under the build transaction';
        END IF;

        IF v_prior.dirty_during_claim_from IS NOT NULL
           AND v_prior.dirty_during_claim_from < v_claim_through THEN
            -- Overlapping dirt entered the claimed span during the build:
            -- retain the earliest dirty work, advance no coverage, release
            -- the claim; the claim day stays pending for a verifiably
            -- clean pass.
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL,
                   claimed_through = NULL,
                   dirty_during_claim_from = NULL,
                   last_error = NULL
             WHERE id;
        ELSE
            -- Clean checkpoint: coverage advances through the verified
            -- claim day (contiguity holds because claims run at the covered
            -- frontier or inside verified range where GREATEST is a no-op);
            -- the claim day is consumed from the dirty bounds; dirt beyond
            -- it stays pending.
            IF v_prior.covered_from IS NOT NULL
               AND v_claim_through < v_prior.covered_from THEN
                -- Restart a verified prefix, retaining dirt through the old
                -- frontier. Never declare the intervening gap covered.
                v_cvg_from := v_claim_from;
                v_cvg_through := v_claim_through;
                v_prior.dirty_through := GREATEST(
                    v_prior.dirty_through, v_prior.covered_through);
            ELSE
                v_cvg_from := LEAST(v_prior.covered_from, v_claim_from);
                v_cvg_through := GREATEST(v_prior.covered_through, v_claim_through);
            END IF;

            IF v_prior.dirty_through > v_claim_through THEN
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from,
                       covered_through = v_cvg_through,
                       dirty_from = v_claim_through,
                       dirty_through = v_prior.dirty_through,
                       claimed_from = NULL,
                       claimed_through = NULL,
                       dirty_during_claim_from = NULL,
                       last_error = NULL
                 WHERE id;
            ELSE
                UPDATE public.monitoring_relay_overview_progress
                   SET covered_from = v_cvg_from,
                       covered_through = v_cvg_through,
                       dirty_from = NULL,
                       dirty_through = NULL,
                       claimed_from = NULL,
                       claimed_through = NULL,
                       dirty_during_claim_from = NULL,
                       last_error = NULL
                 WHERE id;
            END IF;

            INSERT INTO public.monitoring_overview_coverage
                (model_name, covered_from, covered_through)
            VALUES ('monitoring_relay_duration_1hour', v_cvg_from, v_cvg_through)
            ON CONFLICT (model_name) DO UPDATE
                SET covered_from = EXCLUDED.covered_from,
                    covered_through = EXCLUDED.covered_through;

        END IF;
        END IF;
            -----------------------------------------------------------------
            -- Daily coverage verification (read-only against the daily
            -- materialization; NO refresh happens here). The daily frontier
            -- advances only across days whose hourly rows are covered AND
            -- whose daily materialization rows equal those hourly rows
            -- exactly (symmetric EXCEPT ALL: additions to either side prove
            -- staleness; verified-empty days compare as equal absences —
            -- their "no invented owner" emptiness was proven by the very
            -- machine runs that advanced the hourly frontier). The walk is
            -- capped so the checkpoint stays short; a paused daily policy
            -- means the materialization matches only what refreshes have
            -- processed so far, so an unrefreshed day stalls the walk.
            -----------------------------------------------------------------
        IF v_cvg_from IS NOT NULL AND v_cvg_through IS NOT NULL THEN
            SELECT covered_from, covered_through
              INTO v_daily_from, v_daily_through
              FROM public.monitoring_overview_coverage
             WHERE model_name = 'monitoring_relay_duration_1day';

            IF v_daily_from IS NOT NULL AND v_daily_from <> v_cvg_from THEN
                -- An earlier verified prefix temporarily supersedes the old
                -- daily coverage; its gap must be refreshed and checked too.
                UPDATE public.monitoring_overview_coverage
                   SET covered_from = v_cvg_from, covered_through = v_cvg_from
                 WHERE model_name = 'monitoring_relay_duration_1day';
                v_daily_through := NULL;
            END IF;

            v_daily_front := COALESCE(v_daily_through, v_cvg_from);

            FOR v_walk_i IN 1..24 LOOP
                EXIT WHEN (v_daily_front + INTERVAL '1 day') > v_cvg_through;
                EXIT WHEN NOT EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = 'monitoring_relay_duration_1day'
                       AND materialization_watermark >= v_daily_front + INTERVAL '1 day');
                EXIT WHEN EXISTS (
                    SELECT 1 FROM public.monitoring_cagg_watermark
                     WHERE cagg_name = 'monitoring_relay_duration_1day'
                       AND invalidation_start < v_daily_front + INTERVAL '1 day'
                       AND invalidation_end >= v_daily_front);

                SELECT NOT EXISTS (
                    (SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            sum(prior_row.on_microseconds)::BIGINT,
                            sum(prior_row.off_microseconds)::BIGINT,
                            max(prior_row.last_observed_at),
                            max(prior_row.source_watermark)
                       FROM public.monitoring_relay_duration_1hour AS prior_row
                      WHERE prior_row.active
                        AND prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '1 day'
                      GROUP BY 1, 2, 3, 4, 5, 6
                     EXCEPT ALL
                     SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            prior_row.on_microseconds,
                            prior_row.off_microseconds,
                            prior_row.last_observed_at,
                            prior_row.source_watermark
                       FROM public.monitoring_relay_duration_1day AS prior_row
                      WHERE prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '1 day')
                    UNION ALL
                    (SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            prior_row.on_microseconds,
                            prior_row.off_microseconds,
                            prior_row.last_observed_at,
                            prior_row.source_watermark
                       FROM public.monitoring_relay_duration_1day AS prior_row
                      WHERE prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '1 day'
                     EXCEPT ALL
                     SELECT prior_row.location, prior_row.cluster,
                            prior_row.channel, prior_row.device_id,
                            prior_row.device_name, prior_row.device_type,
                            sum(prior_row.on_microseconds)::BIGINT,
                            sum(prior_row.off_microseconds)::BIGINT,
                            max(prior_row.last_observed_at),
                            max(prior_row.source_watermark)
                       FROM public.monitoring_relay_duration_1hour AS prior_row
                      WHERE prior_row.active
                        AND prior_row.bucket >= v_daily_front
                        AND prior_row.bucket < v_daily_front + INTERVAL '1 day'
                      GROUP BY 1, 2, 3, 4, 5, 6)
                ) INTO v_daily_ok;

                IF v_daily_ok IS NOT TRUE THEN
                    EXIT;
                END IF;

                v_daily_front := v_daily_front + INTERVAL '1 day';
            END LOOP;

            IF v_daily_front > v_cvg_from THEN
                INSERT INTO public.monitoring_overview_coverage
                    (model_name, covered_from, covered_through)
                VALUES ('monitoring_relay_duration_1day', v_cvg_from, v_daily_front)
                ON CONFLICT (model_name) DO UPDATE
                    SET covered_from = EXCLUDED.covered_from,
                        covered_through = EXCLUDED.covered_through;
            END IF;
        END IF;
    EXCEPTION
        WHEN query_canceled OR OTHERS THEN
            GET STACKED DIAGNOSTICS v_fault_sqlstate = RETURNED_SQLSTATE,
                                    v_fault_message = MESSAGE_TEXT;
            v_build_failed := TRUE;
    END;

    -----------------------------------------------------------------
    -- 5. Failure path: runs OUTSIDE the failed subtransaction so the
    -- pause/fault commit lands even though the build did not. Dirty and
    -- covered columns are untouched (the subtransaction already returned
    -- them); the claim releases together with the recorded error, and the
    -- advisory lock is relinquished only after both fault commits so a
    -- concurrent invocation can never inherit a half-cleared claim.
    -----------------------------------------------------------------
    IF v_build_failed THEN
        -----------------------------------------------------------------
        -- Ownership stays with THIS invocation through both commits so a
        -- concurrent next invocation observes a consistent pause state
        -- instead of inheriting a stale claim. If either commit cannot be
        -- completed the advisory lock is released before re-raising the
        -- original error, replicating the pre-existing semantics (the
        -- claim remains abandoned-but-present, no coverage advance, and
        -- the hash-lock dependency remains symmetrical for the next
        -- session that takes it).
        -----------------------------------------------------------------
        BEGIN
            IF job_id IS NOT NULL THEN
                PERFORM alter_job(job_id, scheduled => FALSE);
            END IF;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- outside the exception subtransaction; ownership retained
        BEGIN
            SET LOCAL lock_timeout = '1s';
            UPDATE public.monitoring_relay_overview_progress
               SET claimed_from = NULL,
                   claimed_through = NULL,
                   dirty_during_claim_from = NULL,
                   last_error = v_fault_sqlstate || ': ' || v_fault_message
             WHERE id;
        EXCEPTION WHEN query_canceled OR OTHERS THEN
            PERFORM pg_advisory_unlock(v_lock_key);
            RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                                  MESSAGE = v_fault_message;
        END;
        COMMIT;  -- fault metadata and claim release landed before ownership drops
        PERFORM pg_advisory_unlock(v_lock_key);
        RAISE EXCEPTION USING ERRCODE = v_fault_sqlstate,
                              MESSAGE = v_fault_message;
    END IF;

    -----------------------------------------------------------------
    -- Success: commit the build + checkpoint, release the advisory lock.
    -----------------------------------------------------------------
    COMMIT;
    PERFORM pg_advisory_unlock(v_lock_key);
END;
$procedure$;

COMMENT ON PROCEDURE public.monitoring_rebuild_relay_overview(INTEGER, JSONB) IS
'Pausable relay duration rebuild: one UTC day claim + pinned observation_id
watermark per invocation; the 24 hour slices run from the exact support
machine at that watermark; the checkpoint commits hourly rows, dirty bounds
and coverage rows atomically; unacknowledged failures gate before work and
pause only this job (scheduled=false) with the owner config preserved.';

---------------------------------------------------------------------
-- §H. Readiness SQL interface for the parent/API.
--
-- true iff the half-open [required_from, required_through) lies fully
-- within the verified relay hourly coverage AND no pending dirty work
-- overlaps it (dirt inside verified history means the range is being
-- re-verified and must read as unavailable, never as stale success).
-- Daily readiness combines this with the daily coverage row plus the
-- native per-bucket daily invalidation state read by the parent/API.
---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.monitoring_relay_overview_hourly_ready(
    required_from    TIMESTAMPTZ,
    required_through TIMESTAMPTZ
)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $ready$
    SELECT required_from IS NOT NULL
       AND required_through IS NOT NULL
       AND required_from <= required_through
       AND EXISTS (
            SELECT 1
              FROM public.monitoring_overview_coverage
             WHERE model_name = 'monitoring_relay_duration_1hour'
               AND covered_from <= required_from
               AND covered_through >= required_through
       )
       AND NOT EXISTS (
            SELECT 1
              FROM public.monitoring_relay_overview_progress
             WHERE dirty_from IS NOT NULL
               AND dirty_through IS NOT NULL
               AND dirty_from < required_through
               AND dirty_through > required_from
       )
$ready$;

COMMENT ON FUNCTION public.monitoring_relay_overview_hourly_ready(TIMESTAMPTZ, TIMESTAMPTZ) IS
'Coverage/readiness gate for the relay duration hourly tier: verified
contiguous coverage must contain the requested span and no pending dirty
work may overlap it; the daily tier additionally consults the daily
coverage row plus native daily invalidation state.';

---------------------------------------------------------------------
-- §I. Paused daily refresh policy for monitoring_relay_duration_1day.
-- start_offset NULL (all history eligible), end_offset = the daily source
-- width so only settled days refresh, buckets_per_batch = 1 and
-- max_batches_per_execution = 1 (one UTC day per execution), oldest first.
-- The job runs hourly with max_runtime 60 s / max_retries 0 and starts
-- scheduled=false. On repeat the config is verified and owner scheduled
-- state is preserved.
---------------------------------------------------------------------
DO $daily_policy$
DECLARE
    v_mat_hypertable_id BIGINT;
    v_job_id            INTEGER;
    v_drifted           BOOLEAN;
BEGIN
    SELECT mat_hypertable_id INTO v_mat_hypertable_id
      FROM _timescaledb_catalog.continuous_agg
     WHERE user_view_name = 'monitoring_relay_duration_1day'
       AND user_view_schema = 'public';
    IF v_mat_hypertable_id IS NULL THEN
        RAISE EXCEPTION 'monitoring_relay_duration_1day continuous aggregate is missing';
    END IF;

    SELECT count(*) > 1 INTO v_drifted
      FROM timescaledb_information.jobs
     WHERE proc_name = 'policy_refresh_continuous_aggregate'
       AND proc_schema = '_timescaledb_functions'
       AND hypertable_schema = 'public'
       AND hypertable_name = 'monitoring_relay_duration_1day';
    IF v_drifted THEN
        RAISE EXCEPTION 'duplicate refresh policy for monitoring_relay_duration_1day';
    END IF;

    IF EXISTS (
        SELECT 1 FROM timescaledb_information.jobs
         WHERE proc_name = 'policy_refresh_continuous_aggregate'
           AND proc_schema = '_timescaledb_functions'
           AND hypertable_schema = 'public'
           AND hypertable_name = 'monitoring_relay_duration_1day'
    ) THEN
        SELECT j.job_id,
               NOT (
                    j.schedule_interval = INTERVAL '1 hour'
                AND j.config ? 'mat_hypertable_id'
                AND (j.config ->> 'mat_hypertable_id')::BIGINT = v_mat_hypertable_id
                AND jsonb_typeof(j.config -> 'start_offset') = 'null'
                AND (j.config ->> 'end_offset')::INTERVAL IS NOT DISTINCT FROM INTERVAL '1 day'
                AND (j.config ->> 'buckets_per_batch')::INTEGER = 1
                AND (j.config ->> 'max_batches_per_execution')::INTEGER = 1
                AND (j.config -> 'refresh_newest_first') IS NOT DISTINCT FROM 'false'::JSONB
                AND NOT j.config ? 'include_tiered_data'
                AND NOT j.config ? 'compress_after_refresh'
                AND j.max_runtime = INTERVAL '60 seconds'
                AND j.max_retries = 0
               )
          INTO v_job_id, v_drifted
          FROM timescaledb_information.jobs AS j
         WHERE j.proc_name = 'policy_refresh_continuous_aggregate'
           AND j.proc_schema = '_timescaledb_functions'
           AND j.hypertable_schema = 'public'
           AND j.hypertable_name = 'monitoring_relay_duration_1day';
        IF v_drifted THEN
            RAISE EXCEPTION 'monitoring_relay_duration_1day refresh policy drifted; no repair performed';
        END IF;
    ELSE
        v_job_id := add_continuous_aggregate_policy(
            continuous_aggregate => 'monitoring_relay_duration_1day'::regclass,
            start_offset => NULL::INTERVAL,
            end_offset => INTERVAL '1 day',
            schedule_interval => INTERVAL '1 hour',
            if_not_exists => FALSE,
            buckets_per_batch => 1,
            max_batches_per_execution => 1,
            refresh_newest_first => FALSE);
        IF v_job_id IS NULL THEN
            RAISE EXCEPTION 'add_continuous_aggregate_policy returned no job id';
        END IF;
        PERFORM alter_job(v_job_id,
                          max_runtime => INTERVAL '60 seconds',
                          max_retries => 0,
                          scheduled => FALSE);
    END IF;
END
$daily_policy$;

---------------------------------------------------------------------
-- §J. Custom paused rebuild job. Config is exactly the owner triple
-- {"enabled":false,"acknowledged_failures":{}} at creation; on repeat the
-- procedure/schedule/config shapes are verified and the owner enabled /
-- scheduled / acknowledged state is preserved verbatim.
---------------------------------------------------------------------
DO $rebuild_job$
DECLARE
    v_job_id  INTEGER;
    v_drifted BOOLEAN;
BEGIN
    SELECT count(*) > 1 INTO v_drifted
      FROM timescaledb_information.jobs
     WHERE proc_schema = 'public'
       AND proc_name = 'monitoring_rebuild_relay_overview';
    IF v_drifted THEN
        RAISE EXCEPTION 'duplicate custom job for monitoring_rebuild_relay_overview';
    END IF;

    IF EXISTS (
        SELECT 1 FROM timescaledb_information.jobs
         WHERE proc_schema = 'public'
           AND proc_name = 'monitoring_rebuild_relay_overview'
    ) THEN
        SELECT job_id,
               (
                    schedule_interval = INTERVAL '5 minutes'
                AND max_runtime = INTERVAL '60 seconds'
                AND max_retries = 0
                AND jsonb_typeof(config -> 'enabled') = 'boolean'
                AND jsonb_typeof(config -> 'acknowledged_failures') = 'object'
               ) IS DISTINCT FROM TRUE
          INTO v_job_id, v_drifted
          FROM timescaledb_information.jobs
         WHERE proc_schema = 'public'
           AND proc_name = 'monitoring_rebuild_relay_overview';
        IF v_drifted THEN
            RAISE EXCEPTION 'monitoring_rebuild_relay_overview job drifted; no repair performed';
        END IF;
        PERFORM public.monitoring_overview_unacknowledged_failures(
            (SELECT config FROM timescaledb_information.jobs WHERE job_id = v_job_id));
        -- owner enabled / scheduled / acknowledged state preserved verbatim
    ELSE
        v_job_id := add_job(
            proc => 'public.monitoring_rebuild_relay_overview(integer, jsonb)'::regprocedure::regproc,
            schedule_interval => INTERVAL '5 minutes',
            config => jsonb_build_object(
                'enabled', FALSE,
                'acknowledged_failures', '{}'::JSONB),
            initial_start => NULL,
            scheduled => FALSE,
            job_name => 'monitoring_rebuild_relay_overview');
        IF v_job_id IS NULL THEN
            RAISE EXCEPTION 'add_job returned no job id';
        END IF;
        PERFORM alter_job(v_job_id,
                          max_runtime => INTERVAL '60 seconds',
                          max_retries => 0,
                          scheduled => FALSE);
    END IF;
END
$rebuild_job$;

---------------------------------------------------------------------
-- §K. Revoke PUBLIC execution of the mutating procedure. The owner and the
-- job runner keep EXECUTE via ownership; the read-only slice and readiness
-- helpers remain PUBLIC-readable for monitoring/API roles.
---------------------------------------------------------------------
REVOKE EXECUTE ON PROCEDURE public.monitoring_rebuild_relay_overview(INTEGER, JSONB) FROM PUBLIC;
