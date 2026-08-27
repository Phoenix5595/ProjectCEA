#!/usr/bin/env python3
"""Guarded duplicate-key purge tool for ProjectCEA Redis migration leftovers.

Safety contract (project AGENTS.md):
- Read-only discovery + comparison by default. Physical deletion requires an
  explicit owner-authorized operation and BOTH ``--confirm`` and
  ``--owner-approval`` flags.
- Refuses unknown key patterns: every scanned key must be classified as
  canonical (``cea:*``), an active exclusion, or a migrated legacy family.
- Compares legacy/canonical values before declaring a key eligible; exits
  nonzero on any mismatch, missing twin, or blocked pattern.
- Never deletes non-legacy keys; never runs automatically (no daemon/cron).

Eligibility basis: Tasks 18 (ledger), 21-23 (canonicalization + compat removal).
Excluded control-critical namespaces: ``failsafe:*`` (KEEP per T18), the
``sensor:raw`` and ``stream:control`` streams, ``schedules:*`` caches,
``pid:autotune:*`` (still active legacy-shape), and ``automation:degraded``.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import fnmatch
import hashlib
import sys
from typing import Any

CONFIRM_TOKEN = "PURGE-LEGACY-DUPLICATE-KEYS"

EXIT_OK = 0
EXIT_MISMATCH = 2
EXIT_UNKNOWN_KEY = 3
EXIT_BLOCKED = 4
EXIT_MISUSE = 5


@dataclass(frozen=True)
class Family:
    name: str
    glob: str
    parse: Callable[[str], tuple[str, ...] | None]
    canonical: Callable[[tuple[str, ...]], str]


def _parts(key: str, n: int) -> tuple[str, ...] | None:
    parts = tuple(key.split(":"))
    return parts if len(parts) == n else None


def _canon_simple(prefix: str) -> Callable[[tuple[str, ...]], str]:
    # Parsed parts retain the legacy family segment at p[0]; drop it.
    return lambda p: ":".join((prefix, *p[1:]))


def _canon_sensor_last_good(p: tuple[str, ...]) -> str:
    _, cluster, name, _suffix = p
    return f"cea:sensor:global:{cluster}:{name}_last_good"


def _canon_pid_parameters(p: tuple[str, ...]) -> str:
    if p[-1] == "all":
        return "cea:pid:global:default:all"
    if len(p) == 3:  # pid:parameters:{device_type}
        return f"cea:pid:global:default:{p[2]}"
    location, cluster, device_type = p[2], p[3], p[4]
    return f"cea:pid:{location}:{cluster}:{device_type}"


FAMILIES: tuple[Family, ...] = (
    Family(
        "sensor_last_good", "sensor:*:*:last_good", lambda k: _parts(k, 4), _canon_sensor_last_good
    ),
    Family(
        "setpoint_rate_limit",
        "setpoint:*",
        lambda k: _parts(k, 5) if k.endswith(":last_write") else None,
        _canon_simple("cea:setpoint"),
    ),
    Family(
        "setpoint_field",
        "setpoint:*",
        lambda k: _parts(k, 4) if not k.endswith(":last_write") else None,
        _canon_simple("cea:setpoint"),
    ),
    Family(
        "effective_setpoint_light",
        "effective_setpoint:*",
        lambda k: _parts(k, 6) if ":light:" in k else None,
        _canon_simple("cea:effective_setpoint"),
    ),
    Family(
        "effective_setpoint_climate",
        "effective_setpoint:*",
        lambda k: _parts(k, 4) if ":light:" not in k else None,
        _canon_simple("cea:effective_setpoint"),
    ),
    Family("light_state", "light:*", lambda k: _parts(k, 4), _canon_simple("cea:light")),
    Family(
        "automation_state_ts",
        "automation:*:ts",
        lambda k: _parts(k[:-3], 4) if k.endswith(":ts") else None,
        lambda p: f"cea:automation:{':'.join(p[1:])}:ts",
    ),
    Family(
        "automation_state",
        "automation:*",
        lambda k: None if k == "automation:degraded" else _parts(k, 4),
        _canon_simple("cea:automation"),
    ),
    Family(
        "ramp_persist", "ramp_persist:*", lambda k: _parts(k, 4), _canon_simple("cea:ramp_persist")
    ),
    Family(
        "ramp_state",
        "ramp:*",
        lambda k: None if k.startswith("ramp_persist:") else _parts(k, 4),
        _canon_simple("cea:ramp"),
    ),
    Family("mode", "mode:*", lambda k: _parts(k, 3), _canon_simple("cea:mode")),
    Family("alarm", "alarm:*", lambda k: _parts(k, 4), _canon_simple("cea:alarm")),
    Family(
        "heartbeat",
        "heartbeat:*",
        lambda k: _parts(k, 2),
        lambda p: f"cea:heartbeat:global:default:{p[1]}",
    ),
    Family(
        "pid_parameters",
        "pid:parameters:*",
        lambda k: None
        if len(tuple(k.split(":"))) not in (3, 5) or k.endswith(":ts")
        else tuple(k.split(":")),
        _canon_pid_parameters,
    ),
)

ACTIVE_EXCLUSIONS: tuple[str, ...] = (
    "failsafe:*",  # T18 KEEP: safety-critical, intentionally legacy
    "pid:autotune:*",  # still-active legacy-shape namespace (documented T23)
    "automation:degraded",
    "sensor:raw",  # active telemetry stream (non-cea canonical name)
    "stream:control",  # T18 KEEP: active control buffer
    "schedules:*",  # active schedule caches (non-cea builders)
)


@dataclass
class Candidate:
    family: str
    legacy_key: str
    canonical_key: str
    status: str
    detail: str = ""


def classify(client: Any) -> tuple[list[Candidate], list[str], int, int]:
    """Scan read-only; returns (candidates, exclusions_found, canonical_count, unknown_count)."""
    known_globs = [f.glob for f in FAMILIES] + list(ACTIVE_EXCLUSIONS)
    candidates: list[Candidate] = []
    exclusions: list[str] = []
    unknown = 0
    canonical_count = 0
    seen: set[str] = set()
    for key in sorted(_iter_all(client)):
        if key in seen:
            continue
        seen.add(key)
        if key.startswith("cea:"):
            canonical_count += 1
            continue
        if any(fnmatch.fnmatchcase(key, g) for g in ACTIVE_EXCLUSIONS):
            exclusions.append(key)
            continue
        matched = False
        for fam in FAMILIES:
            if not fnmatch.fnmatchcase(key, fam.glob):
                continue
            parsed = fam.parse(key)
            if parsed is None:
                continue
            matched = True
            candidates.append(Candidate(fam.name, key, fam.canonical(parsed), "PENDING"))
            break
        if not matched:
            print(f"UNKNOWN PATTERN (refusing): {key}")
            unknown += 1
    del known_globs
    return candidates, exclusions, canonical_count, unknown


def _iter_all(client: Any):
    scan = getattr(client, "scan_iter", None)
    if scan is not None:
        yield from scan(match="*")
        return
    raise RuntimeError("redis client does not support scan_iter")


def _decode(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


def _fingerprint(value: str | None) -> str:
    if value is None:
        return "<absent>"
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"len={len(value)} sha256:{digest}"


def compare(client: Any, candidates: list[Candidate]) -> None:
    for cand in candidates:
        legacy_raw = _decode(client.get(cand.legacy_key))
        canon_raw = _decode(client.get(cand.canonical_key))
        if canon_raw is None:
            cand.status = "BLOCKED_NO_TWIN"
            cand.detail = "canonical counterpart absent; purging would lose data"
        elif legacy_raw != canon_raw:
            cand.status = "MISMATCH"
            cand.detail = (
                f"legacy {_fingerprint(legacy_raw)} != canonical {_fingerprint(canon_raw)}"
            )
        else:
            cand.status = "ELIGIBLE"


def run_discovery(client: Any) -> int:
    candidates, exclusions, canonical_count, unknown = classify(client)
    compare(client, candidates)
    print(
        f"scan summary: {canonical_count} canonical keys untouched, "
        f"{len(exclusions)} active-exclusion keys skipped, {unknown} unknown"
    )
    for key in exclusions:
        print(f"EXCLUDED (active/control-critical): {key}")
    exit_code = EXIT_OK
    eligible = [c for c in candidates if c.status == "ELIGIBLE"]
    for cand in candidates:
        line = f"[{cand.status}] {cand.family}: {cand.legacy_key} -> {cand.canonical_key}"
        if cand.detail:
            line += f" ({cand.detail})"
        print(line)
        if cand.status == "MISMATCH":
            exit_code = EXIT_MISMATCH
        elif cand.status == "BLOCKED_NO_TWIN":
            exit_code = EXIT_BLOCKED
    if unknown:
        exit_code = EXIT_UNKNOWN_KEY
    print(f"PURGE PLAN: {len(eligible)} legacy key(s) eligible")
    for cand in eligible:
        print(f"  DEL {cand.legacy_key}")
    if exit_code == EXIT_OK:
        print("dry-run clean: parity verified for every candidate")
    return exit_code


def run_delete(client: Any, owner_approval: str) -> int:
    print(f"DELETION MODE authorized by owner approval: {owner_approval}")
    candidates, exclusions, _, unknown = classify(client)
    compare(client, candidates)
    blocked = [c for c in candidates if c.status != "ELIGIBLE"]
    if unknown or blocked:
        print("refusing deletion: discovery pass is not clean")
        return run_discovery(client)
    deleted = 0
    for cand in candidates:
        if cand.legacy_key.startswith("cea:") or cand.canonical_key == cand.legacy_key:
            print(f"SAFETY ABORT: {cand.legacy_key} classified incorrectly")
            return EXIT_MISUSE
        client.delete(cand.legacy_key)
        print(f"DEL {cand.legacy_key}")
        deleted += 1
    print(
        f"deleted {deleted} legacy key(s); canonical keys untouched; "
        f"{len(exclusions)} exclusion(s) preserved"
    )
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "Guarded duplicate-key purge tool").splitlines()[0]
    )
    parser.add_argument("--redis-url", default=None, help="Redis URL (default: REDIS_URL env)")
    parser.add_argument(
        "--confirm",
        metavar="TOKEN",
        default=None,
        help=f"must equal {CONFIRM_TOKEN} to enable deletion",
    )
    parser.add_argument(
        "--owner-approval", default=None, help="owner authorization reference (ticket/approval id)"
    )
    parser.add_argument(
        "--inject-client-module", default=None, help=argparse.SUPPRESS
    )  # test seam: 'module:attr' exposing a client factory
    args = parser.parse_args(argv)

    if args.confirm is not None and args.confirm != CONFIRM_TOKEN:
        print(f"refusing: --confirm token did not match {CONFIRM_TOKEN}")
        return EXIT_MISUSE
    if args.confirm is not None and not args.owner_approval:
        print("refusing: deletion requires --owner-approval alongside --confirm")
        return EXIT_MISUSE

    if args.inject_client_module:
        import importlib

        module_name, attr = args.inject_client_module.split(":", 1)
        client = getattr(importlib.import_module(module_name), attr)()
    else:
        try:
            import redis  # type: ignore
        except ImportError:
            print("redis package unavailable; supply a client via test seam")
            return EXIT_MISUSE
        url = args.redis_url
        import os

        url = url or os.environ.get("REDIS_URL")
        if not url:
            print("no --redis-url or REDIS_URL provided")
            return EXIT_MISUSE
        client = redis.Redis.from_url(url, decode_responses=False)

    if args.confirm is not None:
        assert args.owner_approval is not None
        return run_delete(client, args.owner_approval)
    return run_discovery(client)


if __name__ == "__main__":
    sys.exit(main())
