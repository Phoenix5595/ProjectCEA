"""Offline proof for legacy event-time archive descriptors and annual cutover merges."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any, Mapping
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SERVICE_ROOT = HERE.parent
INFRASTRUCTURE = SERVICE_ROOT.parent
for _entry in (str(SERVICE_ROOT), str(INFRASTRUCTURE), str(INFRASTRUCTURE / "monitoring-service")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from grow_archive_service.annual import (  # noqa: E402
    AnnualComponentInput,
    build_annual_zip,
    schema_descriptors_compatible,
)
from grow_archive_service.binary_codec import canonical, write_header, write_row, write_trailer  # noqa: E402
from grow_archive_service.graphs import ArchiveGraphReader, _scan_zip_graph, _targets  # noqa: E402
from grow_archive_service.parquet_codec import (  # noqa: E402
    iter_archive_binary_rows,
    verify_archive,
    write_archive,
)
from grow_archive_service.pg_fields import encode  # noqa: E402
from grow_archive_service.schema import load_source_catalog  # noqa: E402

TORONTO = ZoneInfo("America/Toronto")
FIXTURES = HERE / "fixtures"
SOURCE_CATALOG = SERVICE_ROOT / "grow_archive_service" / "source_schema.json"
FACT_TABLES = ("monitoring_room_photoperiod", "monitoring_photoperiod_coverage")

RENAMED_TIME_COLUMNS = {
    "automation_state": "timestamp",
    "effective_setpoints": "timestamp",
    "control_history": "timestamp",
    "pid_parameter_history": "timestamp",
    "relay_observation": "observed_at",
    "monitoring_room_photoperiod": "observed_at",
    "monitoring_photoperiod_coverage": "observed_at",
    "mode_transition_history": "triggered_at",
}


def _legacy_automation_descriptor(current: Mapping[str, Any]) -> dict[str, Any]:
    descriptor = deepcopy(current)
    descriptor["time_column"] = "timestamp"
    for column in descriptor["columns"]:
        if column["name"] == "time":
            column["name"] = "timestamp"
    for key in ("primary_key", "primary_key_variants", "unique_constraints"):
        groups = descriptor.get(key) or []
        for group in groups:
            for index, column in enumerate(group):
                if column == "time":
                    group[index] = "timestamp"
    for index in descriptor["indexes"]:
        for position, column in enumerate(index["columns"]):
            if column == "time":
                index["columns"][position] = "timestamp"
            elif column == "time DESC":
                index["columns"][position] = "timestamp DESC"
    descriptor["notes"] = descriptor["notes"].replace("(time, id)", "(timestamp, id)")
    descriptor["baseline_incompatibility"]["disposable_variant"] = ["timestamp", "id"]
    fingerprint_payload = {
        "relation": descriptor["relation"],
        "columns": [
            [column["name"], column["type"], column["not_null"], column["generation"]]
            for column in descriptor["columns"]
        ],
        "primary_key": descriptor["primary_key"],
        "primary_key_variants": descriptor["primary_key_variants"],
        "unique_constraints": descriptor["unique_constraints"],
        "hypertable": descriptor["hypertable"],
    }
    descriptor["fingerprint"] = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return descriptor


class _NoopControl:
    @staticmethod
    def checkpoint() -> None:
        return None


def _fact_values(
    table: str, descriptor: Mapping[str, Any], instant: datetime, ordinal: int
) -> dict[str, Any]:
    event_time = descriptor["time_column"]
    if table == "monitoring_room_photoperiod":
        return {
            "id": 100 + ordinal,
            event_time: instant,
            "location": "Veg Room",
            "cluster": "main",
            "phase": "SUN",
            "mode_id": None,
            "submode_id": None,
            "runtime_snapshot_version": 1,
            "source": "calendar",
        }
    return {
        "id": 200 + ordinal,
        event_time: instant,
        "location": "Veg Room",
        "cluster": "main",
        "state": "available",
        "reason": "initial",
        "runtime_snapshot_version": 1,
    }


def _write_quarter(
    root: Path, quarter: int, descriptors: Mapping[str, Mapping[str, Any]]
) -> AnnualComponentInput:
    dataset_id = uuid4()
    revision = 1
    quarter_start_month = 1 + 3 * (quarter - 1)
    quarter_start = datetime(2024, quarter_start_month, 1, tzinfo=TORONTO)
    next_start = (
        datetime(2025, 1, 1, tzinfo=TORONTO)
        if quarter == 4
        else datetime(2024, quarter_start_month + 3, 1, tzinfo=TORONTO)
    )
    instant = quarter_start + timedelta(days=1, hours=12)
    capture = root / f"q{quarter}" / "capture"
    capture.mkdir(parents=True)
    parts: list[dict[str, Any]] = []
    for ordinal, table in enumerate(FACT_TABLES):
        descriptor = descriptors[table]
        values = _fact_values(table, descriptor, instant, quarter)
        source_row = tuple(
            encode(values[column["name"]], column["type"]) for column in descriptor["columns"]
        )
        for role in ("observation", "graph"):
            fields = source_row + (
                dataset_id.bytes,
                encode(revision, "bigint"),
                encode(0, "integer"),
                encode(role, "text"),
            )
            relative = f"{table}-{role}.copy"
            with (capture / relative).open("wb") as output:
                write_header(output)
                write_row(output, fields)
                write_trailer(output)
            part: dict[str, Any] = {
                "source_table": table,
                "record_role": role,
                "path": relative,
                "row_count": 1,
                "canonical_sha256": hashlib.sha256(canonical(fields)).hexdigest(),
                "partition_field": False,
            }
            if role == "observation":
                part["toronto_date"] = instant.date().isoformat()
            else:
                part["graph_kind"] = "photoperiod" if table == FACT_TABLES[0] else "coverage"
            parts.append(part)
    source_schema = {table: descriptors[table] for table in FACT_TABLES}
    draft = {
        "dataset_id": str(dataset_id),
        "revision": revision,
        "calendar_revision": 1,
        "calendar_year": 2024,
        "quarter": quarter,
        "kind": "veg_quarter",
        "room": "Veg Room",
        "timezone": "America/Toronto",
        "available_from": quarter_start.isoformat(),
        "available_to": next_start.isoformat(),
        "coverage": "complete",
        "gaps": [],
        "absent_streams": [],
        "source_schema": source_schema,
        "graph_schema": source_schema,
        "parts": parts,
    }
    (capture / "metadata.json").write_text(json.dumps(draft), encoding="utf-8")
    archive_path = capture.parent / "quarter.zip"
    verified = write_archive(dataset_id, revision, archive_path)
    return AnnualComponentInput(
        dataset_id,
        revision,
        f"Q{quarter}/",
        archive_path,
        "",
        verified.sha256,
        verified.byte_size,
        verified.data,
    )


def _prove_legacy_graph(path: Path) -> None:
    verified = verify_archive(path)
    manifest = verified.data
    start = datetime(2024, 1, 2, 11, tzinfo=TORONTO)
    end = start + timedelta(hours=2)
    bounds, _base_width, interval, range_model, selection = _targets(
        start, end, overview=False, max_points=20
    )
    with tempfile.TemporaryDirectory(prefix="archive-time-graph-") as temporary:
        scratch = Path(temporary)
        states, _raw_pages, photoperiod, coverage = _scan_zip_graph(
            path,
            manifest,
            "",
            UUID(manifest["dataset_id"]),
            manifest["revision"],
            (),
            (),
            bounds,
            0,
            start,
            end,
            "Veg Room",
            scratch,
            _NoopControl(),
        )
        response = ArchiveGraphReader(None, None, scratch)._response(
            SimpleNamespace(dataset_id=UUID(manifest["dataset_id"]), revision=manifest["revision"]),
            manifest,
            "Veg Room",
            20,
            bounds,
            interval,
            range_model,
            selection,
            False,
            states,
            photoperiod,
            coverage,
        )
    assert len(response["photoperiod"]) == 1
    assert response["photoperiod"][0].time == datetime(2024, 1, 2, 17, tzinfo=UTC)
    assert len(response["photoperiod_coverage"]) == 1
    assert response["photoperiod_coverage"][0].time == datetime(2024, 1, 2, 17, tzinfo=UTC)
    assert "observed_at" not in response["photoperiod"][0].model_dump()
    assert "observed_at" not in response["photoperiod_coverage"][0].model_dump()


def main() -> None:
    legacy_v1_path = FIXTURES / "pre-cutover-schema-v1.zip"
    legacy_v1 = verify_archive(legacy_v1_path)
    v1_rows = [
        row
        for batch in iter_archive_binary_rows(legacy_v1_path, legacy_v1, "actuator_events")
        for row in batch
    ]
    assert len(v1_rows) == 1, "pre-cutover schema-v1 archive did not load its source row"

    legacy_v2_path = FIXTURES / "pre-cutover-schema-v2.zip"
    legacy_v2 = verify_archive(legacy_v2_path)
    assert all(
        descriptor["time_column"] == "observed_at"
        for descriptor in legacy_v2.data["source_schema"].values()
    )
    v2_rows = [
        row
        for batch in iter_archive_binary_rows(
            legacy_v2_path, legacy_v2, "monitoring_room_photoperiod"
        )
        for row in batch
    ]
    assert len(v2_rows) == 1, "pre-cutover schema-v2 archive did not load its source row"
    _prove_legacy_graph(legacy_v2_path)

    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    current_descriptors = {stream["name"]: stream for stream in catalog["streams"]}
    assert catalog["format"] == "sj-cea-source-schema/1" and catalog["version"] == 3
    for table, old_column in RENAMED_TIME_COLUMNS.items():
        descriptor = current_descriptors[table]
        names = [column["name"] for column in descriptor["columns"]]
        assert descriptor["time_column"] == "time" and "time" in names
        assert old_column not in names, f"{table} retains its old event-time column"
    load_source_catalog(SOURCE_CATALOG)

    assert schema_descriptors_compatible(
        _legacy_automation_descriptor(current_descriptors["automation_state"]),
        current_descriptors["automation_state"],
    )

    with tempfile.TemporaryDirectory(prefix="archive-time-annual-") as temporary:
        root = Path(temporary)
        components = [
            AnnualComponentInput(
                UUID(legacy_v2.dataset_id),
                legacy_v2.revision,
                "Q1/",
                legacy_v2_path,
                "",
                legacy_v2.sha256,
                legacy_v2.byte_size,
                legacy_v2.data,
            )
        ]
        components.extend(
            _write_quarter(root, quarter, current_descriptors) for quarter in (2, 3, 4)
        )
        annual_path = root / "annual.zip"
        build_annual_zip(
            uuid4(),
            1,
            calendar_revision=1,
            calendar_year=2024,
            room="Veg Room",
            components=components,
            destination=annual_path,
        )
        annual = verify_archive(annual_path)
        for table in FACT_TABLES:
            merged_descriptor = annual.data["source_schema"][table]
            assert merged_descriptor == current_descriptors[table]
            assert merged_descriptor["time_column"] == "time"
            names = [column["name"] for column in merged_descriptor["columns"]]
            assert "time" in names and "observed_at" not in names

    print(
        json.dumps(
            {
                "result": "PASS",
                "cases": [
                    "pre_cutover_v1_verify_load",
                    "pre_cutover_v2_verify_load_graph",
                    "legacy_to_current_annual_merge",
                    "newest_descriptor_retained",
                ],
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
