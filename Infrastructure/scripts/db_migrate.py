#!/usr/bin/env python3
"""Apply versioned SQL migrations and the release's Alembic heads."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

VERSION_RE = re.compile(r"^(\d{4})_[a-z][a-z0-9_]*$")
HEADER_RE = re.compile(rb"^-- migration: downtime=(true|false)$")
LEDGER_SQL = """CREATE TABLE IF NOT EXISTS public.schema_migration (
    version TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL,
    downtime BOOLEAN NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
)"""
PASSWORD_FILE = Path("/opt/projectcea/shared/env/postgres.env")


class MigrationError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class Migration:
    version: str
    number: int
    path: Path
    sha256: str
    downtime: bool


@dataclass(frozen=True)
class AppliedMigration:
    version: str
    sha256: str
    downtime: bool


@dataclass(frozen=True)
class Database:
    binary: str
    connection_args: tuple[str, ...]
    environment: dict[str, str]

    def run(self, args: Sequence[str], *, quiet: bool = True) -> str:
        command = [
            self.binary,
            "-X",
            "--no-psqlrc",
            "--set=ON_ERROR_STOP=1",
            *self.connection_args,
        ]
        if quiet:
            command.append("--quiet")
        command.extend(args)
        result = subprocess.run(
            command,
            check=False,
            env=self.environment,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            details = (result.stderr or result.stdout).strip()
            message = f"psql_failed:{result.returncode}"
            if details:
                message = f"{message}\n{details}"
            raise MigrationError(message, 1)
        return result.stdout

    def execute(self, sql: str) -> None:
        self.run(["--command", sql])

    def query(self, sql: str) -> list[str]:
        output = self.run(["--tuples-only", "--no-align", "--field-separator=\t", "--command", sql])
        return [line for line in output.splitlines() if line]

    def execute_file(self, path: Path) -> None:
        self.run(["-f", str(path)], quiet=False)


def _password_from_file() -> str | None:
    try:
        lines = PASSWORD_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("POSTGRES_PASSWORD="):
            value = line.partition("=")[2].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            return value
    return None


def _database_config() -> Database:
    database_name = os.environ.get("POSTGRES_DB")
    if not database_name:
        raise MigrationError("POSTGRES_DB is required", 2)

    host = os.environ.get("POSTGRES_HOST", "127.0.0.1") or "127.0.0.1"
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "cea_user")
    binary = os.environ.get("DB_MIGRATE_PSQL", "psql") or "psql"

    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_HOST": host,
            "POSTGRES_PORT": port,
            "POSTGRES_DB": database_name,
            "POSTGRES_USER": user,
        }
    )
    if "POSTGRES_PASSWORD" in os.environ:
        password = os.environ["POSTGRES_PASSWORD"]
    else:
        password = _password_from_file()
        if password is not None:
            environment["POSTGRES_PASSWORD"] = password
    environment.pop("PGPASSWORD", None)
    if password is not None:
        environment["PGPASSWORD"] = password

    return Database(
        binary=binary,
        connection_args=(
            "--host",
            host,
            "--port",
            port,
            "--username",
            user,
            "--dbname",
            database_name,
        ),
        environment=environment,
    )


def _migration_from_path(path: Path) -> Migration:
    stem = path.name[:-4]
    match = VERSION_RE.fullmatch(stem)
    if match is None:
        raise MigrationError(f"migration_filename_invalid:{path.name}", 4)

    contents = path.read_bytes()
    first_line = contents.split(b"\n", maxsplit=1)[0].removesuffix(b"\r")
    header = HEADER_RE.fullmatch(first_line)
    if header is None:
        raise MigrationError(f"migration_header_invalid:{path.name}", 4)

    number = int(match.group(1))
    if number == 0:
        raise MigrationError(f"migration_filename_invalid:{path.name}", 4)
    return Migration(
        version=stem,
        number=number,
        path=path,
        sha256=hashlib.sha256(contents).hexdigest(),
        downtime=header.group(1) == b"true",
    )


def _load_migrations(release: Path) -> list[Migration]:
    migration_dir = release / "Infrastructure" / "database" / "migrations"
    if not migration_dir.is_dir():
        raise MigrationError(f"migration_directory_missing:{migration_dir}", 1)

    migrations: list[Migration] = []
    numbers: set[int] = set()
    for path in sorted(migration_dir.glob("*.sql")):
        if path.name.endswith(".down.sql"):
            continue
        migration = _migration_from_path(path)
        if migration.number in numbers:
            raise MigrationError(f"migration_version_duplicate:{migration.number:04d}", 4)
        numbers.add(migration.number)
        migrations.append(migration)
    migrations.sort(key=lambda migration: migration.number)
    for migration in migrations:
        if migration.downtime and not migration.path.with_suffix(".down.sql").is_file():
            raise MigrationError(f"migration_down_missing:{migration.version}", 4)
    return migrations


def _ensure_ledger(database: Database) -> None:
    database.execute(LEDGER_SQL)


def _read_ledger(database: Database) -> list[AppliedMigration]:
    rows = database.query(
        "SELECT version, sha256, downtime::text FROM public.schema_migration ORDER BY version"
    )
    applied: list[AppliedMigration] = []
    for row in rows:
        fields = row.split("\t")
        if len(fields) != 3 or fields[2] not in {"true", "false"}:
            raise MigrationError("migration_ledger_invalid", 4)
        applied.append(
            AppliedMigration(version=fields[0], sha256=fields[1], downtime=fields[2] == "true")
        )
    return applied


def _validate_applied(migrations: Sequence[Migration], applied: Sequence[AppliedMigration]) -> None:
    by_version = {migration.version: migration for migration in migrations}
    for row in applied:
        migration = by_version.get(row.version)
        if (
            migration is None
            or migration.sha256 != row.sha256
            or migration.downtime != row.downtime
        ):
            raise MigrationError(f"migration_tampered:{row.version}", 4)


def _head(migrations: Sequence[Migration], applied: Sequence[AppliedMigration]) -> str | None:
    numbers = {migration.version: migration.number for migration in migrations}
    if not applied:
        return None
    return max(applied, key=lambda row: numbers[row.version]).version


def _pending(
    migrations: Sequence[Migration], applied: Sequence[AppliedMigration]
) -> list[Migration]:
    applied_versions = {row.version for row in applied}
    return [migration for migration in migrations if migration.version not in applied_versions]


def _plan(
    migrations: Sequence[Migration], applied: Sequence[AppliedMigration]
) -> dict[str, object]:
    pending = _pending(migrations, applied)
    digest_input = "\n".join(
        f"{migration.version}:{migration.sha256}" for migration in pending
    ).encode("utf-8")
    return {
        "pending": [
            {
                "version": migration.version,
                "sha256": migration.sha256,
                "downtime": migration.downtime,
            }
            for migration in pending
        ],
        "requires_downtime": any(migration.downtime for migration in pending),
        "set_sha256": hashlib.sha256(digest_input).hexdigest(),
        "head": _head(migrations, applied),
    }


def _record_applied(database: Database, migration: Migration) -> None:
    downtime = "true" if migration.downtime else "false"
    database.execute(
        "INSERT INTO public.schema_migration (version, sha256, downtime) "
        f"VALUES ('{migration.version}', '{migration.sha256}', {downtime})"
    )


def _apply_migration(database: Database, migration: Migration) -> None:
    database.execute_file(migration.path)
    _record_applied(database, migration)


def _run_alembic(release: Path, database: Database) -> None:
    service_dir = release / "Infrastructure" / "automation-service"
    default_binary = service_dir / ".venv" / "bin" / "alembic"
    binary = os.environ.get("DB_MIGRATE_ALEMBIC") or str(default_binary)
    config = service_dir / "alembic.ini"
    command = [binary, "-c", str(config), "upgrade", "heads"]
    environment = database.environment.copy()
    python_paths = [
        str(release / "Infrastructure"),
        str(service_dir),
    ]
    existing_pythonpath = environment.get("PYTHONPATH")
    if existing_pythonpath:
        python_paths.append(existing_pythonpath)
    environment["PYTHONPATH"] = os.pathsep.join(python_paths)
    result = subprocess.run(
        command,
        cwd=service_dir,
        check=False,
        env=environment,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        message = f"alembic_failed:{result.returncode}"
        if details:
            message = f"{message}\n{details}"
        raise MigrationError(message, 1)


def _alembic_heads(database: Database) -> list[str]:
    exists = database.query("SELECT to_regclass('public.alembic_version') IS NOT NULL")
    if not exists or exists[0] != "t":
        return []
    return database.query("SELECT version_num FROM public.alembic_version ORDER BY 1")


def _apply(
    release: Path,
    database: Database,
    migrations: Sequence[Migration],
    applied: Sequence[AppliedMigration],
    *,
    allow_downtime: bool,
    skip_alembic: bool,
) -> tuple[list[str], str | None, list[str]]:
    pending = _pending(migrations, applied)
    if not allow_downtime:
        refused = next((item for item in pending if item.downtime), None)
        if refused is not None:
            raise MigrationError(f"migration_requires_downtime:{refused.version}", 3)

    stamp = [migration for migration in pending if "alembic_stamp" in migration.version]
    regular = [migration for migration in pending if migration not in stamp]
    done: list[str] = []
    for migration in stamp:
        _apply_migration(database, migration)
        done.append(migration.version)

    if not skip_alembic:
        _run_alembic(release, database)

    for migration in regular:
        _apply_migration(database, migration)
        done.append(migration.version)

    new_applied = _read_ledger(database)
    current_head = _head(migrations, new_applied)
    return done, current_head, _alembic_heads(database)


def _down(
    database: Database,
    migrations: Sequence[Migration],
    applied: Sequence[AppliedMigration],
    target: str,
) -> list[str]:
    by_version = {migration.version: migration for migration in migrations}
    if target == "none":
        target_number: int | None = None
    else:
        target_migration = by_version.get(target)
        if target_migration is None:
            raise MigrationError(f"migration_target_unknown:{target}", 2)
        target_number = target_migration.number

    numbers = {migration.version: migration.number for migration in migrations}
    selected = [
        by_version[row.version]
        for row in applied
        if target_number is None or numbers[row.version] > target_number
    ]
    selected.sort(key=lambda migration: migration.number, reverse=True)

    down_files: dict[str, Path] = {}
    for migration in selected:
        down_path = migration.path.with_suffix(".down.sql")
        if not down_path.is_file():
            raise MigrationError(f"migration_down_missing:{migration.version}", 4)
        down_files[migration.version] = down_path

    reverted: list[str] = []
    for migration in selected:
        database.execute_file(down_files[migration.version])
        database.execute(
            f"DELETE FROM public.schema_migration WHERE version = '{migration.version}'"
        )
        reverted.append(migration.version)
    return reverted


def _format_status(migrations: Sequence[Migration], applied: Sequence[AppliedMigration]) -> str:
    plan = _plan(migrations, applied)
    applied_by_version = {row.version: row for row in applied}
    lines = ["Applied:"]
    if not applied:
        lines.append("  (none)")
    for migration in migrations:
        row = applied_by_version.get(migration.version)
        if row is not None:
            lines.append(f"  {row.version} {row.sha256} downtime={str(row.downtime).lower()}")
    lines.append("Pending:")
    pending = _pending(migrations, applied)
    if not pending:
        lines.append("  (none)")
    for migration in pending:
        lines.append(
            f"  {migration.version} {migration.sha256} downtime={str(migration.downtime).lower()}"
        )
    lines.append(f"Requires downtime: {str(plan['requires_downtime']).lower()}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", help="release repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("status", "head"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--release", default=argparse.SUPPRESS)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--release", default=argparse.SUPPRESS)
    plan_parser.add_argument("--json", action="store_true")

    apply_parser = subparsers.add_parser("apply")
    apply_parser.add_argument("--release", default=argparse.SUPPRESS)
    apply_parser.add_argument("--allow-downtime", action="store_true")
    apply_parser.add_argument("--skip-alembic", action="store_true")
    apply_parser.add_argument("--json", action="store_true")

    down_parser = subparsers.add_parser("down")
    down_parser.add_argument("--release", default=argparse.SUPPRESS)
    down_parser.add_argument("--to", required=True, metavar="VERSION|none")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    release = (
        Path(args.release).expanduser().resolve()
        if args.release
        else Path(__file__).resolve().parents[2]
    )

    try:
        migrations = _load_migrations(release)
        database = _database_config()
        _ensure_ledger(database)
        applied = _read_ledger(database)
        _validate_applied(migrations, applied)

        if args.command == "head":
            print(_head(migrations, applied) or "null")
            return 0
        if args.command == "status":
            print(_format_status(migrations, applied))
            return 0
        if args.command == "plan":
            plan = _plan(migrations, applied)
            if args.json:
                print(json.dumps(plan, separators=(",", ":")))
            else:
                print(_format_status(migrations, applied))
                print(f"Set SHA256: {plan['set_sha256']}")
            return 0
        if args.command == "apply":
            done, current_head, heads = _apply(
                release,
                database,
                migrations,
                applied,
                allow_downtime=args.allow_downtime,
                skip_alembic=args.skip_alembic,
            )
            if args.json:
                print(
                    json.dumps(
                        {
                            "applied": done,
                            "head": current_head,
                            "alembic_heads": heads,
                        },
                        separators=(",", ":"),
                    )
                )
            else:
                for version in done:
                    print(f"applied {version}")
                print(current_head or "null")
            return 0
        if args.command == "down":
            reverted = _down(database, migrations, applied, args.to)
            for version in reverted:
                print(f"reverted {version}")
            return 0
    except MigrationError as error:
        print(str(error), file=sys.stderr)
        return error.exit_code
    except OSError as error:
        print(f"migration_io_error:{error}", file=sys.stderr)
        return 1

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
