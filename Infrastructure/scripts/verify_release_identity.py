#!/usr/bin/env python3
"""Record and verify allowlisted release-input content without deploying anything."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
from typing import Final, Literal, NoReturn, Protocol, TypeAlias

SENSITIVE_LINE: Final = re.compile(
    r"^.*(?:dsn|password|token|secret|api[_-]?key).*$", re.IGNORECASE | re.MULTILINE
)
MUTATING_CHECK_FLAG: Final = re.compile(r"(?<!\S)--(?:fix|write)(?:\b|=)", re.IGNORECASE)
RECORD_NAME: Final = "release-identity-preflight.json"
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class JsonDecoder(Protocol):
    def decode(self, s: str) -> JsonValue: ...


JSON_DECODER: Final[JsonDecoder] = json.JSONDecoder()


@dataclass(frozen=True, slots=True)
class ReleaseInput:
    path: str
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class PreflightRecord:
    head_sha: str
    inputs: tuple[ReleaseInput, ...]


class IdentityError(RuntimeError):
    pass


class Arguments(argparse.Namespace):
    def __init__(self) -> None:
        super().__init__()
        self.mode: Literal["preflight", "verify"] = "preflight"
        self.repo_root: Path | None = None
        self.allowlist: Path | None = None
        self.evidence_dir: Path | None = None
        self.record: Path | None = None
        self.release_root: Path | None = None
        self.check_cmd: list[str] = []


def redact(value: str) -> str:
    return SENSITIVE_LINE.sub("[REDACTED]", value)


def decode_json(decoder: JsonDecoder, s: str) -> JsonValue:
    return decoder.decode(s)


def emit(message: str) -> None:
    print(redact(message))


def fail(message: str) -> NoReturn:
    raise IdentityError(redact(message))


def command_output(repo_root: Path, args: Sequence[str]) -> str:
    result = subprocess.run(
        ("git", "-C", str(repo_root), *args),
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        fail(result.stderr.strip() or "Git command failed")
    return result.stdout


def repository_root(option: Path | None) -> Path:
    if option is not None:
        root = option.resolve()
    else:
        result = subprocess.run(
            ("git", "rev-parse", "--show-toplevel"), capture_output=True, check=False, text=True
        )
        if result.returncode != 0:
            fail("Current directory is not inside a Git repository")
        root = Path(result.stdout.strip()).resolve()
    if not root.is_dir():
        fail(f"Repository root does not exist: {root}")
    _ = command_output(root, ("rev-parse", "--is-inside-work-tree"))
    return root


def validate_path(repo_root: Path, raw_path: str) -> ReleaseInput:
    candidate = Path(raw_path)
    if not raw_path or candidate.is_absolute() or ".." in candidate.parts:
        fail(f"Allowlist path escapes repository root: {raw_path}")

    component_path = repo_root
    for component in candidate.parts:
        component_path /= component
        if component_path.is_symlink():
            fail(f"Allowlist path has symlink component: {raw_path}")
    if not component_path.is_file():
        fail(f"Release input is not a regular file: {raw_path}")
    return ReleaseInput(path=candidate.as_posix())


def load_allowlist(repo_root: Path, allowlist: Path) -> tuple[ReleaseInput, ...]:
    try:
        lines = allowlist.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        fail(f"Cannot read allowlist: {error}")
    inputs = tuple(validate_path(repo_root, line.strip()) for line in lines if line.strip())
    if not inputs:
        fail("Allowlist contains no release inputs")
    paths = tuple(item.path for item in inputs)
    if len(paths) != len(set(paths)):
        fail("Allowlist contains duplicate release inputs")
    return inputs


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_clean_committed(repo_root: Path, inputs: Sequence[ReleaseInput]) -> str:
    drifted: list[str] = []
    for release_input in inputs:
        tracked = subprocess.run(
            ("git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", release_input.path),
            capture_output=True,
            check=False,
            text=True,
        )
        status = command_output(
            repo_root,
            ("status", "--porcelain=v1", "--untracked-files=all", "--", release_input.path),
        )
        if tracked.returncode != 0 or status:
            drifted.append(release_input.path)
    if drifted:
        fail("Dirty or untracked release inputs: " + ", ".join(drifted))
    return command_output(
        repo_root,
        (
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            "--",
            *(item.path for item in inputs),
        ),
    )


def run_checks(repo_root: Path, commands: Sequence[str]) -> None:
    for command in commands:
        try:
            argv = tuple(shlex.split(command))
        except ValueError as error:
            fail(f"Invalid check command: {error}")
        if not argv:
            fail("Configured check command is empty")
        result = subprocess.run(argv, cwd=repo_root, capture_output=True, check=False, text=True)
        output = redact(result.stdout + result.stderr).strip()
        if output:
            emit(output)
        if result.returncode != 0:
            fail(f"Configured check command failed with exit={result.returncode}")


def write_record(evidence_dir: Path, record: PreflightRecord, porcelain: str) -> Path:
    payload = {
        "version": 1,
        "head_sha": record.head_sha,
        "release_inputs": [
            {"path": release_input.path, "sha256": release_input.sha256}
            for release_input in record.inputs
        ],
        "porcelain": porcelain,
    }
    record_path = evidence_dir / RECORD_NAME
    _ = record_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return record_path


def read_record(path: Path) -> PreflightRecord:
    try:
        raw: JsonValue = decode_json(JSON_DECODER, path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        fail(f"Cannot read preflight record: {error}")
    match raw:
        case {"head_sha": str() as head_sha, "release_inputs": list() as raw_inputs}:
            inputs: list[ReleaseInput] = []
            for item in raw_inputs:
                match item:
                    case {"path": str() as item_path, "sha256": str() as sha256}:
                        inputs.append(ReleaseInput(path=item_path, sha256=sha256))
                    case _:
                        fail("Preflight record has an invalid release input")
            if not inputs:
                fail("Preflight record contains no release inputs")
            return PreflightRecord(head_sha=head_sha, inputs=tuple(inputs))
        case _:
            fail("Preflight record has an invalid shape")


def preflight(
    repo_root: Path, allowlist: Path, evidence_dir: Path, commands: Sequence[str]
) -> None:
    inputs = load_allowlist(repo_root, allowlist)
    for command in commands:
        if MUTATING_CHECK_FLAG.search(command):
            fail("Configured check command contains a forbidden mutating flag")
    porcelain = assert_clean_committed(repo_root, inputs)
    run_checks(repo_root, commands)
    hashed_inputs = tuple(
        ReleaseInput(path=item.path, sha256=file_sha256(repo_root / item.path)) for item in inputs
    )
    record = PreflightRecord(
        head_sha=command_output(repo_root, ("rev-parse", "HEAD")).strip(), inputs=hashed_inputs
    )
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        fail("Evidence directory must be a pre-existing non-symlink directory")
    record_path = write_record(evidence_dir.resolve(), record, porcelain)
    emit(f"Preflight passed: {len(record.inputs)} release inputs; record={record_path}")


def release_file(release_root: Path, release_input: ReleaseInput) -> Path:
    if release_root.is_symlink() or not release_root.is_dir():
        fail("Release root must be a non-symlink directory")
    deployed = release_root
    for component in Path(release_input.path).parts:
        deployed /= component
        if deployed.is_symlink():
            fail(f"Release input has symlink component: {release_input.path}")
    if not deployed.is_file():
        fail(f"Drifted path: {release_input.path} (missing from release root)")
    return deployed


def verify(repo_root: Path, record_path: Path, release_root: Path | None) -> None:
    record = read_record(record_path)
    for expected in record.inputs:
        source = validate_path(repo_root, expected.path)
        if expected.sha256 is None or file_sha256(repo_root / source.path) != expected.sha256:
            fail(f"Drifted path: {expected.path} (repository hash mismatch)")
        if (
            release_root is not None
            and file_sha256(release_file(release_root, expected)) != expected.sha256
        ):
            fail(f"Drifted path: {expected.path} (release hash mismatch)")
    emit(
        f"Verify passed: {len(record.inputs)} release inputs match preflight HEAD {record.head_sha}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, exit_on_error=False)
    _ = parser.add_argument("mode", choices=("preflight", "verify"))
    _ = parser.add_argument("--repo-root", type=Path)
    _ = parser.add_argument("--allowlist", type=Path)
    _ = parser.add_argument("--evidence-dir", type=Path)
    _ = parser.add_argument("--record", type=Path)
    _ = parser.add_argument("--release-root", type=Path)
    _ = parser.add_argument("--check-cmd", action="append", default=[])
    try:
        args = parser.parse_args(namespace=Arguments())
        root = repository_root(args.repo_root)
        if args.mode == "preflight":
            if args.allowlist is None or args.evidence_dir is None:
                fail("preflight requires --allowlist and --evidence-dir")
            preflight(root, args.allowlist, args.evidence_dir, args.check_cmd)
        else:
            if args.record is None:
                fail("verify requires --record")
            verify(root, args.record, args.release_root)
    except (argparse.ArgumentError, IdentityError) as error:
        print(f"ERROR: {redact(str(error))}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
