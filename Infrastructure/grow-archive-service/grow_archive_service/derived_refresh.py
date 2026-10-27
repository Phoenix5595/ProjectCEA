"""Repair source-dependent summaries after verified source mutation.

preflight is read-only apart from temporary session advisory locks. It fails
closed on unknown dependency edges, missing expected readers, non-default
buckets, missing owners or invoker privileges. It never enables a job or
alters grants.

Relay hourly rows are a procedural dependency, not a CAGG. Archive DELETEs
do not dirty the existing global rebuild job. Repair their affected hours
explicitly using that job's exact slice and recorded-owner SELECT, then
refresh native CAGGs in dependency order with standalone top-level CALLs.
Coverage and archive_availability are not advanced by this module.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Final

import asyncpg

from .derived_refresh_preflight import PreflightMixin as _PreflightMixin
from .derived_refresh_support import (
    AggregateDependency,
    DAY,
    HOUR,
    KNOWN,
    RELAY,
    RELAY_LOCK,
    _sources,
    _try_lock,
    _unlock,
)
from .execution import HeavyWorkCancelled, HeavyWorkExecutor
from .models import ArchiveError


MAINTENANCE_LOCK: Final = "sj-cea-derived-history-refresh"


_FRESHNESS: Final = """
WITH c AS (
    SELECT mat_hypertable_id, raw_hypertable_id
      FROM _timescaledb_catalog.continuous_agg
     WHERE user_view_schema = $1 AND user_view_name = $2
), invalidations AS (
    SELECT l.lowest_modified_value AS lo, l.greatest_modified_value AS hi
      FROM c JOIN _timescaledb_catalog.continuous_aggs_materialization_invalidation_log l
        ON l.materialization_id = c.mat_hypertable_id
    UNION ALL
    SELECT l.lowest_modified_value, l.greatest_modified_value
      FROM c JOIN _timescaledb_catalog.continuous_aggs_hypertable_invalidation_log l
        ON l.hypertable_id = c.raw_hypertable_id
), spans AS (
    SELECT CASE WHEN lo < -210866803200000000::bigint THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(lo) END AS lo,
           CASE WHEN hi < -210866803200000000::bigint THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(hi) END AS hi
      FROM invalidations
), watermark AS (
    -- The empty-CAGG sentinel and far-future values are not representable
    -- in Python; compare in SQL and return only the finite clamped tail start.
    SELECT CASE WHEN _timescaledb_functions.cagg_watermark(c.mat_hypertable_id) < -210866803200000000::bigint
                THEN '-infinity'::timestamptz
                ELSE _timescaledb_functions.to_timestamp(_timescaledb_functions.cagg_watermark(c.mat_hypertable_id))
           END AS at
      FROM c
)
SELECT w.at >= $4::timestamptz AS covered,
       greatest($3::timestamptz, least($4::timestamptz, w.at)) AS tail_start,
       EXISTS (SELECT 1 FROM spans WHERE lo < $4::timestamptz AND hi >= $3::timestamptz) AS pending
  FROM watermark w
"""

_RELAY_COLUMNS: Final = (
    "bucket,location,cluster,channel,device_id,device_name,device_type,"
    "on_microseconds,off_microseconds,last_observed_at,source_watermark,active"
)
# This is the per-hour build SELECT in monitoring_rebuild_relay_overview,
# including its unknown-only lane and last-recorded-owner qualification.
_RELAY_BUILD: Final = """
INSERT INTO pg_temp.archive_relay_hour_repair
    (bucket,location,cluster,channel,device_id,device_name,device_type,
     on_microseconds,off_microseconds,last_observed_at,source_watermark,active)
