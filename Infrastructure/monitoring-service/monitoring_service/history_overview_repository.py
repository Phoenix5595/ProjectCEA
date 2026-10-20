"""Bounded all-history monitoring overviews with exact source coverage gates.

``HistoryOverviewRepository`` serves fixed-range sensor and control summaries
from one read-only statement whose coverage/invalidation/watermark guards share
the row snapshot. Sensor and light contributions merge NUMERIC sufficient
statistics across daily/hourly/raw tiers without prorating or double counting.
Requested PID spans merge time-weighted values by covered seconds rather than
sample count. Relay durations merge integer microseconds so on + off + unknown
equals exact bucket elapsed time; buckets without recorded owner time are
honest unknown time, never fabricated state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from typing import Final, Protocol, final

import asyncpg

from monitoring_service.control_models import ControlReadScope
from monitoring_service.history_buckets import (
    FixedHistoryBucket,
    choose_history_bucket,
    history_bucket_bounds,
    timedelta_microseconds,
    validate_history_budget,
)
from monitoring_service.history_overview_models import (
    HistoryOverviewControlResponse,
    HistoryOverviewControlSeries,
    HistoryOverviewMetadata,
    HistoryOverviewPoint,
    HistoryOverviewRange,
    HistoryOverviewRelayPoint,
    HistoryOverviewRelaySeries,
    HistoryOverviewRequestedPidSeries,
    HistoryOverviewSamplePoint,
    HistoryOverviewSensorResponse,
    HistoryOverviewSensorSeries,
    HistoryOverviewSensorStatistics,
)
from monitoring_service.history_overview_sql import (
    CONTROL_OVERVIEW_STATEMENT,
    DATA_ROW_KIND,
    GUARD_ROW_KIND,
    GUARD_UNAVAILABLE_MESSAGE,
    LIGHT_OVERVIEW_STATEMENT,
    RELAY_OVERVIEW_STATEMENT,
    SENSOR_OVERVIEW_STATEMENT,
    bucket_points_json,
)
from monitoring_service.query_observation import request_observation
from monitoring_service.sensor_models import (
    MonitoringUnavailableError,
    Node,
    RoomMetadata,
    StddevQuality,
    resolve_room_metadata,
)
from monitoring_service.sensor_rows import node_mapping_args, unit_families
from shared.history_availability import local_history_scope
from shared.history_summary import SummaryState, decimal, exact_add, float_or_none

_MICROSCALE: Final[Decimal] = Decimal(1_000_000)


class _OverviewDatabase(Protocol):
    async def fetch(
        self, query: str, *args: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]: ...


@final
class HistoryOverviewRepository:
    """Serve fixed all-history overviews from bounded covered sources."""

    def __init__(self, database: _OverviewDatabase) -> None:
        self._database = database

    # ------------------------------------------------------------- sensor
    async def sensor(
        self, room: str, history_range: HistoryOverviewRange, max_points: int = 600
    ) -> HistoryOverviewSensorResponse:
        """Read exact bounded sensor summaries under same-snapshot guards."""
        budget = validate_history_budget(max_points)
        metadata_room = resolve_room_metadata(room)
        bucket = choose_history_bucket(history_range.start, history_range.end, budget)
        bounds = history_bucket_bounds(history_range.start, history_range.end, bucket)
        buckets_json = bucket_points_json(bounds)
        node_mapping = node_mapping_args(room, metadata_room.nodes)
        async with request_observation(tier="overview"), local_history_scope(
            room, history_range.start, history_range.end, ("measurement",)
        ):
            rows = await self._fetch_guarded(
                SENSOR_OVERVIEW_STATEMENT,
                room,
                *node_mapping,
                history_range.start,
                history_range.end,
                buckets_json,
                bucket.seconds,
            )
        grouped: defaultdict[tuple[Node, str, str, str], defaultdict[int, SummaryState]] = (
            defaultdict(lambda: defaultdict(SummaryState))
        )
        for record in rows:
            identity = (
                Node(record["node"]),
                record["sensor"],
                record["unit"],
                record["data_type"],
            )
            grouped[identity][int(record["idx"])].apply(record)
        families = unit_families()
        nodes = metadata_room.nodes
        series: list[HistoryOverviewSensorSeries] = []
        statistics: list[HistoryOverviewSensorStatistics] = []
        for identity in sorted(grouped, key=lambda item: (nodes.index(item[0]), item[1])):
            node, sensor, unit, data_type = identity
            if data_type not in families:
                continue
            bucket_states = grouped[identity]
            series.append(
                HistoryOverviewSensorSeries(
                    sensor=sensor,
                    node=node,
                    unit_family=families[data_type],
                    unit=unit,
                    points=tuple(
                        _overview_point(state, bounds[index])
                        for index, state in sorted(bucket_states.items())
                    ),
                )
            )
            whole = SummaryState()
            for state in bucket_states.values():
                whole.absorb(state)
            statistics.append(_sensor_statistics(sensor, node, whole))
        return HistoryOverviewSensorResponse(
            metadata=_metadata(datetime.now(UTC), history_range, metadata_room, budget, bucket),
            series=tuple(series),
            statistics=tuple(statistics),
        )

    # ------------------------------------------------------------ control
    async def control(
        self,
        location: str,
        history_range: HistoryOverviewRange,
        max_points: int = 600,
        scope: ControlReadScope = "all",
    ) -> HistoryOverviewControlResponse:
        """Read bounded control summaries plus recorded relay lane durations.

        The ``lights`` scope reads only the recorded light family under its own
        coverage guards and returns empty requested-PID and relay series; it
        never queries or gates on automation-side derived readiness.
        """
        budget = validate_history_budget(max_points)
        metadata_room = resolve_room_metadata(location)
        bucket = choose_history_bucket(history_range.start, history_range.end, budget)
        bounds = history_bucket_bounds(history_range.start, history_range.end, bucket)
        buckets_json = bucket_points_json(bounds)
        requested_pid: tuple[HistoryOverviewRequestedPidSeries, ...] = ()
        relays: tuple[HistoryOverviewRelaySeries, ...] = ()
        async with request_observation(tier="overview"), local_history_scope(
            location, history_range.start, history_range.end,
            ("effective_setpoints", "monitoring_room_photoperiod", "monitoring_photoperiod_coverage")
            if scope == "lights" else
            ("effective_setpoints", "automation_state", "relay_observation",
             "monitoring_room_photoperiod", "monitoring_photoperiod_coverage"),
        ):
            if scope == "lights":
                light_rows = await self._fetch_guarded(
                    LIGHT_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
                    bucket.seconds,
                )
                lights, _ = _control_series_groups(light_rows, bounds)
            else:
                control_rows = await self._fetch_guarded(
                    CONTROL_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
                    bucket.seconds,
                )
                lights, requested_pid = _control_series_groups(control_rows, bounds)
                relay_rows = await self._fetch_guarded(
                    RELAY_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
                    bucket.seconds,
                )
                relays = _relay_series_groups(relay_rows, bounds)
        return HistoryOverviewControlResponse(
            metadata=_metadata(datetime.now(UTC), history_range, metadata_room, budget, bucket),
            lights=lights,
            requested_pid=requested_pid,
            relays=relays,
        )

    async def _fetch_guarded(
        self, statement: str, *arguments: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]:
        """Execute one guarded statement; a false guard reports 503 exactly."""
        try:
            rows = await self._database.fetch(statement, *arguments)
        except MonitoringUnavailableError:
            raise
        except (
            asyncpg.PostgresError,
            ConnectionError,
            OSError,
            TimeoutError,
            RuntimeError,
        ) as exc:
            raise MonitoringUnavailableError(GUARD_UNAVAILABLE_MESSAGE) from exc
        guard_row = next((record for record in rows if record["kind"] == GUARD_ROW_KIND), None)
        if guard_row is None or not bool(guard_row["guard_ok"]):
            raise MonitoringUnavailableError(GUARD_UNAVAILABLE_MESSAGE)
        return [record for record in rows if record["kind"] == DATA_ROW_KIND]


def _metadata(
    generated_at: datetime,
    overview_range: HistoryOverviewRange,
    room: RoomMetadata,
    requested_max_points: int,
    bucket: FixedHistoryBucket,
) -> HistoryOverviewMetadata:
    return HistoryOverviewMetadata(
        generated_at=generated_at,
        range=overview_range,
        room=room,
        requested_max_points=requested_max_points,
        bucket=bucket,
        source_coverage="ready",
    )


def _overview_point(
    state: SummaryState, bound: tuple[datetime, datetime, bool]
) -> HistoryOverviewSamplePoint:
    """Materialize exactly one sample-backed point from merged bucket pieces."""
    return HistoryOverviewSamplePoint(
        start=bound[0],
        end=bound[1],
        average=float_or_none(state.average_decimal),
        minimum=float_or_none(state.minimum),
        maximum=float_or_none(state.maximum),
        sample_count=state.count,
        partial=bound[2],
    )


def _sensor_statistics(
    sensor: str, node: Node, whole: SummaryState
) -> HistoryOverviewSensorStatistics:
    """Zero/one/multi sample statistics per the nullable overview contract."""
    if whole.count == 0:
        return HistoryOverviewSensorStatistics(
            sensor=sensor, node=node, sample_count=0, stddev_quality=StddevQuality.EXACT
        )
    if whole.count == 1:
        return HistoryOverviewSensorStatistics(
            sensor=sensor,
            node=node,
            sample_count=1,
            minimum=float_or_none(whole.minimum),
            maximum=float_or_none(whole.maximum),
            average=float_or_none(whole.average_decimal),
            stddev_samp=0.0,
            stddev_quality=StddevQuality.EXACT,
        )
    stddev = float_or_none(whole.sample_stddev_decimal())
    return HistoryOverviewSensorStatistics(
        sensor=sensor,
        node=node,
        sample_count=whole.count,
        minimum=float_or_none(whole.minimum),
        maximum=float_or_none(whole.maximum),
        average=float_or_none(whole.average_decimal),
        stddev_samp=stddev,
        stddev_quality=StddevQuality.EXACT,
    )


@dataclass(slots=True)
class _TimeWeightedState:
    """Merge disjoint PID spans by their covered time, retaining row counts."""

    count: int = 0
    weighted_value: Decimal = Decimal(0)
    covered_seconds: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def apply(self, record: Mapping[str, object]) -> None:
        count = record.get("sample_count")
        if count is not None:
            if type(count) is not int:
                raise TypeError("summary sample_count must be an integer")
            self.count += count
        weighted_value = record.get("weighted_value")
        if weighted_value is not None:
            self.weighted_value = exact_add(self.weighted_value, decimal(weighted_value))
        covered_seconds = record.get("covered_seconds")
        if covered_seconds is not None:
            self.covered_seconds = exact_add(self.covered_seconds, decimal(covered_seconds))
        minimum = record.get("min_value")
        if minimum is not None:
            value = decimal(minimum)
            self.minimum = value if self.minimum is None else min(self.minimum, value)
        maximum = record.get("max_value")
        if maximum is not None:
            value = decimal(maximum)
            self.maximum = value if self.maximum is None else max(self.maximum, value)

    @property
    def average_decimal(self) -> Decimal | None:
        if self.covered_seconds == 0:
            return None
        with localcontext() as context:
            context.prec = max(
                28,
                len(self.weighted_value.as_tuple().digits)
                + len(self.covered_seconds.as_tuple().digits)
                + 16,
            )
            return self.weighted_value / self.covered_seconds


def _time_weighted_overview_point(
    state: _TimeWeightedState, bound: tuple[datetime, datetime, bool]
) -> HistoryOverviewPoint:
    """Materialize one PID overview point from disjoint covered spans."""
    return HistoryOverviewPoint(
        start=bound[0],
        end=bound[1],
        average=float_or_none(state.average_decimal),
        minimum=float_or_none(state.minimum),
        maximum=float_or_none(state.maximum),
        sample_count=state.count,
        partial=bound[2],
    )


def _control_series_groups(
    rows: Sequence[asyncpg.Record],
    bounds: Sequence[tuple[datetime, datetime, bool]],
) -> tuple[tuple[HistoryOverviewControlSeries, ...], tuple[HistoryOverviewRequestedPidSeries, ...]]:
    """Build lights and requested-PID series from the same merged snapshot."""
    light_grouped: defaultdict[
        tuple[str, str, str, str], defaultdict[int, SummaryState]
    ] = defaultdict(lambda: defaultdict(SummaryState))
    pid_grouped: defaultdict[
        tuple[str, str, str, str], defaultdict[int, _TimeWeightedState]
    ] = defaultdict(lambda: defaultdict(_TimeWeightedState))
    for record in rows:
        identity = (
            record["family"],
            record["location"],
            record["cluster"],
            record["device_name"],
        )
        bucket_index = int(record["idx"])
        if record["family"] == "light":
            light_grouped[identity][bucket_index].apply(record)
        else:
            pid_grouped[identity][bucket_index].apply(record)

    lights: list[HistoryOverviewControlSeries] = []
    for identity in sorted(light_grouped):
        _, location, cluster, device_name = identity
        points = tuple(
            _overview_point(light_grouped[identity].get(index, SummaryState()), bound)
            for index, bound in enumerate(bounds)
        )
        lights.append(
            HistoryOverviewControlSeries(
                location=location,
                cluster=cluster,
                device_name=device_name,
                points=points,
            )
        )

    requested_pid: list[HistoryOverviewRequestedPidSeries] = []
    for identity in sorted(pid_grouped):
        _, location, cluster, device_name = identity
        points = tuple(
            _time_weighted_overview_point(
                pid_grouped[identity].get(index, _TimeWeightedState()), bound
            )
            for index, bound in enumerate(bounds)
        )
        requested_pid.append(
            HistoryOverviewRequestedPidSeries(
                location=location,
                cluster=cluster,
                device_name=device_name,
                points=points,
            )
        )
    return tuple(lights), tuple(requested_pid)


def _relay_series_groups(
    rows: Sequence[asyncpg.Record],
    bounds: Sequence[tuple[datetime, datetime, bool]],
) -> tuple[HistoryOverviewRelaySeries, ...]:
    """Build honest recorded-lane duration series with unknown fill."""
    lane_buckets: defaultdict[
        tuple[str, str, int, int, str, str], defaultdict[int, tuple[int, int]]
    ] = defaultdict(lambda: defaultdict(lambda: (0, 0)))
    for record in rows:
        identity = (
            record["location"],
            record["cluster"],
            int(record["channel"]),
            int(record["device_id"]),
            record["device_name"],
            record["device_type"],
        )
        existing = lane_buckets[identity][int(record["idx"])]
        lane_buckets[identity][int(record["idx"])] = (
            existing[0] + int(record["on_microseconds"]),
            existing[1] + int(record["off_microseconds"]),
        )
    series: list[HistoryOverviewRelaySeries] = []
    for identity in sorted(lane_buckets):
        location, cluster, channel, device_id, device_name, device_type = identity
        points: list[HistoryOverviewRelayPoint] = []
        known = lane_buckets[identity]
        for index, bound in enumerate(bounds):
            start, end, partial = bound
            on_us, off_us = known.get(index, (0, 0))
            elapsed_us = timedelta_microseconds(end - start)
            unknown_us = elapsed_us - on_us - off_us
            points.append(
                HistoryOverviewRelayPoint(
                    start=start,
                    end=end,
                    on_seconds=float(decimal(on_us) / _MICROSCALE),
                    off_seconds=float(decimal(off_us) / _MICROSCALE),
                    unknown_seconds=float(decimal(unknown_us) / _MICROSCALE),
                    coverage_complete=unknown_us == 0,
                    partial=partial,
                )
            )
        series.append(
            HistoryOverviewRelaySeries(
                location=location,
                cluster=cluster,
                channel=channel,
                device_id=device_id,
                device_name=device_name,
                device_type=device_type,
                points=tuple(points),
            )
        )
    return tuple(series)
