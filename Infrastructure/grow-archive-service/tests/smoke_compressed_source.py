"""Native compressed-source export and verified source-deletion proof."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from dav_fixture import loopback_dav
from smoke_derived_refresh import install_real_derived_schema
from smoke_lifecycle import _assert, _parse_database_url
import asyncpg
import httpx

from grow_archive_service.binary_codec import record_payload
from grow_archive_service.cache import ArchiveCache
from grow_archive_service.capture import SourceCapture
from grow_archive_service.deletion import SourceDeletion
from grow_archive_service.derived_refresh import DerivedHistoryMaintenance
from grow_archive_service.exporter import ExportService
from grow_archive_service.execution import HeavyWorkExecutor
from grow_archive_service.membership import CaptureWindow, selection_sql, window_arrays
from grow_archive_service.parquet_codec import iter_archive_binary_rows
from grow_archive_service.schema import load_source_catalog
from grow_archive_service.source_retention import removable_observation_sql
from grow_archive_service.webdav import WebDAVArtifactClient
from shared.nextcloud_dav import discover_nextcloud

ROW_COUNT = 4100


async def _all_source_bags(pool: asyncpg.Pool) -> dict[str, Counter]:
    async with pool.acquire() as conn:
        return {
            stream.name: Counter(
                row["payload"]
                for row in await conn.fetch(
                    f'SELECT record_send(m) AS payload FROM public."{stream.name}" m'
                )
            )
            for stream in load_source_catalog().defined_streams()
        }


async def _capture_source_bags(pool: asyncpg.Pool, windows):
    catalog = load_source_catalog()
    bags: dict[str, Counter] = {}
    observation_counts: dict[str, int] = {}
    oids: dict[str, tuple[int, ...]] = {}
    async with pool.acquire() as conn:
        for stream in catalog.defined_streams():
            predicate, role = selection_sql(stream)
            records = await conn.fetch(
                f'SELECT record_send(m) AS payload,{role} AS role FROM public."{stream.name}" m '
                f"WHERE {predicate}",
                *window_arrays(windows),
            )
            if any(
                column.name == stream.time_column and not column.not_null
                for column in stream.columns
            ):
                null_predicate, null_role = selection_sql(stream, include_null_time=True)
                records += await conn.fetch(
                    f'SELECT record_send(m) AS payload,{null_role} AS role FROM public."{stream.name}" m '
                    f"WHERE {null_predicate}",
                    list(dict.fromkeys(window.room for window in windows)),
                )
            bags[stream.name] = Counter((row["payload"], row["role"]) for row in records)
            observation_counts[stream.name] = sum(
                1 for row in records if row["role"] == "observation"
            )
            sql_count = await conn.fetchval(
                f"SELECT count(*) FROM public.\"{stream.name}\" m WHERE {predicate} AND ({role})='observation'",
                *window_arrays(windows),
            )
            _assert(
                sql_count == observation_counts[stream.name],
                f"source observation count and binary bag differ: {stream.name}",
            )
            attributes = await conn.fetch(
                "SELECT atttypid::integer AS oid FROM pg_attribute WHERE attrelid=$1::regclass "
                "AND attnum>0 AND NOT attisdropped ORDER BY attnum",
                stream.relation,
            )
            oids[stream.name] = tuple(row["oid"] for row in attributes)
    return bags, observation_counts, oids


async def _removable_source_bag(pool, source_table, window, cutoff):
    stream = load_source_catalog().by_name()[source_table]
    predicate, role = selection_sql(stream)
    removable = removable_observation_sql(stream, alias="m", cutoff="$6")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f'SELECT record_send(m) AS payload FROM public."{stream.name}" m WHERE {predicate} '
            f'AND m."{stream.time_column}">=$4 AND m."{stream.time_column}"<$5 '
            f"AND ({role})='observation' AND {removable}",
            *window_arrays((window,)),
            window.start,
            window.end,
            cutoff,
        )
    return Counter(row["payload"] for row in rows)


async def _materialize_control_caggs(conn, start, end):
    call = (
        "CALL public.refresh_continuous_aggregate($1::regclass,$2::timestamptz,$3::timestamptz,"
        "true,'{\"buckets_per_batch\":0}'::jsonb)"
    )
    for view in (
        "monitoring_effective_setpoints_1min",
        "monitoring_effective_setpoints_5min",
        "monitoring_automation_state_1min",
        "monitoring_automation_state_5min",
    ):
        await conn.execute(call, f"public.{view}", start, end)


async def _assert_control_caggs_match_raw(conn, start, end):
    counts = {}
    for view, width in (
        ("monitoring_effective_setpoints_1min", timedelta(minutes=1)),
        ("monitoring_effective_setpoints_5min", timedelta(minutes=5)),
    ):
        actual = await conn.fetch(
            f"SELECT bucket,location,cluster,device_name,mode,effective_heating_setpoint_count,"
            f"effective_heating_setpoint_sum,effective_heating_setpoint_min,effective_heating_setpoint_max,"
            f"effective_heating_setpoint_last,last_observed_at FROM public.{view} "
            "WHERE bucket>=$1 AND bucket<$2 AND location='Flower Room' AND cluster='main' "
            "AND device_name='light_f_1' AND mode='DAY' ORDER BY 1,2,3,4,5",
            start,
            end,
        )
        expected = await conn.fetch(
            'WITH source AS (SELECT time_bucket($3::interval,"time") AS bucket,location,cluster,'
            'device_name,mode,"time",monitoring_ingest_id,effective_heating_setpoint '
            'FROM public.effective_setpoints WHERE "time">=$1 AND "time"<$2 '
            "AND location='Flower Room' AND cluster='main' AND device_name='light_f_1' AND mode='DAY') "
            "SELECT bucket,location,cluster,device_name,mode,"
            "count(effective_heating_setpoint)::bigint AS effective_heating_setpoint_count,"
            "sum(effective_heating_setpoint::numeric) AS effective_heating_setpoint_sum,"
            "min(effective_heating_setpoint::numeric) AS effective_heating_setpoint_min,"
            "max(effective_heating_setpoint::numeric) AS effective_heating_setpoint_max,"
            '(array_agg(effective_heating_setpoint ORDER BY "time" DESC,'
            "COALESCE(monitoring_ingest_id,0) DESC))[1] AS effective_heating_setpoint_last,"
            'max("time") AS last_observed_at FROM source '
            "GROUP BY bucket,location,cluster,device_name,mode ORDER BY 1,2,3,4,5",
            start,
            end,
            width,
        )
        _assert(
            [tuple(row.values()) for row in actual] == [tuple(row.values()) for row in expected],
            f"{view} differs from recomputed raw effective_setpoints",
        )
        counts[view] = len(actual)

    for view, width in (
        ("monitoring_automation_state_1min", timedelta(minutes=1)),
        ("monitoring_automation_state_5min", timedelta(minutes=5)),
    ):
        actual = await conn.fetch(
            f"SELECT bucket,location,cluster,device_name,sample_count,device_state_min,device_state_max,"
            f"device_state_last,device_mode_last,pid_output_count,pid_output_min,pid_output_max,"
            f"pid_output_last,duty_cycle_percent_count,duty_cycle_percent_min,duty_cycle_percent_max,"
            f"duty_cycle_percent_last,control_reason_last,last_observed_at FROM public.{view} "
            "WHERE bucket>=$1 AND bucket<$2 AND location='Flower Room' AND cluster='main' "
            "AND device_name='heater_f_1' ORDER BY 1,2,3,4",
            start,
            end,
        )
        expected = await conn.fetch(
            'WITH source AS (SELECT time_bucket($3::interval,"time") AS bucket,location,cluster,'
            'device_name,"time",id,device_state,device_mode,pid_output,duty_cycle_percent,control_reason '
            'FROM public.automation_state WHERE "time">=$1 AND "time"<$2 '
            "AND location='Flower Room' AND cluster='main' AND device_name='heater_f_1') "
            "SELECT bucket,location,cluster,device_name,count(*)::bigint AS sample_count,"
            "min(device_state) AS device_state_min,max(device_state) AS device_state_max,"
            '(array_agg(device_state ORDER BY "time" DESC,id DESC))[1] AS device_state_last,'
            '(array_agg(device_mode ORDER BY "time" DESC,id DESC))[1] AS device_mode_last,'
            "count(pid_output)::bigint AS pid_output_count,min(pid_output) AS pid_output_min,"
            "max(pid_output) AS pid_output_max,"
            '(array_agg(pid_output ORDER BY "time" DESC,id DESC))[1] AS pid_output_last,'
            "count(duty_cycle_percent)::bigint AS duty_cycle_percent_count,"
            "min(duty_cycle_percent) AS duty_cycle_percent_min,max(duty_cycle_percent) AS duty_cycle_percent_max,"
            '(array_agg(duty_cycle_percent ORDER BY "time" DESC,id DESC))[1] AS duty_cycle_percent_last,'
            '(array_agg(control_reason ORDER BY "time" DESC,id DESC))[1] AS control_reason_last,'
            'max("time") AS last_observed_at FROM source '
            "GROUP BY bucket,location,cluster,device_name ORDER BY 1,2,3,4",
            start,
            end,
            width,
        )
        _assert(
            [tuple(row.values()) for row in actual] == [tuple(row.values()) for row in expected],
            f"{view} differs from recomputed raw automation_state",
        )
        counts[view] = len(actual)
    return counts


async def run(database_url: str) -> None:
    _parse_database_url(database_url)
    _assert(os.getenv("MONITORING_HARNESS_INTERNAL") == "1", "owned harness marker required")
    await install_real_derived_schema(database_url)
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    heavy = HeavyWorkExecutor()
    heavy.control.allow_resume()
    grow_id, dataset_id = uuid4(), uuid4()
    start = datetime(2026, 1, 4, 1, tzinfo=UTC)
    end = start + timedelta(hours=2)
    anchor_at = datetime(2026, 1, 5, 12, tzinfo=UTC)
    predecessor_at = start + timedelta(seconds=ROW_COUNT - 1)
    control_times = tuple(start + timedelta(minutes=minute) for minute in (10, 20, 30))
    try:
        async with pool.acquire() as conn:
            _assert(
                await conn.fetchval(
                    "SELECT fixture_version FROM grow_data_lifecycle_fixture_metadata "
                    "WHERE fixture_name='grow-data-lifecycle'"
                )
                == 1,
                "owned fixture marker missing",
            )
            _assert(
                not await conn.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=current_user"),
                "fixture role must not be superuser",
            )
            sensor = await conn.fetchrow(
                "SELECT s.sensor_id,s.name,rm.name AS room FROM sensor s JOIN device d USING(device_id) "
                "LEFT JOIN rack r USING(rack_id) LEFT JOIN room rm USING(room_id) "
                "WHERE rm.name='Flower Room' AND right(s.name,2) IN ('_f','_b') "
                "ORDER BY s.sensor_id LIMIT 1"
            )
            _assert(sensor is not None, "Flower Room front/back sensor fixture missing")
            sensor_id = sensor["sensor_id"]
            crop_batch_id = await conn.fetchval(
                "INSERT INTO crop_batch(crop_name,start_date,end_date,room_id) "
                "VALUES('Owned compressed-source grow','2026-01-04','2026-01-04',1) RETURNING batch_id"
            )
            await conn.execute(
                "INSERT INTO grow_lifecycle(grow_plan_id,crop_batch_id,state,original_drying_end,"
                "first_closed_at,closed_at) VALUES($1,$2,'closed','2026-01-04',clock_timestamp(),clock_timestamp())",
                grow_id,
                crop_batch_id,
            )
            await conn.execute(
                "INSERT INTO archive_dataset(dataset_id,kind,grow_plan_id,room,available_from,available_to,"
                "anniversary_at) VALUES($1,'grow',$2,'Flower Room',$3,$4,'2027-01-04T05:00:00Z')",
                dataset_id,
                grow_id,
                start,
                end,
            )
            await conn.execute(
                "INSERT INTO archive_window(dataset_id,calendar_revision,event_id,phase_type,room,start_at,end_at) "
                "VALUES($1,1,2000,'flower_bulk','Flower Room',$2,$3)",
                dataset_id,
                start,
                end,
            )
            await conn.execute(
                "INSERT INTO archive_scope_override(source_table,source_identity,start_at,end_at,room) "
                "VALUES('measurement',jsonb_build_object('sensor_id',$1::integer),$2,$3,'Flower Room')",
                sensor_id,
                start,
                end,
            )
            await conn.execute(
                'INSERT INTO mode_transition_history(location,cluster,new_mode_id,triggered_by,"time") '
                "VALUES('Flower Room','main',1,'system',NULL)"
            )
            await conn.execute(
                "INSERT INTO measurement(time,sensor_id,value,status) "
                "SELECT $1::timestamptz+ticks.i*interval '1 second',$2,"
                "CASE WHEN ticks.i%11=0 THEN '-0'::real ELSE (ticks.i%101)::real END,'ok' "
                "FROM generate_series(0,$3-1) AS ticks(i)",
                start,
                sensor_id,
                ROW_COUNT,
            )
            await conn.execute(
                "INSERT INTO measurement(time,sensor_id,value,status) VALUES($1,$2,25.5::real,'ok')",
                anchor_at,
                sensor_id,
            )
            await conn.executemany(
                'INSERT INTO effective_setpoints("time",location,cluster,mode,device_name,'
                "effective_heating_setpoint) VALUES($1,'Flower Room','main','DAY','light_f_1',$2)",
                [(instant, 22.0 + index * 2.0) for index, instant in enumerate(control_times)],
            )
            await conn.execute(
                'INSERT INTO effective_setpoints("time",location,cluster,mode,device_name,'
                "effective_heating_setpoint) VALUES($1,'Flower Room','main','DAY','light_f_1',30.0)",
                anchor_at,
            )
            await conn.executemany(
                'INSERT INTO automation_state("time",location,cluster,device_name,device_state,'
                "device_mode,pid_output,duty_cycle_percent,control_reason) "
                "VALUES($1,'Flower Room','main','heater_f_1',$2,'auto',$3,$4,$5)",
                [
                    (
                        instant,
                        1 if index % 2 == 0 else 0,
                        10.0 + index * 10.0,
                        10.0 + index * 10.0,
                        f"compressed-cagg-{index}",
                    )
                    for index, instant in enumerate(control_times)
                ],
            )
            await conn.execute(
                'INSERT INTO automation_state("time",location,cluster,device_name,device_state,'
                "device_mode,pid_output,duty_cycle_percent,control_reason) "
                "VALUES($1,'Flower Room','main','heater_f_1',1,'auto',50.0,50.0,'retained-anchor')",
                anchor_at,
            )
            await _materialize_control_caggs(conn, start, end)
            control_caggs_before = await _assert_control_caggs_match_raw(conn, start, end)
            _assert(
                set(control_caggs_before.values()) == {3},
                "control CAGGs were not initially materialized from all three source buckets",
            )
            for table, time_column, segment_columns in (
                ("measurement", "time", "sensor_id"),
                ("effective_setpoints", "time", "location,cluster,device_name,mode"),
                ("automation_state", "time", "location,cluster,device_name"),
            ):
                await conn.execute(
                    f"ALTER TABLE public.{table} SET (timescaledb.compress, "
                    f"timescaledb.compress_orderby='{time_column} ASC',"
                    f"timescaledb.compress_segmentby='{segment_columns}')"
                )
                await conn.fetchval(
                    "SELECT compress_chunk(format('%I.%I',c.chunk_schema,c.chunk_name)::regclass) "
                    "FROM timescaledb_information.chunks c WHERE c.hypertable_schema='public' "
                    "AND c.hypertable_name=$1 AND c.range_start<=$2 AND c.range_end>$2",
                    table,
                    start,
                )
                compressed = await conn.fetchval(
                    "SELECT is_compressed FROM timescaledb_information.chunks "
                    "WHERE hypertable_schema='public' AND hypertable_name=$1 "
                    "AND range_start<=$2 AND range_end>$2",
                    table,
                    start,
                )
                _assert(compressed is True, f"{table} source chunk was not natively compressed")
            indices_before = await conn.fetch(
                "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND tablename='automation_state' ORDER BY indexname"
            )
            _assert(len(indices_before) == 6, "fixture must contain all six automation indexes")

        window = CaptureWindow("Flower Room", start, end)
        windows = (window,)
        source, source_observations, oids = await _capture_source_bags(pool, windows)
        _assert(
            source_observations["measurement"] == ROW_COUNT,
            "compressed source window does not contain the complete fixture row set",
        )
        _assert(
            source_observations["effective_setpoints"] == 3,
            "compressed source window omitted effective_setpoints control rows",
        )
        _assert(
            source_observations["automation_state"] == 3,
            "compressed source window omitted automation_state control rows",
        )
        source_before_export = await _all_source_bags(pool)

        os.environ["CALDAV_ALLOW_HTTP_TEST"] = "true"
        with (
            tempfile.TemporaryDirectory(prefix="grow-compressed-source-owned-") as directory,
            loopback_dav() as (origin, _state),
        ):
            root = Path(directory)

            @asynccontextmanager
            async def dav_factory(instance_id=None):
                async with httpx.AsyncClient(
                    auth=httpx.BasicAuth("fixture", "fixture-only-password"),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=10,
                ) as client:
                    discovery = await discover_nextcloud(client, origin + "/dav")
                    if instance_id is not None:
                        _assert(discovery.instance_id == instance_id, "fixture instance changed")
                    yield WebDAVArtifactClient(
                        discovery,
                        client,
                        instance_id=discovery.instance_id,
                        scratch_root=root / "staging" / "http",
                    )

            service = ExportService(
                pool,
                root,
                heavy,
                SourceCapture(pool, heavy),
                dav_factory=dav_factory,
                cache=ArchiveCache(root / "cache", root / "staging", reserve=0),
            )
            queue = service.queue
            export_job = await queue.enqueue(dataset_id, "export", 1, required_closed=False)
            export_claim = await queue.claim(
                uuid4(), now=datetime.now(UTC), job_id=export_job.job_id
            )
            _assert(export_claim is not None, "compressed-source export job was not claimable")
            try:
                artifact_id = await service.export_dataset(export_claim)
                await queue.succeed(export_claim)
            except BaseException as error:
                code = getattr(error, "code", "")
                if code:
                    await queue.fail(export_claim, code)
                raise
            verified = await service.reverify_artifact(artifact_id)
            artifact_row = await pool.fetchrow(
                "SELECT local_path FROM archive_artifact WHERE artifact_id=$1", artifact_id
            )
            archive_path = Path(artifact_row["local_path"])
            _assert(archive_path.is_file(), "verified export did not retain a local ZIP copy")
            _assert(
                await _all_source_bags(pool) == source_before_export,
                "capture changed source fields or multiplicities",
            )
            for table in ("measurement", "effective_setpoints", "automation_state"):
                _assert(
                    await pool.fetchval(
                        "SELECT is_compressed FROM timescaledb_information.chunks "
                        "WHERE hypertable_schema='public' AND hypertable_name=$1 "
                        "AND range_start<=$2 AND range_end>$2",
                        table,
                        start,
                    ),
                    f"source export decompressed the native {table} chunk",
                )

            for stream in load_source_catalog().defined_streams():
                decoded: Counter = Counter()
                observation_rows = 0
                for batch in iter_archive_binary_rows(archive_path, verified, stream.name):
                    for binary in batch:
                        original = binary[: len(stream.columns)]
                        record_role = binary[len(stream.columns) + 3].decode()
                        decoded[(record_payload(original, oids[stream.name]), record_role)] += 1
                        observation_rows += record_role == "observation"
                _assert(
                    decoded == source[stream.name],
                    f"compressed source ZIP binary fields or multiplicities changed: {stream.name}",
                )
                _assert(
                    observation_rows == source_observations[stream.name],
                    f"compressed source ZIP observation count changed: {stream.name}",
                )
                _assert(
                    verified.data["observation_counts"].get(stream.name, 0) == observation_rows,
                    f"compressed source ZIP manifest count changed: {stream.name}",
                )
            measurement_members = [
                entry
                for entry in verified.data["members"]
                if entry.get("source_table") == "measurement"
                and entry.get("record_role") == "observation"
            ]
            _assert(
                sum(entry["row_count"] for entry in measurement_members) == ROW_COUNT,
                "verified ZIP omitted or aggregated compressed source rows",
            )
            _assert(
                all(
                    entry.get("window_start") is not None and entry.get("window_end") is not None
                    for entry in measurement_members
                ),
                "compressed-source raw members omitted their time windows",
            )

            eligible_by_table = {}
            for table in ("measurement", "effective_setpoints", "automation_state"):
                eligible_by_table[table] = await _removable_source_bag(pool, table, window, end)
            _assert(
                len(eligible_by_table["measurement"]) == ROW_COUNT - 1,
                "fixture must expose the full removable measurement set and retain its predecessor",
            )
            for table in ("effective_setpoints", "automation_state"):
                _assert(
                    len(eligible_by_table[table]) == 2,
                    f"{table} fixture must expose two removable rows and retain its predecessor",
                )
            predecessor_payload = await pool.fetchval(
                "SELECT record_send(m) FROM public.measurement m WHERE sensor_id=$1 AND time=$2",
                sensor_id,
                predecessor_at,
            )
            anchor_payload = await pool.fetchval(
                "SELECT record_send(m) FROM public.measurement m WHERE sensor_id=$1 AND time=$2",
                sensor_id,
                anchor_at,
            )
            effective_predecessor_payload = await pool.fetchval(
                'SELECT record_send(m) FROM public.effective_setpoints m WHERE "time"=$1 '
                "AND location='Flower Room' AND cluster='main' AND device_name='light_f_1' AND mode='DAY'",
                control_times[-1],
            )
            effective_anchor_payload = await pool.fetchval(
                'SELECT record_send(m) FROM public.effective_setpoints m WHERE "time"=$1 '
                "AND location='Flower Room' AND cluster='main' AND device_name='light_f_1' AND mode='DAY'",
                anchor_at,
            )
            automation_predecessor_payload = await pool.fetchval(
                'SELECT record_send(m) FROM public.automation_state m WHERE "time"=$1 '
                "AND location='Flower Room' AND cluster='main' AND device_name='heater_f_1'",
                control_times[-1],
            )
            automation_anchor_payload = await pool.fetchval(
                'SELECT record_send(m) FROM public.automation_state m WHERE "time"=$1 '
                "AND location='Flower Room' AND cluster='main' AND device_name='heater_f_1'",
                anchor_at,
            )
            _assert(
                all(
                    payload is not None
                    for payload in (
                        predecessor_payload,
                        anchor_payload,
                        effective_predecessor_payload,
                        effective_anchor_payload,
                        automation_predecessor_payload,
                        automation_anchor_payload,
                    )
                ),
                "predecessor or post-window protected fixture rows are missing",
            )

            deletion = SourceDeletion(pool, heavy, queue, DerivedHistoryMaintenance(pool, heavy))
            expiry_job = await queue.enqueue(dataset_id, "expire", 1, required_closed=False)
            expiry_claim = await queue.claim(
                uuid4(), now=datetime.now(UTC), job_id=expiry_job.job_id
            )
            _assert(expiry_claim is not None, "compressed-source deletion job was not claimable")
            try:
                deleted = await deletion.delete_verified(
                    expiry_claim, verified, rows_before=end, now=datetime.now(UTC)
                )
                await queue.succeed(expiry_claim)
            except BaseException as error:
                code = getattr(error, "code", "")
                if code:
                    await queue.fail(expiry_claim, code)
                raise

            expected_deleted = sum(len(rows) for rows in eligible_by_table.values())
            _assert(
                deleted == expected_deleted,
                f"verified deletion removed {deleted} rows, expected {expected_deleted}",
            )
            expected_after = dict(source_before_export)
            for table, rows in eligible_by_table.items():
                expected_after[table] -= rows
            _assert(
                await _all_source_bags(pool) == expected_after,
                "deletion changed protected source bytes or removed rows outside the verified set",
            )
            _assert(
                await pool.fetchval(
                    "SELECT record_send(m) FROM public.measurement m WHERE sensor_id=$1 AND time=$2",
                    sensor_id,
                    predecessor_at,
                )
                == predecessor_payload,
                "latest measurement predecessor anchor changed during deletion",
            )
            _assert(
                await pool.fetchval(
                    "SELECT record_send(m) FROM public.measurement m WHERE sensor_id=$1 AND time=$2",
                    sensor_id,
                    anchor_at,
                )
                == anchor_payload,
                "post-window measurement row changed during deletion",
            )
            _assert(
                await pool.fetchval(
                    'SELECT record_send(m) FROM public.effective_setpoints m WHERE "time"=$1 '
                    "AND location='Flower Room' AND cluster='main' AND device_name='light_f_1' AND mode='DAY'",
                    control_times[-1],
                )
                == effective_predecessor_payload,
                "effective_setpoints predecessor anchor changed during deletion",
            )
            _assert(
                await pool.fetchval(
                    'SELECT record_send(m) FROM public.effective_setpoints m WHERE "time"=$1 '
                    "AND location='Flower Room' AND cluster='main' AND device_name='light_f_1' AND mode='DAY'",
                    anchor_at,
                )
                == effective_anchor_payload,
                "post-window effective_setpoints row changed during deletion",
            )
            _assert(
                await pool.fetchval(
                    'SELECT record_send(m) FROM public.automation_state m WHERE "time"=$1 '
                    "AND location='Flower Room' AND cluster='main' AND device_name='heater_f_1'",
                    control_times[-1],
                )
                == automation_predecessor_payload,
                "automation_state predecessor anchor changed during deletion",
            )
            _assert(
                await pool.fetchval(
                    'SELECT record_send(m) FROM public.automation_state m WHERE "time"=$1 '
                    "AND location='Flower Room' AND cluster='main' AND device_name='heater_f_1'",
                    anchor_at,
                )
                == automation_anchor_payload,
                "post-window automation_state row changed during deletion",
            )
            async with pool.acquire() as conn:
                indices_after = await conn.fetch(
                    "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' "
                    "AND tablename='automation_state' ORDER BY indexname"
                )
            _assert(indices_after == indices_before, "automation_state indexes changed")
            async with pool.acquire() as conn:
                cagg_counts_after = await _assert_control_caggs_match_raw(conn, start, end)
            _assert(
                cagg_counts_after == {view: 1 for view in control_caggs_before},
                "derived refresh did not update compressed-source CAGG buckets from raw rows",
            )
            for table in ("measurement", "effective_setpoints", "automation_state"):
                remains_compressed = await pool.fetchval(
                    "SELECT COALESCE(bool_or(is_compressed),false) "
                    "FROM timescaledb_information.chunks WHERE hypertable_schema='public' "
                    "AND hypertable_name=$1 AND range_start<$3 AND range_end>$2",
                    table,
                    start,
                    end,
                )
                _assert(
                    not remains_compressed,
                    f"verified deletion left an overlapping {table} source chunk compressed",
                )
            print(
                f"Native compressed-source ZIP parity and verified deletion: "
                f"archived_rows={ROW_COUNT}, effective_setpoints=3, automation_state=3, "
                f"deleted_rows={deleted}, CAGG buckets={sum(cagg_counts_after.values())}; "
                "predecessor and protected source bytes unchanged; six automation indexes unchanged"
            )
    finally:
        await heavy.shutdown()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    asyncio.run(run(parser.parse_args().database_url))
