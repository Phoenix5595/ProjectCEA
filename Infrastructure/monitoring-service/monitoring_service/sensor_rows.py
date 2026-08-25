"""Canonical node mapping and row conversion for sensor read queries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC

import asyncpg

from monitoring_service.sensor_models import (
    MonitoringUnavailableError,
    Node,
    SensorSeries,
    SensorStatistics,
    SeriesPoint,
    StddevQuality,
    UnitFamily,
)
from shared.cluster_topology import sensor_name_like_pattern


def node_mapping_args(room: str, nodes: tuple[Node, ...]) -> tuple[str, str, str, str]:
    """Build the two fixed node/pattern slots used by the query VALUES CTE."""
    node_patterns = tuple(
        (node.value, sensor_name_like_pattern(room, node.value) or "") for node in nodes
    )
    match node_patterns:
        case ((first_node, first_pattern),):
            return first_node, first_pattern, "", ""
        case ((first_node, first_pattern), (second_node, second_pattern)):
            return first_node, first_pattern, second_node, second_pattern
        case _:
            raise MonitoringUnavailableError(f"unsupported monitoring node mapping for {room}")


def series_from_rows(
    rows: Sequence[asyncpg.Record], nodes: tuple[Node, ...]
) -> tuple[SensorSeries, ...]:
    """Split SQL-labeled rows into legacy-ordered per-node sensor series."""
    grouped: defaultdict[tuple[Node, str, str, str], list[SeriesPoint]] = defaultdict(list)
    for row in rows:
        node = Node(row["node"])
        grouped[(node, row["sensor"], row["unit"], row["data_type"])].append(
            SeriesPoint(
                timestamp=row["bucket"].astimezone(UTC),
                average=float(row["average"]),
                minimum=float(row["minimum"]),
                maximum=float(row["maximum"]),
                sample_count=int(row["sample_count"]),
            )
        )
    return tuple(
        SensorSeries(
            sensor=sensor,
            node=node,
            unit_family=unit_family(kind),
            unit=unit,
            points=tuple(sorted(points, key=lambda point: point.timestamp)),
        )
        for (node, sensor, unit, kind), points in sorted(
            grouped.items(), key=lambda item: (nodes.index(item[0][0]), *item[0][1:])
        )
        if kind in unit_families()
    )


def statistics_from_rows(
    rows: Sequence[asyncpg.Record], nodes: tuple[Node, ...], stddev_quality: StddevQuality
) -> tuple[SensorStatistics, ...]:
    """Build canonical-node statistics from rows labeled by the query mapping."""
    return tuple(
        SensorStatistics(
            sensor=row["sensor"],
            node=Node(row["node"]),
            minimum=float(row["minimum"]),
            maximum=float(row["maximum"]),
            average=float(row["average"]),
            stddev_samp=float(row["stddev_samp"]),
            sample_count=int(row["sample_count"]),
            stddev_quality=stddev_quality,
        )
        for row in sorted(rows, key=lambda row: (nodes.index(Node(row["node"])), row["sensor"]))
    )


def unit_families() -> dict[str, UnitFamily]:
    """Return the supported data-type to API-unit-family mapping."""
    return {
        "temperature": UnitFamily.CELSIUS,
        "humidity": UnitFamily.PERCENT,
        "vpd": UnitFamily.KPA,
        "pressure_deficit": UnitFamily.KPA,
        "co2": UnitFamily.PPM,
        "pressure": UnitFamily.HPA,
        "water_level": UnitFamily.MM,
    }


def unit_family(kind: str) -> UnitFamily:
    """Resolve a supported sensor data type into its wire unit family."""
    families = unit_families()
    try:
        return families[kind]
    except KeyError as exc:
        raise MonitoringUnavailableError(f"unsupported monitoring sensor type: {kind}") from exc
