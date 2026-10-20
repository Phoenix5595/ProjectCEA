"""Annual Veg containers: four verified quarterly payloads combined unchanged.

The annual ZIP is a container, not a re-encode: every member listed in every
component manifest inventory is copied byte-identically under
``Q<quarter>/`` and one annual manifest embeds each full child manifest with
its identity, revision, counts and hashes, so parent and child schema/phase
metadata stay usable directly by ML tooling. Corrections reuse the previous
annual container's prefixes for the unchanged quarters and combine them with
the rebuilt component; no quarterly ZIP is required to exist any more.

Canonical encoding (``canonical_json_bytes``) is the exact JSON encoding the
codec writes for ``manifest.json``/``schema.json`` (sorted keys, compact
separators, UTF-8, no NaN), so ``manifest_sha256`` recomputes byte-exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

FORMAT = "sj-cea-history/1"
MEMBER_LIMIT = 8 * 1024 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024 * 1024
MANIFEST_LIMIT = 16 * 1024 * 1024
BLOCK = 1024 * 1024
PREFIX_PATTERN = re.compile(r"^Q[1-4]/$")


class AnnualIntegrityError(ValueError):
    """The annual container or one of its components is inconsistent."""


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical manifest encoding shared with the codec (sorted, compact, UTF-8)."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


_EVENT_TIME_TOKEN = re.compile(
    r"(?<![a-z0-9])(?:timestamp|observed_at|triggered_at)(?![a-z0-9])",
)


def _normalize_event_time_names(
    value: Any,
    field: str | None = None,
    table_name: str | None = None,
    parent_field: str | None = None,
) -> Any:
    if isinstance(value, Mapping):
        if table_name is None and isinstance(value.get("name"), str):
            table_name = value["name"]
        return {
            key: _normalize_event_time_names(
                item, field=str(key), table_name=table_name, parent_field=field
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _normalize_event_time_names(
                item, field=field, table_name=table_name, parent_field=field
            )
            for item in value
        ]
    if isinstance(value, str):
        if field in {"type", "source_files"}:
            return value
        if table_name == "automation_state" and parent_field == "indexes" and field == "name":
            return value
        return _EVENT_TIME_TOKEN.sub("time", value)
    return value


def _refresh_descriptor_fingerprint(descriptor: dict[str, Any]) -> None:
    columns = descriptor.get("columns")
    primary_key = descriptor.get("primary_key")
    variants = descriptor.get("primary_key_variants") or []
    unique_constraints = descriptor.get("unique_constraints")
    if (
        not isinstance(columns, list)
        or not all(isinstance(column, Mapping) for column in columns)
        or not isinstance(primary_key, list)
        or not isinstance(variants, list)
        or not isinstance(unique_constraints, list)
        or type(descriptor.get("hypertable")) is not bool
    ):
        return
    payload = {
        "relation": descriptor.get("relation"),
        "columns": [
            [
                column.get("name"),
                column.get("type"),
                column.get("not_null"),
                column.get("generation"),
            ]
            for column in columns
        ],
        "primary_key": primary_key,
        "primary_key_variants": variants,
        "unique_constraints": unique_constraints,
        "hypertable": descriptor["hypertable"],
    }
    descriptor["fingerprint"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def schema_descriptors_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Accept only byte-equivalent descriptors apart from legacy event-time names."""
    if canonical_json_bytes(left) == canonical_json_bytes(right):
        return True
    normalized_left = _normalize_event_time_names(left)
    normalized_right = _normalize_event_time_names(right)
    if isinstance(normalized_left, dict):
        _refresh_descriptor_fingerprint(normalized_left)
    if isinstance(normalized_right, dict):
        _refresh_descriptor_fingerprint(normalized_right)
    return canonical_json_bytes(normalized_left) == canonical_json_bytes(normalized_right)


def manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


@dataclass(frozen=True, slots=True)
class AnnualComponentInput:
    dataset_id: UUID
    revision: int
    member_prefix: str
    source: Path
    source_prefix: str
    sha256: str
    byte_size: int
    manifest: Mapping[str, Any]


