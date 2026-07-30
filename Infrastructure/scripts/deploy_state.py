#!/usr/bin/env python3
"""Atomic deploy-state CRUD + reconciliation helper for deploy/rollback/finalize.

State schema:
  last_good_release_id: str | None
  last_good_release_path: str | None
  rollback_to_path: str | None
  candidate_release_id: str | None
  candidate_release_path: str | None
  candidate_started_at: str | None
  updated_at: str
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
import pathlib
import sys
from typing import cast


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_state(path: pathlib.Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except FileNotFoundError:
        return {}
    if not text.strip():
        return {}
    decoded = cast(object, json.loads(text))
    if not isinstance(decoded, dict):
        return {}
    typed = cast(dict[object, object], decoded)
    result: dict[str, object] = {}
    for key, value in typed.items():
        if isinstance(key, str):
            result[key] = value
    return result


def _write_state(path: pathlib.Path, state: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    _ = tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, path)


def _resolve(path_str: str) -> str:
    return str(pathlib.Path(path_str).resolve())


def _ensure_defaults(state: dict[str, object]) -> None:
    _ = state.setdefault("last_good_release_id", None)
    _ = state.setdefault("last_good_release_path", None)
    _ = state.setdefault("rollback_to_path", None)


def _json_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def cmd_read(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    if len(argv) > 1:
        print(_json_value(state.get(argv[1])))
    else:
        print(json.dumps(state))
    return 0


def cmd_reconcile(argv: list[str]) -> int:
    """Reconcile a stale state file to the active symlink when no candidate exists."""
    path = pathlib.Path(argv[0])
    current = _resolve(argv[1])
    state = _read_state(path)
    if not state:
        print("no_state")
        return 0
    if state.get("candidate_release_id"):
        print("candidate_exists")
        return 0
    last_good_path = state.get("last_good_release_path")
    if isinstance(last_good_path, str) and _resolve(last_good_path) == current:
        print("ok")
        return 0
    _ensure_defaults(state)
    state["last_good_release_id"] = os.path.basename(current)
    state["last_good_release_path"] = current
    state["rollback_to_path"] = None
    state["updated_at"] = _utc_now()
    _write_state(path, state)
    print("reconciled")
    return 0


def cmd_require_no_candidate(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    if state.get("candidate_release_id"):
        print("candidate_exists")
        return 1
    print("no_candidate")
    return 0


def cmd_set_candidate(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    candidate_id, candidate_path = argv[1], argv[2]
    state = _read_state(path)
    _ensure_defaults(state)
    state["candidate_release_id"] = candidate_id
    state["candidate_release_path"] = _resolve(candidate_path)
    state["candidate_started_at"] = _utc_now()
    state["updated_at"] = _utc_now()
    _write_state(path, state)
    print("ok")
    return 0


def cmd_clear_candidate(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    for key in (
        "candidate_release_id",
        "candidate_release_path",
        "candidate_started_at",
    ):
        _ = state.pop(key, None)
    state["updated_at"] = _utc_now()
    _write_state(path, state)
    print("ok")
    return 0


def cmd_promote(argv: list[str]) -> int:
    """Promote candidate to last-good; old last-good becomes rollback target."""
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    candidate_id = state.get("candidate_release_id")
    candidate_path = state.get("candidate_release_path")
    if not candidate_id or not candidate_path:
        print("no_candidate")
        return 1
    old_last_good_id = state.get("last_good_release_id")
    old_last_good_path = state.get("last_good_release_path")
    _ensure_defaults(state)
    state["last_good_release_id"] = candidate_id
    state["last_good_release_path"] = _resolve(str(candidate_path))
    state["rollback_to_path"] = _resolve(str(old_last_good_path)) if old_last_good_path else None
    for key in (
        "candidate_release_id",
        "candidate_release_path",
        "candidate_started_at",
    ):
        _ = state.pop(key, None)
    state["updated_at"] = _utc_now()
    _write_state(path, state)
    print(
        json.dumps(
            {
                "former_last_good_id": old_last_good_id,
                "former_last_good_path": old_last_good_path,
            }
        )
    )
    return 0


def cmd_get_last_good_path(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    print(state.get("last_good_release_path") or "")
    return 0


def cmd_get_rollback_path(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    print(state.get("rollback_to_path") or "")
    return 0


def cmd_has_candidate(argv: list[str]) -> int:
    path = pathlib.Path(argv[0])
    state = _read_state(path)
    print("yes" if state.get("candidate_release_id") else "no")
    return 0


def cmd_cleanup(argv: list[str]) -> int:
    """Print release directories that are safe to delete, newest first."""
    releases_dir = pathlib.Path(argv[0])
    state_path = pathlib.Path(argv[1])
    max_releases = int(argv[2])
    state = _read_state(state_path)
    protected = {
        state.get("last_good_release_path"),
        state.get("candidate_release_path"),
        state.get("rollback_to_path"),
    }
    protected = {_resolve(str(p)) for p in protected if isinstance(p, str)}
    if not releases_dir.exists():
        return 0
    entries = sorted(
        (p for p in releases_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    kept = 0
    for entry in entries:
        if kept < max_releases:
            kept += 1
            continue
        resolved = str(entry.resolve())
        if resolved in protected:
            continue
        print(resolved)
    return 0


COMMANDS = {
    "read": cmd_read,
    "reconcile": cmd_reconcile,
    "require-no-candidate": cmd_require_no_candidate,
    "set-candidate": cmd_set_candidate,
    "clear-candidate": cmd_clear_candidate,
    "promote": cmd_promote,
    "get-last-good-path": cmd_get_last_good_path,
    "get-rollback-path": cmd_get_rollback_path,
    "has-candidate": cmd_has_candidate,
    "cleanup": cmd_cleanup,
}


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]
    if not args:
        print(
            "Usage: deploy_state.py <command> [args]\ncommands: " + ", ".join(COMMANDS),
            file=sys.stderr,
        )
        return 2
    command = args[0]
    handler = COMMANDS.get(command)
    if handler is None:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    return handler(args[1:])


if __name__ == "__main__":
    raise SystemExit(main())