SELECT $1::timestamptz, d.location,d.cluster,d.channel,d.device_id,d.device_name,d.device_type,
       d.on_us,d.off_us,owner_at.last_observed_at,$3::bigint,true
  FROM (
      SELECT s.location,s.cluster,s.channel,s.device_id,s.device_name,s.device_type,
             coalesce(sum((extract(epoch FROM s.end_at-s.start_at)*1000000)::bigint)
                      FILTER (WHERE s.state='on'),0)::bigint AS on_us,
             coalesce(sum((extract(epoch FROM s.end_at-s.start_at)*1000000)::bigint)
                      FILTER (WHERE s.state='off'),0)::bigint AS off_us
        FROM public.monitoring_relay_duration_slices($1::timestamptz,$2::timestamptz,$3::bigint) s
       GROUP BY s.location,s.cluster,s.channel,s.device_id,s.device_name,s.device_type
  ) d JOIN LATERAL (
      SELECT max(ro."time") AS last_observed_at
        FROM public.relay_observation ro
       WHERE ro.observation_id <= $3::bigint AND ro."time" < $2::timestamptz
         AND ro.location=d.location AND ro.cluster=d.cluster AND ro.channel=d.channel
         AND ro.device_id=d.device_id AND ro.device_name=d.device_name AND ro.device_type=d.device_type
  ) owner_at ON true
 WHERE d.on_us>0 OR d.off_us>0 OR owner_at.last_observed_at >= $1::timestamptz