def component_members(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    members = manifest.get("members")
    if not isinstance(members, list) or not members:
        raise AnnualIntegrityError("component member inventory missing")
    return members


def _safe_member(name: Any) -> bool:
    return (
        isinstance(name, str)
        and bool(name)
        and "\\" not in name
        and "\x00" not in name
        and not name.startswith("/")
        and not re.match(r"^[A-Za-z]:", name)
        and not any(part in ("", ".", "..") for part in name.split("/"))
    )


def annual_member_inventory(
    manifest: Mapping[str, Any],
) -> tuple[tuple[Mapping[str, Any], Mapping[str, Any], str], ...]:
    """Ordered ``(component, member_entry, zip_member_name)`` triples.

    ``zip_member_name`` is the prefixed name inside the annual ZIP; the same
    triple drives verification (codec) and any component reader.
    """
    components = manifest.get("components")
    if not isinstance(components, list) or len(components) != 4:
        raise AnnualIntegrityError("annual must contain four quarter components")
    inventory: list[tuple[Mapping[str, Any], Mapping[str, Any], str]] = []
    seen: set[str] = set()
    seen_prefixes: set[str] = set()
    for component in components:
        if not isinstance(component, Mapping):
            raise AnnualIntegrityError("invalid annual component")
        prefix = component.get("member_prefix")
        if (
            not isinstance(prefix, str)
            or not PREFIX_PATTERN.match(prefix)
            or prefix in seen_prefixes
        ):
            raise AnnualIntegrityError("invalid component prefix")
        seen_prefixes.add(prefix)
        child = component.get("manifest")
        if not isinstance(child, Mapping):
            raise AnnualIntegrityError("component manifest missing")
        if (
            child.get("format") != FORMAT
            or str(child.get("dataset_id")) != str(component.get("dataset_id"))
            or child.get("revision") != component.get("revision")
        ):
            raise AnnualIntegrityError("component identity mismatch")
        for entry in component_members(child):
            name = entry.get("path")
            if not _safe_member(name):
                raise AnnualIntegrityError("unsafe component member path")
            target = prefix + str(name)
            if target in seen:
                raise AnnualIntegrityError("duplicate annual member")
            seen.add(target)
            inventory.append((component, entry, target))
    if seen_prefixes != {"Q1/", "Q2/", "Q3/", "Q4/"}:
        raise AnnualIntegrityError("annual quarter set is incomplete")
    return tuple(inventory)


def _merge_schema(target: dict[str, Any], schema: Mapping[str, Any], *, label: str) -> None:
    for table, descriptor in schema.items():
        existing = target.get(table)
        if existing is not None and not schema_descriptors_compatible(existing, descriptor):
            raise AnnualIntegrityError(f"component {label} schema mismatch")
        target[table] = descriptor


def build_annual_manifest(
    dataset_id: UUID,
    revision: int,
    *,
    calendar_revision: int,
    calendar_year: int,
    room: str,
    components: Sequence[AnnualComponentInput],
) -> dict[str, Any]:
    """Pure annual manifest; totals are sums over the embedded child manifests."""
    if revision < 1 or calendar_revision < 1 or len(components) != 4:
        raise AnnualIntegrityError("annual must contain four quarters")
    ordered = sorted(components, key=lambda component: component.member_prefix)
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    observations: dict[str, int] = {}
    contexts: dict[str, int] = {}
    per_day: dict[str, dict[str, int]] = {}
    source_schema: dict[str, Any] = {}
    graph_schema: dict[str, Any] = {}
    absent: set[str] = set()
    gaps: list[Any] = []
    coverage = "complete"
    available_from: str | None = None
    available_to: str | None = None
    decoded_bytes = 0
    for component in ordered:
        if (
            not PREFIX_PATTERN.match(component.member_prefix)
            or component.member_prefix in seen
            or len(component.sha256) != 64
            or any(char not in "0123456789abcdef" for char in component.sha256.lower())
            or component.byte_size <= 0
        ):
            raise AnnualIntegrityError("invalid component identity/prefix")
        seen.add(component.member_prefix)
        if component.source_prefix and not PREFIX_PATTERN.match(component.source_prefix):
            raise AnnualIntegrityError("invalid source component prefix")
        if Path(component.source).is_symlink():
            raise AnnualIntegrityError("component source is not owned")
        manifest = component.manifest
        if (
            manifest.get("format") != FORMAT
            or str(manifest.get("dataset_id")) != str(component.dataset_id)
            or manifest.get("revision") != component.revision
            or manifest.get("timezone") != "America/Toronto"
        ):
            raise AnnualIntegrityError("component identity mismatch")
        component_members(manifest)
        for table, count in (manifest.get("observation_counts") or {}).items():
            observations[table] = observations.get(table, 0) + int(count)
        for table, count in (manifest.get("context_counts") or {}).items():
            contexts[table] = contexts.get(table, 0) + int(count)
        for table, days in (manifest.get("per_day_counts") or {}).items():
            merged = per_day.setdefault(table, {})
            for day, count in days.items():
                merged[day] = merged.get(day, 0) + int(count)
        _merge_schema(source_schema, manifest.get("source_schema") or {}, label="source")
        _merge_schema(graph_schema, manifest.get("graph_schema") or {}, label="graph")
        absent.update(str(name) for name in (manifest.get("absent_streams") or []))
        child_coverage = manifest.get("coverage", "unknown")
        if child_coverage == "gapped":
            coverage = "gapped"
        elif child_coverage != "complete" and coverage == "complete":
            coverage = "unknown"
        gaps.extend(manifest.get("gaps") or [])
        child_from = manifest.get("available_from")
        child_to = manifest.get("available_to")
        if child_from is not None and (available_from is None or child_from < available_from):
            available_from = child_from
        if child_to is not None and (available_to is None or child_to > available_to):
            available_to = child_to
        decoded_bytes += int(manifest.get("decoded_bytes") or 0)
        entries.append(
            {
                "dataset_id": str(component.dataset_id),
                "revision": component.revision,
                "member_prefix": component.member_prefix,
                "sha256": component.sha256,
                "byte_size": component.byte_size,
                "manifest_sha256": manifest_sha256(manifest),
                "manifest": manifest,
            }
        )
    schema_document = {"source_schema": source_schema, "graph_schema": graph_schema}
    decoded_bytes += len(canonical_json_bytes(schema_document))
    if decoded_bytes > TOTAL_LIMIT:
        raise AnnualIntegrityError("annual decoded budget exceeded")
    return {
        "format": FORMAT,
        "kind": "veg_year",
        "dataset_id": str(dataset_id),
        "revision": revision,
        "calendar_revision": calendar_revision,
        "calendar_year": calendar_year,
        "room": room,
        "timezone": "America/Toronto",
        "available_from": available_from,
        "available_to": available_to,
        "original_anniversary_at": None,
        "coverage": coverage,
        "gaps": gaps,
        "absent_streams": sorted(absent),
        "observation_counts": observations,
        "context_counts": contexts,
        "per_day_counts": per_day,
        "decoded_bytes": decoded_bytes,
        "source_schema": source_schema,
        "graph_schema": graph_schema,
        "components": entries,
    }


def build_annual_zip(
    dataset_id: UUID,
    revision: int,
    *,
    calendar_revision: int,
    calendar_year: int,
    room: str,
    components: Sequence[AnnualComponentInput],
    destination: Path,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Stream component members unchanged under their prefixes; manifest last."""
    destination = Path(destination)
    manifest = build_annual_manifest(
        dataset_id,
        revision,
        calendar_revision=calendar_revision,
        calendar_year=calendar_year,
        room=room,
        components=components,
    )
    schema_document = {
        "source_schema": manifest["source_schema"],
        "graph_schema": manifest["graph_schema"],
    }
    schema_bytes = canonical_json_bytes(schema_document)
    manifest_bytes = canonical_json_bytes(manifest)
    if len(schema_bytes) > MANIFEST_LIMIT or len(manifest_bytes) > MANIFEST_LIMIT:
        raise AnnualIntegrityError("annual manifest/schema size limit exceeded")
    if len(schema_bytes) + len(manifest_bytes) > TOTAL_LIMIT:
        raise AnnualIntegrityError("annual archive size limit exceeded")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    total_member_bytes = 0
    written: set[str] = set()
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True
        ) as archive:
            for component in sorted(components, key=lambda item: item.member_prefix):
                with zipfile.ZipFile(component.source, "r") as source_archive:
                    available = set(source_archive.namelist())
                    for entry in component_members(component.manifest):
                        name = entry.get("path")
                        if not _safe_member(name):
                            raise AnnualIntegrityError("unsafe component member path")
                        source_name = component.source_prefix + str(name)
                        target_name = component.member_prefix + str(name)
                        if source_name not in available:
                            raise AnnualIntegrityError("component member missing")
                        if target_name in written:
                            raise AnnualIntegrityError("duplicate annual member")
                        written.add(target_name)
                        digest = hashlib.sha256()
                        size = 0
                        with (
                            source_archive.open(source_name) as source,
                            archive.open(target_name, "w", force_zip64=True) as output,
                        ):
                            while block := source.read(BLOCK):
                                if (
                                    size + len(block) > MEMBER_LIMIT
                                    or total_member_bytes + len(block) > TOTAL_LIMIT
                                ):
                                    raise AnnualIntegrityError(
                                        "annual member/archive exceeds budget"
                                    )
                                output.write(block)
                                digest.update(block)
                                size += len(block)
                                total_member_bytes += len(block)
                                if checkpoint is not None:
                                    checkpoint()
                        if size != entry.get("byte_size") or digest.hexdigest() != entry.get(
                            "sha256"
                        ):
                            raise AnnualIntegrityError("component member bytes changed")
            archive.writestr("schema.json", schema_bytes)
            archive.writestr("manifest.json", manifest_bytes)
        if temporary.stat().st_size > TOTAL_LIMIT:
            raise AnnualIntegrityError("annual ZIP exceeds archive size limit")
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return manifest
