"""Fail-closed, read-only preflight for scheduled source-age Timescale jobs.

Compression is allowed only with the source-resident three-month policy.
Retention jobs and earlier compression policies block destructive lifecycle
activation. This module never modifies a job or pauses unrelated refreshes.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

import asyncpg

from .schema import SourceCatalog, load_source_catalog

SOURCE_AGE_POLICY_PROCS: Final = ("policy_compression", "policy_retention")
_IDENTIFIER: Final = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


@dataclass(frozen=True, slots=True)
class SourceAgePolicyConflict:
    """One scheduled Timescale age policy attached to an archive source."""

    job_id: int
    proc_schema: str
    proc_name: str
    hypertable_schema: str
    hypertable_name: str

    @property
    def source_table(self) -> str:
        return f"{self.hypertable_schema}.{self.hypertable_name}"

    def as_dict(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "proc_schema": self.proc_schema,
            "proc_name": self.proc_name,
            "source_table": self.source_table,
        }


@dataclass(frozen=True, slots=True)
class SourceAgePolicyReport:
    """Read-only source policy inventory suitable for fixed-code status output."""

    source_tables: tuple[str, ...]
    conflicts: tuple[SourceAgePolicyConflict, ...]

    @property
    def ok(self) -> bool:
        return not self.conflicts

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "source_tables": list(self.source_tables),
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "blocked_code": "source_age_policy_conflict" if self.conflicts else None,
        }


class SourceAgePolicyBlocked(RuntimeError):
    """Lifecycle activation is blocked by scheduled source-age policies."""

    code: Final = "source_age_policy_conflict"

    def __init__(self, report: SourceAgePolicyReport) -> None:
        self.report = report
        super().__init__(self.code)


class SourceAgePolicyInspectionFailed(RuntimeError):
    """Policy metadata could not be inspected, so activation must stay held."""

    code: Final = "source_age_policy_preflight_failed"

    def __init__(self) -> None:
        super().__init__(self.code)


def _source_targets(catalog: SourceCatalog) -> tuple[tuple[str, str], ...]:
    """Return catalog-defined source relation names, including unavailable plan streams.

    An unavailable stream still has an explicit plan table name in the checked-in
    catalog.  Looking for a job attached to that exact public name is safe and
    conservative; it does not infer or inspect arbitrary database objects.
    """
    targets: set[tuple[str, str]] = set()
    for stream in catalog.streams:
        relation = stream.relation or f"public.{stream.name}"
        parts = relation.split(".")
        if len(parts) == 1:
            schema_name, table_name = "public", parts[0]
        elif len(parts) == 2:
            schema_name, table_name = parts
        else:
            raise ValueError("invalid source relation descriptor")
        if not _IDENTIFIER.fullmatch(schema_name) or not _IDENTIFIER.fullmatch(table_name):
            raise ValueError("invalid source relation descriptor")
        targets.add((schema_name, table_name))
    if not targets:
        raise ValueError("source catalog has no stream relations")
    return tuple(sorted(targets))


async def inspect_source_age_policies(
    conn: asyncpg.Connection,
    *,
    catalog: SourceCatalog | None = None,
) -> SourceAgePolicyReport:
    """Inventory scheduled compression/retention jobs attached to source streams.

    The function is safe inside or outside an existing transaction and performs
    only SELECT statements.  A metadata/query failure is represented by a fixed
    exception code and must be treated as blocked, never as an empty inventory.
    """
    try:
        active_catalog = catalog if catalog is not None else load_source_catalog()
        targets = _source_targets(active_catalog)
        schemas = [schema_name for schema_name, _ in targets]
        tables = [table_name for _, table_name in targets]
        rows = await conn.fetch(
            """
            SELECT j.job_id, j.proc_schema, j.proc_name,
                   j.hypertable_schema, j.hypertable_name
              FROM timescaledb_information.jobs AS j
             WHERE j.scheduled IS TRUE
               AND j.proc_name = ANY($3::text[])
               AND (
                    j.proc_name = 'policy_retention'
                    OR (
                        j.proc_name = 'policy_compression'
                        AND COALESCE(
                            (j.config->>'compress_after')::interval,
                            interval '0 seconds'
                        ) < interval '3 months'
                    )
               )
               AND EXISTS (
                    SELECT 1
                      FROM unnest($1::text[], $2::text[])
                           AS source(relation_schema, relation_name)
                     WHERE source.relation_schema = j.hypertable_schema
                       AND source.relation_name = j.hypertable_name
               )
             ORDER BY j.job_id
            """,
            schemas,
            tables,
            list(SOURCE_AGE_POLICY_PROCS),
        )
    except SourceAgePolicyInspectionFailed:
        raise
    except Exception as exc:
        raise SourceAgePolicyInspectionFailed() from exc

    conflicts = tuple(
        SourceAgePolicyConflict(
            job_id=int(row["job_id"]),
            proc_schema=str(row["proc_schema"]),
            proc_name=str(row["proc_name"]),
            hypertable_schema=str(row["hypertable_schema"]),
            hypertable_name=str(row["hypertable_name"]),
        )
        for row in rows
    )
    return SourceAgePolicyReport(
        source_tables=tuple(f"{schema_name}.{table_name}" for schema_name, table_name in targets),
        conflicts=conflicts,
    )


async def assert_source_age_policies_clear(
    conn: asyncpg.Connection,
    *,
    catalog: SourceCatalog | None = None,
) -> SourceAgePolicyReport:
    """Return a clear report or raise a fixed-code blocker for lifecycle work."""
    report = await inspect_source_age_policies(conn, catalog=catalog)
    if not report.ok:
        raise SourceAgePolicyBlocked(report)
    return report
