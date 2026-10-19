"""Lossless verification and child readers for four-quarter annual ZIPs.

Annual ZIPs are byte-preserving containers: a child's original members live
under its catalogued Q1/..Q4/ prefix and retain that child's UUID/revision.
This module deliberately verifies prefixed members directly; routing an annual
container through the ordinary single-dataset ZIP verifier would recurse once
the codec dispatch is installed, and extracting children would create an
unbounded copy of a potentially multi-GiB archive.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
from typing import Any, NoReturn
from uuid import UUID
import zipfile
from zoneinfo import ZoneInfo

from . import parquet_codec as _codec
from .arrow_fields import arrow_schema, binary_rows_from_batch
from .binary_codec import canonical
from .manifest import ArchiveManifest

FORMAT = _codec.FORMAT
MAX_MANIFEST = _codec.MAX_MANIFEST
MAX_MEMBER = _codec.MAX_MEMBER
MAX_TOTAL = _codec.MAX_TOTAL
MAX_ROWS = _codec.MAX_ROWS
MAX_BATCH_BYTES = _codec.MAX_BATCH_BYTES
TORONTO = ZoneInfo("America/Toronto")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_PREFIX = re.compile(r"^Q([1-4])/$")
_TABLE = re.compile(r"^[a-z_][a-z0-9_]*$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHECKPOINT_BLOCK = 1024 * 1024


class AnnualIntegrityError(_codec.ArchiveIntegrityError):
    """Annual container, child metadata, or typed member is inconsistent."""


class _CheckpointFailure(Exception):
    """Keep cancellation/checkpoint errors distinct from malformed ZIP errors."""

    def __init__(self, original: BaseException):
        super().__init__(str(original))
        self.original = original


def _bad(message: str) -> NoReturn:
    raise AnnualIntegrityError(message)


def _checkpoint(checkpoint: Callable[[], None] | None) -> None:
    if checkpoint is None:
        return
    try:
        checkpoint()
    except BaseException as error:
        raise _CheckpointFailure(error) from error


def _canonical_json(value: Any) -> bytes:
    try:
        return _codec._json_bytes(value)
    except (TypeError, ValueError, OverflowError, UnicodeError) as error:
        raise AnnualIntegrityError("invalid canonical annual JSON") from error


def canonical_json_bytes(value: Any) -> bytes:
    """Return the exact canonical JSON encoding used for manifest digests."""
    return _canonical_json(value)


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(manifest)).hexdigest()


def is_annual_archive_manifest(manifest: Mapping[str, Any]) -> bool:
    """Dispatch malformed component-bearing documents to the strict annual verifier."""
    return isinstance(manifest, Mapping) and (
        manifest.get("kind") == "veg_year" or "components" in manifest
    )


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _nonnegative_int(value: Any) -> bool:
    return type(value) is int and value >= 0


def _canonical_uuid(value: Any, label: str) -> UUID:
    if not isinstance(value, str):
        _bad(f"{label} UUID missing")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise AnnualIntegrityError(f"invalid {label} UUID") from error
    if str(parsed) != value:
        _bad(f"noncanonical {label} UUID")
    return parsed


def _parse_instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        _bad(f"{label} timestamp missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise AnnualIntegrityError(f"invalid {label} timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _bad(f"{label} timestamp must be timezone-aware")
    return parsed


def _quarter_bounds(year: int, quarter: int) -> tuple[datetime, datetime]:
    if not 1 <= year <= 9998 or quarter not in (1, 2, 3, 4):
        _bad("invalid annual calendar quarter")
    month = 1 + (quarter - 1) * 3
    start = datetime(year, month, 1, tzinfo=TORONTO)
    if quarter == 4:
        end = datetime(year + 1, 1, 1, tzinfo=TORONTO)
    else:
        end = datetime(year, month + 3, 1, tzinfo=TORONTO)
    return start, end


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _HEX.fullmatch(value) is None:
        _bad(f"invalid {label} SHA256")
    return value


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        _bad(f"{label} must be an object")
    return value


def _count_map(value: Any, label: str) -> dict[str, int]:
    raw = _mapping(value, label)
    result: dict[str, int] = {}
    for table, count in raw.items():
        if _TABLE.fullmatch(table) is None or not _nonnegative_int(count):
            _bad(f"invalid {label} entry")
        result[table] = count
    return result


def _per_day_map(value: Any, label: str) -> dict[str, dict[str, int]]:
    raw = _mapping(value, label)
    result: dict[str, dict[str, int]] = {}
    for table, days_value in raw.items():
        if _TABLE.fullmatch(table) is None:
            _bad(f"invalid {label} table")
        days = _mapping(days_value, f"{label} days")
        normalized: dict[str, int] = {}
        for day, count in days.items():
            if _DAY.fullmatch(day) is None or not _nonnegative_int(count):
                _bad(f"invalid {label} day/count")
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except ValueError as error:
                raise AnnualIntegrityError(f"invalid {label} date") from error
            normalized[day] = count
        result[table] = normalized
    return result


def _merge_schema(target: dict[str, Any], value: Any, label: str) -> None:
    schema = _mapping(value, f"{label} schema")
    for table, descriptor in schema.items():
        if _TABLE.fullmatch(table) is None or not isinstance(descriptor, Mapping):
            _bad(f"invalid {label} schema descriptor")
        existing = target.get(table)
        if existing is not None and _canonical_json(existing) != _canonical_json(descriptor):
            _bad(f"annual child {label} schema mismatch")
        target[table] = descriptor


def _child_specs(manifest: Mapping[str, Any]) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any], str], ...]:
    """Validate the annual envelope and return trusted (component, child, prefix) triples."""
    if manifest.get("format") != FORMAT or manifest.get("kind") != "veg_year":
        _bad("not an sj-cea-history/1 annual Veg manifest")
    _canonical_uuid(manifest.get("dataset_id"), "annual dataset")
    if not _positive_int(manifest.get("revision")) or not _positive_int(manifest.get("calendar_revision")):
        _bad("invalid annual revision")
    year = manifest.get("calendar_year")
    if type(year) is not int or not 1 <= year <= 9998:
        _bad("invalid annual calendar year")
    if manifest.get("timezone") != "America/Toronto":
        _bad("annual timezone mismatch")
    room = manifest.get("room")
    if room != "Veg Room":
        _bad("annual room mismatch")
    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != 4:
        _bad("annual must contain exactly four quarter components")

    parent_id = UUID(str(manifest["dataset_id"]))
    children: set[UUID] = set()
    result: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    for index, component_value in enumerate(components, start=1):
        component = _mapping(component_value, "annual component")
        prefix = component.get("member_prefix")
        if not isinstance(prefix, str):
            _bad("annual component prefix must be a string")
        match = _PREFIX.fullmatch(prefix)
        if match is None or int(match.group(1)) != index:
            _bad("annual component prefixes must be ordered Q1/ through Q4/")
        child_value = component.get("manifest")
        child = _mapping(child_value, "component manifest")
        child_id = _canonical_uuid(component.get("dataset_id"), "component")
        if child_id == parent_id or child_id in children:
            _bad("annual component dataset identity is duplicated")
        children.add(child_id)
        if not _positive_int(component.get("revision")):
            _bad("invalid component revision")
        revision = component["revision"]
        if (child.get("format") != FORMAT or child.get("kind") != "veg_quarter"
                or child.get("dataset_id") != str(child_id) or child.get("revision") != revision
                or child.get("timezone") != "America/Toronto" or child.get("room") != room):
            _bad("component manifest identity, kind, room, or timezone mismatch")
        if not _positive_int(revision) or not _positive_int(child.get("revision")):
            _bad("invalid component manifest revision")
        year_in_child = child.get("calendar_year", year)
        quarter_in_child = child.get("quarter", index)
        if (type(year_in_child) is not int or type(quarter_in_child) is not int
                or year_in_child != year or quarter_in_child != index):
            _bad("component calendar year/quarter mismatch")
        _digest(component.get("sha256"), "component ZIP")
        if not _positive_int(component.get("byte_size")) or component["byte_size"] > MAX_TOTAL:
            _bad("invalid component ZIP size")
        _digest(component.get("manifest_sha256"), "component manifest")
        if manifest_sha256(child) != component["manifest_sha256"]:
            _bad("component manifest digest mismatch")
        try:
            _codec._json_safe(child)
        except _codec.ArchiveIntegrityError as error:
            raise AnnualIntegrityError("unsafe component manifest") from error

        start, end = _quarter_bounds(year, index)
        child_start = _parse_instant(child.get("available_from"), "component available_from")
        child_end = _parse_instant(child.get("available_to"), "component available_to")
        if child_start < start or child_end > end or child_start >= child_end:
            _bad("component availability falls outside its Toronto calendar quarter")
        result.append((component, child, prefix))

    annual_start, _ = _quarter_bounds(year, 1)
    _, annual_end = _quarter_bounds(year, 4)
    parent_start = _parse_instant(manifest.get("available_from"), "annual available_from")
    parent_end = _parse_instant(manifest.get("available_to"), "annual available_to")
    if parent_start != annual_start or parent_end != annual_end:
        _bad("annual availability does not match its Toronto calendar year")
    if manifest.get("original_anniversary_at") is not None:
        _bad("Veg annual archive must not have a grow anniversary")
    return tuple(result)


def annual_component(manifest: Mapping[str, Any] | ArchiveManifest,
                     component_dataset_id: UUID) -> tuple[Mapping[str, Any], str]:
    """Resolve a child by immutable catalog UUID, never by a caller-supplied prefix.

    ``manifest`` must come from a completed annual verification. The returned
    child manifest and prefix are the exact values embedded in that container.
    """
    data = manifest.data if isinstance(manifest, ArchiveManifest) else manifest
    if not isinstance(component_dataset_id, UUID):
        _bad("component lookup requires a UUID")
    specs = _child_specs(_mapping(data, "annual manifest"))
    found = [(child, prefix) for component, child, prefix in specs
             if UUID(str(component["dataset_id"])) == component_dataset_id]
    if len(found) != 1:
        _bad("annual component dataset is absent or ambiguous")
    return found[0]


def annual_member_inventory(manifest: Mapping[str, Any] | ArchiveManifest
                            ) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any], str], ...]:
    """Return trusted (component, child inventory entry, prefixed name) triples."""
    data = manifest.data if isinstance(manifest, ArchiveManifest) else manifest
    specs = _child_specs(_mapping(data, "annual manifest"))
    inventory: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    seen: set[str] = set()
    for component, child, prefix in specs:
        entries = child.get("members")
        if not isinstance(entries, list) or not entries:
            _bad("component member inventory missing")
        schema_count = 0
        for entry_value in entries:
            entry = _validated_member_entry(entry_value, child)
            schema_count += entry["record_role"] == "schema"
            target = prefix + entry["path"]
            if target in seen:
                _bad("duplicate annual member path")
            seen.add(target)
            inventory.append((component, entry, target))
        if schema_count != 1:
            _bad("component must contain exactly one child schema.json")
    return tuple(inventory)


def _validated_member_entry(value: Any, manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    entry = _mapping(value, "component member")
    name = entry.get("path")
    if not isinstance(name, str):
        _bad("component member path must be a string")
    if not _codec._safe_member(name) or not _codec._member_allowed(entry, manifest):
        _bad("component member allowlist mismatch")
    role = entry.get("record_role")
    if role not in ("schema", "observation", "context", "graph"):
        _bad("invalid component record role")
    if not _nonnegative_int(entry.get("row_count")) or not _nonnegative_int(entry.get("decoded_bytes")):
        _bad("invalid component row/decoded count")
    if not _positive_int(entry.get("byte_size")) or entry["byte_size"] > MAX_MEMBER:
        _bad("component member size limit exceeded")
    for key in ("sha256", "canonical_sha256", "schema_sha256"):
        _digest(entry.get(key), f"component member {key}")
    if role == "schema":
        if (name != "schema.json" or entry.get("source_table") is not None
                or entry["row_count"] != 0 or entry["decoded_bytes"] != entry["byte_size"]
                or entry["sha256"] != entry["canonical_sha256"]
                or entry["sha256"] != entry["schema_sha256"]):
            _bad("invalid component schema inventory entry")
    else:
        if not isinstance(entry.get("source_table"), str) or _TABLE.fullmatch(entry["source_table"]) is None:
            _bad("invalid component source table")
        if not isinstance(entry.get("partition_field", False), bool):
            _bad("invalid component partition flag")
        if role == "observation":
            day = entry.get("toronto_date")
            if not isinstance(day, str) or _DAY.fullmatch(day) is None:
                _bad("observation Toronto day missing")
            try:
                datetime.strptime(day, "%Y-%m-%d")
            except ValueError as error:
                raise AnnualIntegrityError("invalid observation Toronto day") from error
        elif entry.get("toronto_date") is not None:
            _bad("non-observation member has a Toronto day")
        if not name.endswith(".parquet"):
            _bad("component row member is not Parquet")
    return entry


def _validate_annual_metadata(manifest: Mapping[str, Any], annual_schema_size: int) -> tuple[
        tuple[tuple[Mapping[str, Any], Mapping[str, Any], str], ...], dict[str, Any]]:
    specs = _child_specs(manifest)
    annual_observations: dict[str, int] = {}
    annual_contexts: dict[str, int] = {}
    annual_days: dict[str, dict[str, int]] = {}
    source_schema: dict[str, Any] = {}
    graph_schema: dict[str, Any] = {}
    absent_streams: set[str] = set()
    gaps: list[Any] = []
    coverage = "complete"
    child_decoded_total = 0

    for _component, child, _prefix in specs:
        _merge_schema(source_schema, child.get("source_schema"), "source")
        _merge_schema(graph_schema, child.get("graph_schema"), "graph")
        observations = _count_map(child.get("observation_counts"), "component observation counts")
        contexts = _count_map(child.get("context_counts"), "component context counts")
        days = _per_day_map(child.get("per_day_counts"), "component per-day counts")
        if not _positive_int(child.get("decoded_bytes")):
            _bad("invalid component decoded byte count")
        child_decoded_total += child["decoded_bytes"]
        if child_decoded_total > MAX_TOTAL:
            _bad("annual decoded budget exceeded")
        for table, count in observations.items():
            annual_observations[table] = annual_observations.get(table, 0) + count
        for table, count in contexts.items():
            annual_contexts[table] = annual_contexts.get(table, 0) + count
        for table, table_days in days.items():
            merged = annual_days.setdefault(table, {})
            for day, count in table_days.items():
                merged[day] = merged.get(day, 0) + count
        absent = child.get("absent_streams")
        if not isinstance(absent, list) or any(not isinstance(item, str) for item in absent):
            _bad("invalid component absent-stream inventory")
        absent_streams.update(absent)
        child_gaps = child.get("gaps")
        if not isinstance(child_gaps, list):
            _bad("invalid component gaps")
        gaps.extend(child_gaps)
        child_coverage = child.get("coverage")
        if child_coverage not in ("complete", "gapped", "unknown"):
            _bad("invalid component coverage")
        if child_coverage == "gapped":
            coverage = "gapped"
        elif child_coverage != "complete" and coverage == "complete":
            coverage = "unknown"

    schema_document = {"source_schema": source_schema, "graph_schema": graph_schema}
    if _canonical_json(manifest.get("source_schema")) != _canonical_json(source_schema):
        _bad("annual source schema differs from its components")
    if _canonical_json(manifest.get("graph_schema")) != _canonical_json(graph_schema):
        _bad("annual graph schema differs from its components")
    if _count_map(manifest.get("observation_counts"), "annual observation counts") != annual_observations:
        _bad("annual observation totals differ from its components")
    if _count_map(manifest.get("context_counts"), "annual context counts") != annual_contexts:
        _bad("annual context totals differ from its components")
    if _per_day_map(manifest.get("per_day_counts"), "annual per-day counts") != annual_days:
        _bad("annual per-day totals differ from its components")
    absent_value = manifest.get("absent_streams")
    if absent_value != sorted(absent_streams):
        _bad("annual absent-stream inventory differs from its components")
    if _canonical_json(manifest.get("gaps")) != _canonical_json(gaps):
        _bad("annual gaps differ from its components")
    if manifest.get("coverage") != coverage:
        _bad("annual coverage differs from its components")
    expected_decoded = child_decoded_total + annual_schema_size
    if (expected_decoded > MAX_TOTAL or not _nonnegative_int(manifest.get("decoded_bytes"))
            or manifest["decoded_bytes"] != expected_decoded):
        _bad("annual decoded byte total mismatch")
    return specs, schema_document


def _read_limited(archive: zipfile.ZipFile, name: str, info: zipfile.ZipInfo,
                  limit: int, label: str) -> bytes:
    if info.file_size > limit:
        _bad(f"{label} size limit exceeded")
    with archive.open(name, "r") as member:
        data = member.read(limit + 1)
    if len(data) != info.file_size or len(data) > limit:
        _bad(f"{label} size mismatch")
    return data


def _parse_canonical_json(data: bytes, label: str) -> Any:
    try:
        value = json.loads(data)
    except (TypeError, ValueError, UnicodeError) as error:
        raise AnnualIntegrityError(f"invalid {label} JSON") from error
    if _canonical_json(value) != data:
        _bad(f"noncanonical {label} JSON")
    try:
        _codec._json_safe(value)
    except _codec.ArchiveIntegrityError as error:
        raise AnnualIntegrityError(f"unsafe {label} metadata") from error
    return value


def _validate_zip_inventory(archive: zipfile.ZipFile) -> tuple[list[zipfile.ZipInfo], dict[str, zipfile.ZipInfo]]:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > 100000 or len(names) != len(set(names)):
        _bad("duplicate or excessive annual ZIP inventory")
    total = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for entry in entries:
        mode = stat.S_IFMT(entry.external_attr >> 16)
        if (not _codec._safe_member(entry.filename) or mode == stat.S_IFLNK
                or mode not in (0, stat.S_IFREG) or entry.flag_bits & 0x1
                or entry.file_size > MAX_MEMBER or entry.compress_type != zipfile.ZIP_STORED):
            _bad("unsafe or unsupported annual ZIP member")
        total += entry.file_size
        if total > MAX_TOTAL:
            _bad("annual decoded member budget exceeded")
        by_name[entry.filename] = entry
    if len(entries) < 2 or names[-1] != "manifest.json" or "schema.json" not in by_name:
        _bad("annual manifest must be last and schema.json must exist")
    if by_name["manifest.json"].file_size > MAX_MANIFEST or by_name["schema.json"].file_size > MAX_MANIFEST:
        _bad("annual manifest/schema limit exceeded")
    return entries, by_name


def _hash_member_steps(archive: zipfile.ZipFile, name: str,
                       checkpoint: Callable[[], None] | None) -> Iterator[dict[str, Any]]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name, "r") as member:
        while block := member.read(_CHECKPOINT_BLOCK):
            digest.update(block)
            size += len(block)
            if size > MAX_MEMBER:
                _bad("annual member size limit exceeded")
            _checkpoint(checkpoint)
            yield {"phase": "member_hash", "member": name, "bytes": size}
    return size, digest.hexdigest()


def _verify_parquet_member_steps(archive: zipfile.ZipFile, name: str,
                                 entry: Mapping[str, Any], child: Mapping[str, Any],
                                 dataset_id: UUID, revision: int,
                                 checkpoint: Callable[[], None] | None) -> Iterator[dict[str, Any]]:
    pa, pq = _codec._arrow()
    role = entry["record_role"]
    table = entry["source_table"]
    day = entry.get("toronto_date")
    schema_set = child["graph_schema"] if role == "graph" else child["source_schema"]
    descriptor = schema_set[table]
    columns = _codec._columns(descriptor, entry.get("partition_field", False))
    expected_schema = arrow_schema(pa, columns, descriptor, dataset_id=dataset_id,
                                   revision=revision, source_table=table, record_role=role)
    inverse = hashlib.sha256()
    count = 0
    decoded_bytes = 0
    with archive.open(name, "r") as member:
        parquet = pq.ParquetFile(member)
        if (not parquet.schema_arrow.equals(expected_schema, check_metadata=True)
                or hashlib.sha256(parquet.schema_arrow.serialize().to_pybytes()).hexdigest()
                != entry["schema_sha256"]):
            _bad("annual child typed schema mismatch")
        if parquet.metadata.num_rows != entry["row_count"]:
            _bad("annual child Parquet row count mismatch")
        for group in range(parquet.num_row_groups):
            metadata = parquet.metadata.row_group(group)
            if metadata.num_rows > 65536 or metadata.total_byte_size > 64 * 1024 * 1024:
                _bad("annual child Parquet row-group limit exceeded")
        for batch in parquet.iter_batches(batch_size=MAX_ROWS):
            for binary in binary_rows_from_batch(batch, columns):
                _codec._validate_row(binary, columns, descriptor, dataset_id, revision, role, day)
                encoded = canonical(binary)
                if len(encoded) > MAX_BATCH_BYTES:
                    _bad("annual child row exceeds bounded codec batch")
                inverse.update(encoded)
                decoded_bytes += len(encoded)
                count += 1
            _checkpoint(checkpoint)
            yield {"phase": "binary_roundtrip", "member": name, "rows": count}
    if (count != entry["row_count"] or decoded_bytes != entry["decoded_bytes"]
            or inverse.hexdigest() != entry["canonical_sha256"]):
        _bad("annual child original binary fields, multiplicity, or decoded bytes changed")
    return count, decoded_bytes


def _verify_child_member_steps(archive: zipfile.ZipFile, name: str,
                               entry: Mapping[str, Any], child: Mapping[str, Any],
                               dataset_id: UUID, revision: int,
                               checkpoint: Callable[[], None] | None
                               ) -> Iterator[dict[str, Any]]:
    member_size, member_sha = yield from _hash_member_steps(archive, name, checkpoint)
    if member_size != entry["byte_size"] or member_sha != entry["sha256"]:
        _bad("annual child original member hash/size mismatch")
    if entry["record_role"] == "schema":
        expected = _canonical_json({"source_schema": child["source_schema"],
                                    "graph_schema": child["graph_schema"]})
        info = archive.getinfo(name)
        actual = _read_limited(archive, name, info, MAX_MANIFEST, "child schema")
        if actual != expected:
            _bad("annual child schema member differs from its manifest")
        return 0, member_size
    return (yield from _verify_parquet_member_steps(
        archive, name, entry, child, dataset_id, revision, checkpoint))


def _verify_child_totals(child: Mapping[str, Any], observed: dict[str, int],
                         contexts: dict[str, int], per_day: dict[str, dict[str, int]],
                         decoded: int) -> None:
    if observed != _count_map(child.get("observation_counts"), "component observations"):
        _bad("component observation counts differ from Parquet rows")
    if contexts != _count_map(child.get("context_counts"), "component contexts"):
        _bad("component context counts differ from Parquet rows")
    if per_day != _per_day_map(child.get("per_day_counts"), "component per-day counts"):
        _bad("component day totals differ from Parquet rows")
    if decoded != child.get("decoded_bytes"):
        _bad("component decoded byte total differs from Parquet rows")


def iter_verify_annual_archive(path: Path, expected_sha256: str | None = None,
                               checkpoint: Callable[[], None] | None = None
                               ) -> Iterator[dict[str, Any]]:
    """Yield bounded annual ZIP verification steps; consume through ``complete``.

    The verifier hashes the whole file and each original child member, checks
    every embedded child manifest/schema/count, and decodes every Parquet row
    back to the canonical PostgreSQL binary-field tuple. No child archive or
    annual ZIP is extracted or materialized in memory.
    """
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_file():
            _bad("annual archive path is not an owned regular file")
        byte_size = path.stat().st_size
    except OSError as error:
        raise AnnualIntegrityError("annual archive is unavailable") from error
    if byte_size <= 0 or byte_size > MAX_TOTAL:
        _bad("annual archive file size limit exceeded")
    if expected_sha256 is not None:
        _digest(expected_sha256, "expected annual archive")

    whole = hashlib.sha256()
    read_size = 0
    try:
        with path.open("rb") as source:
            while block := source.read(_CHECKPOINT_BLOCK):
                whole.update(block)
                read_size += len(block)
                if read_size > MAX_TOTAL:
                    _bad("annual archive file size limit exceeded")
                _checkpoint(checkpoint)
                yield {"phase": "archive_hash", "bytes": read_size}
    except _CheckpointFailure as failure:
        raise failure.original
    except OSError as error:
        raise AnnualIntegrityError("annual archive could not be read") from error
    sha256 = whole.hexdigest()
    if read_size != byte_size or (expected_sha256 is not None and sha256 != expected_sha256):
        _bad("annual whole-file hash/size mismatch")

    try:
        archive = zipfile.ZipFile(path, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise AnnualIntegrityError("invalid annual ZIP") from error

    try:
        with archive:
            _entries, by_name = _validate_zip_inventory(archive)
            manifest_raw = _read_limited(archive, "manifest.json", by_name["manifest.json"],
                                         MAX_MANIFEST, "annual manifest")
            manifest_value = _parse_canonical_json(manifest_raw, "annual manifest")
            manifest = _mapping(manifest_value, "annual manifest")
            if not is_annual_archive_manifest(manifest) or manifest.get("kind") != "veg_year":
                _bad("annual manifest dispatch mismatch")
            specs = _child_specs(manifest)

            annual_schema_raw = _read_limited(archive, "schema.json", by_name["schema.json"],
                                              MAX_MANIFEST, "annual schema")
            annual_schema = _parse_canonical_json(annual_schema_raw, "annual schema")
            _annual_specs, expected_schema_doc = _validate_annual_metadata(manifest, len(annual_schema_raw))
            if annual_schema != expected_schema_doc:
                _bad("annual schema document mismatch")
            if annual_schema_raw != _canonical_json(expected_schema_doc):
                _bad("annual schema is not the canonical merged child schema")

            inventory = annual_member_inventory(manifest)
            expected_names = {"manifest.json", "schema.json"}
            expected_names.update(name for _component, _entry, name in inventory)
            if set(by_name) != expected_names:
                _bad("annual ZIP contains missing or unlisted members")
            if by_name["manifest.json"].filename != "manifest.json":
                _bad("annual root manifest missing")

            for component, child, prefix in specs:
                child_id = UUID(str(component["dataset_id"]))
                revision = int(component["revision"])
                child_observations: dict[str, int] = {}
                child_contexts: dict[str, int] = {}
                child_days: dict[str, dict[str, int]] = {}
                child_decoded = 0
                for entry_value in child["members"]:
                    entry = _validated_member_entry(entry_value, child)
                    name = prefix + entry["path"]
                    if name not in by_name:
                        _bad("annual prefixed child member missing")
                    count, decoded = yield from _verify_child_member_steps(
                        archive, name, entry, child, child_id, revision, checkpoint)
                    child_decoded += decoded
                    if entry["record_role"] == "observation":
                        table = entry["source_table"]
                        day = entry["toronto_date"]
                        child_observations[table] = child_observations.get(table, 0) + count
                        dates = child_days.setdefault(table, {})
                        dates[day] = dates.get(day, 0) + count
                    elif entry["record_role"] == "context":
                        table = entry["source_table"]
                        child_contexts[table] = child_contexts.get(table, 0) + count
                _verify_child_totals(child, child_observations, child_contexts, child_days, child_decoded)
                _checkpoint(checkpoint)
                yield {"phase": "component_verified", "dataset_id": str(child_id),
                       "revision": revision, "member_prefix": prefix}
            yield {"complete": True, "manifest": manifest, "sha256": sha256,
                   "byte_size": byte_size}
    except _CheckpointFailure as failure:
        raise failure.original
    except AnnualIntegrityError:
        raise
    except _codec.ArchiveIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError,
            zipfile.BadZipFile, RuntimeError, EOFError, OSError) as error:
        raise AnnualIntegrityError("invalid annual structure or typed member") from error


def verify_annual_archive(path: Path, expected_sha256: str | None = None) -> ArchiveManifest:
    """Convenience verifier matching ``parquet_codec.verify_archive``."""
    result = None
    for step in iter_verify_annual_archive(path, expected_sha256):
        if step.get("complete"):
            result = step
    if result is None:
        raise AnnualIntegrityError("annual verification incomplete")
    return ArchiveManifest(result["manifest"], result["sha256"], result["byte_size"])


def _zip_member_matches(info: zipfile.ZipInfo, expected_name: str) -> None:
    mode = stat.S_IFMT(info.external_attr >> 16)
    if (info.filename != expected_name or not _codec._safe_member(info.filename)
            or mode not in (0, stat.S_IFREG) or info.flag_bits & 0x1
            or info.compress_type != zipfile.ZIP_STORED or info.file_size > MAX_MEMBER):
        _bad("annual reader member is unsafe or changed")


def _iter_component_binary_rows(path: Path, verified: ArchiveManifest, component_dataset_id: UUID,
                                *, source_table: str | None = None, graph_kind: str | None = None,
                                include_member_info: bool = False,
                                checkpoint: Callable[[], None] | None = None
                                ) -> Iterator[Any]:
    if Path(path).is_symlink() or Path(path).stat().st_size != verified.byte_size:
        _bad("verified annual ZIP size changed")
    if not isinstance(verified.sha256, str) or _HEX.fullmatch(verified.sha256) is None:
        _bad("verified annual ZIP digest is invalid")
    child, prefix = annual_component(verified, component_dataset_id)
    if source_table is not None and _TABLE.fullmatch(source_table) is None:
        _bad("invalid annual source table")
    if graph_kind is not None and graph_kind not in ("sensor", "light", "photoperiod", "coverage"):
        _bad("invalid annual graph kind")
    dataset_id = UUID(str(child["dataset_id"]))
    revision = int(child["revision"])
    pa, pq = _codec._arrow()

    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                _bad("duplicate annual reader members")
            for member_name in names:
                if not _codec._safe_member(member_name):
                    _bad("unsafe annual reader path")
            expected_names = {"manifest.json", "schema.json"}
            expected_names.update(name for _component, _entry, name in annual_member_inventory(verified))
            if set(names) != expected_names:
                _bad("annual reader member allowlist changed")
            # Recheck small manifest/schema metadata so a changed same-size
            # container cannot redirect a catalogued UUID after verification.
            manifest_raw = _read_limited(archive, "manifest.json", archive.getinfo("manifest.json"),
                                         MAX_MANIFEST, "annual manifest")
            if manifest_raw != _canonical_json(verified.data):
                _bad("annual manifest changed after verification")
            global_schema = _read_limited(archive, "schema.json", archive.getinfo("schema.json"),
                                          MAX_MANIFEST, "annual schema")
            schema_document = {"source_schema": verified.data["source_schema"],
                               "graph_schema": verified.data["graph_schema"]}
            if global_schema != _canonical_json(schema_document):
                _bad("annual schema changed after verification")
            child_schema_name = prefix + "schema.json"
            child_schema_info = archive.getinfo(child_schema_name)
            _zip_member_matches(child_schema_info, child_schema_name)
            child_schema = _read_limited(archive, child_schema_name, child_schema_info,
                                         MAX_MANIFEST, "child schema")
            if child_schema != _canonical_json({"source_schema": child["source_schema"],
                                                "graph_schema": child["graph_schema"]}):
                _bad("annual child schema changed after verification")

            for entry_value in child["members"]:
                entry = _validated_member_entry(entry_value, child)
                role = entry["record_role"]
                if role == "schema":
                    continue
                if source_table is not None:
                    if role not in ("observation", "context") or entry.get("source_table") != source_table:
                        continue
                else:
                    if role != "graph" or not entry["path"].startswith(f"graph/{graph_kind}/"):
                        continue
                name = prefix + entry["path"]
                info = archive.getinfo(name)
                _zip_member_matches(info, name)
                member_digest = hashlib.sha256()
                member_size = 0
                with archive.open(name, "r") as member:
                    while block := member.read(_CHECKPOINT_BLOCK):
                        member_digest.update(block)
                        member_size += len(block)
                        _checkpoint(checkpoint)
                if member_size != entry["byte_size"] or member_digest.hexdigest() != entry["sha256"]:
                    _bad("annual selected child member hash changed")

                table = entry["source_table"]
                descriptor = (child["graph_schema"] if role == "graph" else child["source_schema"])[table]
                columns = _codec._columns(descriptor, entry.get("partition_field", False))
                schema = arrow_schema(pa, columns, descriptor, dataset_id=dataset_id,
                                      revision=revision, source_table=table, record_role=role)
                member_info: Mapping[str, Any] = entry
                if include_member_info:
                    described = dict(entry)
                    described["member_prefix"] = prefix
                    described["container_path"] = name
                    if role == "graph":
                        described["graph_kind"] = entry["path"].split("/", 2)[1]
                    member_info = described
                digest = hashlib.sha256()
                count = 0
                decoded_size = 0
                buffered: list[tuple[bytes | None, ...]] = []
                buffered_size = 0
                with archive.open(name, "r") as member:
                    parquet = pq.ParquetFile(member)
                    if (not parquet.schema_arrow.equals(schema, check_metadata=True)
                            or hashlib.sha256(parquet.schema_arrow.serialize().to_pybytes()).hexdigest()
                            != entry["schema_sha256"]
                            or parquet.metadata.num_rows != entry["row_count"]):
                        _bad("annual selected child schema/count changed")
                    for group in range(parquet.num_row_groups):
                        metadata = parquet.metadata.row_group(group)
                        if metadata.num_rows > 65536 or metadata.total_byte_size > 64 * 1024 * 1024:
                            _bad("annual selected child row-group limit exceeded")
                    for batch in parquet.iter_batches(batch_size=MAX_ROWS):
                        for binary in binary_rows_from_batch(batch, columns):
                            _codec._validate_row(binary, columns, descriptor, dataset_id, revision,
                                                  role, entry.get("toronto_date"))
                            encoded = canonical(binary)
                            if len(encoded) > MAX_BATCH_BYTES:
                                _bad("annual selected row exceeds streaming batch limit")
                            if buffered and (len(buffered) >= MAX_ROWS
                                             or buffered_size + len(encoded) > MAX_BATCH_BYTES):
                                if include_member_info:
                                    yield member_info, buffered
                                else:
                                    yield buffered
                                buffered = []
                                buffered_size = 0
                            buffered.append(binary)
                            buffered_size += len(encoded)
                            digest.update(encoded)
                            decoded_size += len(encoded)
                            count += 1
                        _checkpoint(checkpoint)
                    if (count != entry["row_count"] or decoded_size != entry["decoded_bytes"]
                            or digest.hexdigest() != entry["canonical_sha256"]):
                        _bad("annual selected child binary fields or multiplicity changed")
                    if buffered:
                        if include_member_info:
                            yield member_info, buffered
                        else:
                            yield buffered
    except _CheckpointFailure as failure:
        raise failure.original
    except AnnualIntegrityError:
        raise
    except _codec.ArchiveIntegrityError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError,
            zipfile.BadZipFile, RuntimeError, EOFError, OSError) as error:
        raise AnnualIntegrityError("invalid annual component reader input") from error


def iter_annual_binary_rows(path: Path, verified: ArchiveManifest, source_table: str, *,
                            component_dataset_id: UUID,
                            checkpoint: Callable[[], None] | None = None
                            ) -> Iterator[list[tuple[bytes | None, ...]]]:
    """Stream raw observation/context PG-binary tuples for one catalogued quarter.

    ``component_dataset_id`` selects the embedded child and therefore its only
    trusted Q-prefix. The annual parent UUID/revision is never substituted for
    physical or Arrow child identity. Callers must consume the iterator fully
    before accepting results; each selected member's ZIP SHA and binary-field
    multiplicity digest is checked before its final batch is yielded.
    """
    yield from _iter_component_binary_rows(path, verified, component_dataset_id,
                                          source_table=source_table, checkpoint=checkpoint)


def iter_annual_graph_rows(path: Path, verified: ArchiveManifest, graph_kind: str, *,
                           component_dataset_id: UUID,
                           checkpoint: Callable[[], None] | None = None
                           ) -> Iterator[tuple[Mapping[str, Any], list[tuple[bytes | None, ...]]]]:
    """Yield verified child member metadata plus bounded graph-role binary batches.

    Each member descriptor is a fresh mapping copied from the child inventory,
    with its trusted ``member_prefix``, prefixed ``container_path`` and derived
    ``graph_kind`` added. The tuple shape lets the graph reader select source
    columns without looking up arbitrary paths itself.
    """
    yield from _iter_component_binary_rows(path, verified, component_dataset_id,
                                          graph_kind=graph_kind, include_member_info=True,
                                          checkpoint=checkpoint)
