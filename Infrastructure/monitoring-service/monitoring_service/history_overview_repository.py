"""Bounded all-history monitoring overviews with exact source coverage gates.

``HistoryOverviewRepository`` serves the fixed-range all-history sensor and
control summaries defined by ``history_overview_models``. Each fetch is one
read-only statement whose own coverage/invalidation/watermark guards share
the row snapshot; a false guard means the exact 503 message
``history overview aggregate coverage is unavailable`` and nothing is
modeled. Contributions merge NUMERIC sufficient statistics across the
daily/hourly/raw tiers without prorating or double counting, relay durations
merge integer microseconds so on + off + unknown equals the exact bucket
elapsed time, and buckets without recorded owner time are honest unknown
time, never fabricated state.
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

_MICROSCALE: Final[Decimal] = Decimal(1_000_000)


class _OverviewDatabase(Protocol):
    async def fetch(
        self, query: str, *args: str | int | float | datetime | timedelta
    ) -> list[asyncpg.Record]: ...


@dataclass
class _SummaryState:
    """Additive NUMERIC sufficient statistics merged across source pieces."""

    count: int = 0
    value_sum: Decimal = Decimal(0)
    value_sum_squares: Decimal = Decimal(0)
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    present: bool = False

    def apply(self, record: Mapping[str, object] | asyncpg.Record) -> None:
        count = record.get("sample_count")
        if count is not None:
            assert isinstance(count, int), "overview sufficient-statistic count must be BIGINT"
            self.count += count
        value_sum = record.get("value_sum")
        if value_sum is not None:
            self.value_sum = _exact_add(self.value_sum, _decimal(value_sum))
        value_sum_squares = record.get("value_sum_squares")
        if value_sum_squares is not None:
            self.value_sum_squares = _exact_add(self.value_sum_squares, _decimal(value_sum_squares))
        minimum = record.get("min_value")
        if minimum is not None:
            minimum_decimal = _decimal(minimum)
            self.minimum = (
                minimum_decimal if self.minimum is None else min(self.minimum, minimum_decimal)
            )
        maximum = record.get("max_value")
        if maximum is not None:
            maximum_decimal = _decimal(maximum)
            self.maximum = (
                maximum_decimal if self.maximum is None else max(self.maximum, maximum_decimal)
            )
        if record.get("present") is True:
            self.present = True

    def absorb(self, state: _SummaryState) -> None:
        """Fold one whole-range state into another (statistics capture)."""
        self.count += state.count
        self.value_sum = _exact_add(self.value_sum, state.value_sum)
        self.value_sum_squares = _exact_add(self.value_sum_squares, state.value_sum_squares)
        if state.minimum is not None:
            self.minimum = (
                state.minimum if self.minimum is None else min(self.minimum, state.minimum)
            )
        if state.maximum is not None:
            self.maximum = (
                state.maximum if self.maximum is None else max(self.maximum, state.maximum)
            )
        self.present = self.present or state.present

    @property
    def average_decimal(self) -> Decimal | None:
        if self.count == 0:
            return None
        with localcontext() as context:
            context.prec = max(
                28, len(self.value_sum.as_tuple().digits) + len(str(self.count)) + 16
            )
            return self.value_sum / Decimal(self.count)


def _decimal(value: object) -> Decimal:
    """Convert a SQL NUMERIC value into the exact ``Decimal`` scale."""
    return Decimal(str(value))


def _exact_add(left: Decimal, right: Decimal) -> Decimal:
    """Keep NUMERIC sufficient statistics lossless across bucket merges."""
    with localcontext() as context:
        context.prec = max(
            28,
            max(left.adjusted(), right.adjusted())
            - min(int(left.as_tuple().exponent), int(right.as_tuple().exponent))
            + 2,
        )
        return left + right


def _float_or_none(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


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
        async with request_observation(tier="overview"):
            rows = await self._fetch_guarded(
                SENSOR_OVERVIEW_STATEMENT,
                room,
                *node_mapping,
                history_range.start,
                history_range.end,
                buckets_json,
            )
        grouped: defaultdict[tuple[Node, str, str, str], defaultdict[int, _SummaryState]] = (
            defaultdict(lambda: defaultdict(_SummaryState))
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
            whole = _SummaryState()
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
        requested_pid: tuple[HistoryOverviewControlSeries, ...] = ()
        relays: tuple[HistoryOverviewRelaySeries, ...] = ()
        async with request_observation(tier="overview"):
            if scope == "lights":
                light_rows = await self._fetch_guarded(
                    LIGHT_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
                )
                lights, _ = _control_series_groups(light_rows, bounds)
            else:
                control_rows = await self._fetch_guarded(
                    CONTROL_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
                )
                lights, requested_pid = _control_series_groups(control_rows, bounds)
                relay_rows = await self._fetch_guarded(
                    RELAY_OVERVIEW_STATEMENT,
                    location,
                    history_range.start,
                    history_range.end,
                    buckets_json,
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
    state: _SummaryState, bound: tuple[datetime, datetime, bool]
) -> HistoryOverviewPoint:
    """Materialize exactly one overview point from merged bucket pieces."""
    return HistoryOverviewPoint(
        start=bound[0],
        end=bound[1],
        average=_float_or_none(state.average_decimal),
        minimum=_float_or_none(state.minimum),
        maximum=_float_or_none(state.maximum),
        sample_count=state.count,
        partial=bound[2],
    )


def _sensor_statistics(
    sensor: str, node: Node, whole: _SummaryState
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
            minimum=_float_or_none(whole.minimum),
            maximum=_float_or_none(whole.maximum),
            average=_float_or_none(whole.average_decimal),
            stddev_samp=0.0,
            stddev_quality=StddevQuality.EXACT,
        )
    count = Decimal(whole.count)
    with localcontext() as context:
        # Cancellation at a large measurement offset must not erase real
        # variance. Reserve every input/product digit plus division headroom.
        context.prec = max(
            28,
            2 * len(whole.value_sum.as_tuple().digits) + len(str(whole.count)) + 16,
            len(whole.value_sum_squares.as_tuple().digits) + len(str(whole.count)) + 16,
        )
        variance = (whole.value_sum_squares - whole.value_sum * whole.value_sum / count) / (
            count - Decimal(1)
        )
        stddev = float(max(variance, Decimal(0)).sqrt())
    return HistoryOverviewSensorStatistics(
        sensor=sensor,
        node=node,
        sample_count=whole.count,
        minimum=_float_or_none(whole.minimum),
        maximum=_float_or_none(whole.maximum),
        average=_float_or_none(whole.average_decimal),
        stddev_samp=stddev,
        stddev_quality=StddevQuality.EXACT,
    )


def _control_series_groups(
    rows: Sequence[asyncpg.Record],
    bounds: Sequence[tuple[datetime, datetime, bool]],
) -> tuple[tuple[HistoryOverviewControlSeries, ...], tuple[HistoryOverviewControlSeries, ...]]:
    """Build the lights and requested_pid series from one merged snapshot."""
    grouped: defaultdict[tuple[str, str, str, str], defaultdict[int, _SummaryState]] = defaultdict(
        lambda: defaultdict(_SummaryState)
    )
    for record in rows:
        identity = (
            record["family"],
            record["location"],
            record["cluster"],
            record["device_name"],
        )
        grouped[identity][int(record["idx"])].apply(record)
    lights: list[HistoryOverviewControlSeries] = []
    requested_pid: list[HistoryOverviewControlSeries] = []
    for identity in sorted(grouped):
        family, location, cluster, device_name = identity
        points = tuple(
            _overview_point(grouped[identity].get(index, _SummaryState()), bound)
            for index, bound in enumerate(bounds)
        )
        series = HistoryOverviewControlSeries(
            location=location, cluster=cluster, device_name=device_name, points=points
        )
        if family == "light":
            lights.append(series)
        else:
            requested_pid.append(series)
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
                    on_seconds=float(_decimal(on_us) / _MICROSCALE),
                    off_seconds=float(_decimal(off_us) / _MICROSCALE),
                    unknown_seconds=float(_decimal(unknown_us) / _MICROSCALE),
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