"""
_RELAY_UPSERT: Final = (
    f"INSERT INTO public.monitoring_relay_duration_1hour ({_RELAY_COLUMNS}) "
    f"SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair "
    "ON CONFLICT (bucket,location,cluster,channel,device_id,device_name,device_type) "
    "DO UPDATE SET on_microseconds=EXCLUDED.on_microseconds,"
    "off_microseconds=EXCLUDED.off_microseconds,last_observed_at=EXCLUDED.last_observed_at,"
    "source_watermark=EXCLUDED.source_watermark,active=true"
)
_RELAY_PARITY: Final = (
    "SELECT NOT EXISTS ("
    f"(SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair EXCEPT ALL "
    f"SELECT {_RELAY_COLUMNS} FROM public.monitoring_relay_duration_1hour "
    "WHERE bucket=$1::timestamptz AND active) UNION ALL "
    f"(SELECT {_RELAY_COLUMNS} FROM public.monitoring_relay_duration_1hour "
    "WHERE bucket=$1::timestamptz AND active EXCEPT ALL "
    f"SELECT {_RELAY_COLUMNS} FROM pg_temp.archive_relay_hour_repair))"
)


class DerivedHistoryMaintenance(_PreflightMixin):
    def __init__(self, pool: asyncpg.Pool, heavy_work: HeavyWorkExecutor) -> None:
        self.pool = pool
        self.heavy_work = heavy_work
        self._lock = asyncio.Lock()

    async def refresh(
        self,
        source_table: str,
        room: str | None,
        start: datetime,
        end: datetime,
    ) -> None:
        """Repair committed changes; leave availability closed on every failure.

        Bounds are half-open actual changed observation instants. CAGGs are
        not room filtered. Relay replacement is also global per hour because
        channel ownership and heartbeat support can cross rooms.
        """
        sources = _sources((source_table,))
        if any(v.tzinfo is None or v.utcoffset() is None for v in (start, end)) or end <= start:
            raise ArchiveError("archive_derived_invalid_window", status_code=400)
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        _ = room
        try:
            await self._checkpoint()
            async with self._lock, self.pool.acquire() as conn:
                if not await _try_lock(conn, MAINTENANCE_LOCK):
                    raise ArchiveError("archive_derived_refresh_busy", status_code=503)
                relay_locked = False
                try:
                    if source_table == RELAY:
                        relay_locked = await _try_lock(conn, RELAY_LOCK)
                        if not relay_locked:
                            raise ArchiveError("archive_derived_refresh_busy", status_code=503)
                    async with conn.transaction(isolation="repeatable_read", readonly=True):
                        plan = await self._inspect(conn, sources)
                    if source_table in plan.absent_sources:
                        raise ArchiveError("archive_derived_source_missing")
                    lower, upper = start, end
                    if source_table == RELAY:
                        lower, upper = await self._relay_bounds(conn, start, end)
                        await self._relay_quiet(conn, lower, upper)
                        for lo, hi in _chunks(lower, upper, HOUR, maximum_span=HOUR):
                            await self._repair_relay_hour(conn, lo, hi)
                    if plan.dependencies:
                        assert plan.refresh_procedure_schema is not None
                        schema = plan.refresh_procedure_schema
                        # Timescale 2.28 refreshes may internally commit. No
                        # explicit transaction and no command bundled with CALL.
                        # Disable native batching for this already-bounded span
                        # so empty source slices cannot be skipped by batching.
                        call = (
                            f'CALL "{schema}".refresh_continuous_aggregate('
                            "$1::regclass,$2::timestamptz,$3::timestamptz,true,"
                            "'{\"buckets_per_batch\":0}'::jsonb)"
                        )
                        for dependency in plan.dependencies:
                            for lo, hi in _chunks(lower, upper, dependency.bucket_width):
                                await self._refresh_chunk(conn, call, dependency, lo, hi)
                        for dependency in plan.dependencies:
                            lo, hi = _aligned(lower, upper, dependency.bucket_width)
                            await self._verify(conn, dependency, lo, hi)
                    if source_table == RELAY:
                        await self._relay_quiet(conn, lower, upper)
                finally:
                    if relay_locked:
                        await _unlock(conn, RELAY_LOCK)
                    await _unlock(conn, MAINTENANCE_LOCK)
        except ArchiveError:
            raise
        except asyncio.CancelledError:
            raise
        except HeavyWorkCancelled as error:
            raise ArchiveError("archive_job_cancelled", status_code=503) from error
        except Exception as error:
            raise ArchiveError(
                "archive_derived_refresh_failed",
                status_code=503,
                detail={
                    "source_table": source_table,
                },
            ) from error

    async def _relay_bounds(
        self,
        conn: asyncpg.Connection,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime]:
        # A recorded baseline may survive indefinitely while supported by
        # heartbeats. +/-60 seconds is NOT a safe influence bound. The API
        # supplies no channel list, so conservatively follow all 16 channels
        # to their first retained successor, capped at existing derived
        # history. Channels without a successor must repair to that horizon.
        horizon = await conn.fetchval(
            """
            SELECT greatest($1::timestamptz,
                (SELECT bucket+interval '1 hour' FROM public.monitoring_relay_duration_1hour
                  ORDER BY bucket DESC LIMIT 1),
                (SELECT max(covered_through) FROM public.monitoring_overview_coverage
                  WHERE model_name IN ('monitoring_relay_duration_1hour','monitoring_relay_duration_1day')))
        """,
            end,
        )
        if not isinstance(horizon, datetime) or horizon.year == 9999:
            raise ArchiveError("archive_derived_relay_horizon_unknown")
        through = await conn.fetchval(
            """
            SELECT greatest($1::timestamptz, max(least($2::timestamptz,coalesce(next_change.time,$2::timestamptz))))
              FROM generate_series(0,15) channel(number)
              LEFT JOIN LATERAL (
                  SELECT "time" FROM public.relay_observation o
                   WHERE o.channel=channel.number AND o."time" >= $1::timestamptz
                   ORDER BY "time",observation_id LIMIT 1
              ) next_change ON true
        """,
            end,
            horizon,
        )
        return _aligned(start, through, HOUR)

    async def _repair_relay_hour(
        self, conn: asyncpg.Connection, start: datetime, end: datetime
    ) -> None:
        await self._checkpoint()
        async with conn.transaction(isolation="repeatable_read"):
            # The global rebuild's advisory lock is held, but raw writers
            # are not blocked. Slice and owner queries share this snapshot;
            # later overlapping writes remain dirty and block success below.
            watermark = await conn.fetchval(
                "SELECT coalesce(max(observation_id),0)::bigint FROM public.relay_observation"
            )
            await conn.execute(
                "CREATE TEMP TABLE archive_relay_hour_repair "
                "(LIKE public.monitoring_relay_duration_1hour INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            await conn.execute(_RELAY_BUILD, start, end, watermark)
            await conn.execute(
                "UPDATE public.monitoring_relay_duration_1hour SET active=false WHERE bucket=$1",
                start,
            )
            await conn.execute(_RELAY_UPSERT)
            if not await conn.fetchval(_RELAY_PARITY, start):
                raise ArchiveError("archive_derived_relay_hourly_unverified", status_code=503)
        await self._checkpoint()
        await self._relay_quiet(conn, start, end)

    async def _relay_quiet(self, conn: asyncpg.Connection, start: datetime, end: datetime) -> None:
        row = await conn.fetchrow(
            "SELECT claimed_from,claimed_through,dirty_from,dirty_through "
            "FROM public.monitoring_relay_overview_progress WHERE id"
        )
        if row is None:
            raise ArchiveError("archive_derived_relay_progress_missing")
        if row["claimed_from"] is not None or row["claimed_through"] is not None:
            raise ArchiveError("archive_derived_relay_checkpoint_busy")
        if (
            row["dirty_from"] is not None
            and row["dirty_through"] is not None
            and row["dirty_from"] < end
            and row["dirty_through"] > start
        ):
            # Do not clear a global dirty interval with a bounded repair.
            # The original supervised workflow owns that progress evidence.
            raise ArchiveError("archive_derived_relay_window_dirty")

    async def _refresh_chunk(
        self,
        conn: asyncpg.Connection,
        call: str,
        dependency: AggregateDependency,
        start: datetime,
        end: datetime,
    ) -> None:
        await self._checkpoint()
        try:
            await conn.execute(call, f"{dependency.view_schema}.{dependency.view_name}", start, end)
        except asyncpg.PostgresError as error:
            if error.sqlstate != "57014" or end - start <= dependency.bucket_width:
                raise
            count = _micros(end - start) // _micros(dependency.bucket_width)
            midpoint = start + dependency.bucket_width * (count // 2)
            await self._refresh_chunk(conn, call, dependency, start, midpoint)
            await self._refresh_chunk(conn, call, dependency, midpoint, end)
            return
        await self._checkpoint()
        await self._verify(conn, dependency, start, end)

    async def _verify(
        self,
        conn: asyncpg.Connection,
        dependency: AggregateDependency,
        start: datetime,
        end: datetime,
    ) -> None:
        """Accept only no pending invalidation plus materialization through ``end``.

        The native watermark is the end of the last bucket holding data, so a
        quiet tail sits above it legitimately; that tail must be proven empty
        in both the aggregate and its input.
        """
        row = await conn.fetchrow(
            _FRESHNESS, dependency.view_schema, dependency.view_name, start, end
        )
        if row is not None and not row["pending"]:
            # The quiet tail above the watermark must be empty in the
            # aggregate and its input (whole range when nothing materialized).
            if row["covered"] or await self._empty_bucket(conn, dependency, row["tail_start"], end):
                return
        raise ArchiveError(
            "archive_derived_refresh_unverified",
            status_code=503,
            detail={
                "aggregate": dependency.view_name,
            },
        )

    async def _empty_bucket(
        self,
        conn: asyncpg.Connection,
        dependency: AggregateDependency,
        start: datetime,
        end: datetime,
    ) -> bool:
        # A native watermark may retreat when the last materialized row is
        # removed. Prove both the stored bucket and its actual input empty.
        source, parent, _width = KNOWN[dependency.view_name]
        time_column = "bucket" if parent != source else "time"
        predicate = "TRUE"
        if dependency.view_name == "monitoring_light_intensity_1hour":
            predicate = (
                "device_name IS NOT NULL AND (effective_light_intensity IS NOT NULL "
                "OR nominal_light_intensity IS NOT NULL OR ramp_progress_light IS NOT NULL)"
            )
        return bool(
            await conn.fetchval(
                f'SELECT NOT EXISTS(SELECT 1 FROM "{dependency.view_schema}"."{dependency.view_name}" '
                "WHERE bucket>=$1 AND bucket<$2) AND NOT EXISTS("
                f'SELECT 1 FROM public."{parent}" WHERE "{time_column}">=$1 AND "{time_column}"<$2 '
                f"AND ({predicate}))",
                start,
                end,
            )
        )

    async def _checkpoint(self) -> None:
        await asyncio.to_thread(self.heavy_work.control.checkpoint)


def _micros(value: timedelta) -> int:
    return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds


def _aligned(start: datetime, end: datetime, width: timedelta) -> tuple[datetime, datetime]:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    step = _micros(width)
    lo = _micros(start.astimezone(UTC) - epoch)
    hi = _micros(end.astimezone(UTC) - epoch)
    return (
        epoch + timedelta(microseconds=(lo // step) * step),
        epoch + timedelta(microseconds=((hi + step - 1) // step) * step),
    )


def _chunks(
    start: datetime,
    end: datetime,
    width: timedelta,
    *,
    maximum_span: timedelta = DAY,
) -> Iterator[tuple[datetime, datetime]]:
    lower, upper = _aligned(start, end, width)
    span = width * max(1, _micros(maximum_span) // _micros(width))
    while lower < upper:
        through = min(upper, lower + span)
        yield lower, through
        lower = through
