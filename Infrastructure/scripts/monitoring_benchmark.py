#!/usr/bin/env python3
"""Guarded CLI for measuring the read-only monitoring-service API."""

from __future__ import annotations

import argparse
import asyncio  # noqa: ANYIO_OK - the required CLI contract specifies asyncio.Semaphore.
import ipaddress
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from Infrastructure.scripts.monitoring_benchmark_core import (
    TARGETS,
    WINDOW_HOURS,
    BenchmarkConfig,
    nearest_rank_percentile,
    sample,
    soak,
)

__all__: Final = ("nearest_rank_percentile", "validate_base_url", "validate_options")
SECRET_HEADER = re.compile(r"(authorization|api[-_]?key|password|token)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class GuardError(Exception):
    """A preflight refusal that must occur before any network I/O."""

    message: str

    def __str__(self) -> str:
        return self.message


def build_parser() -> argparse.ArgumentParser:
    """Build the explicit, read-only benchmark command-line interface."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", required=True, help="http(s) service origin; no path or credentials"
    )
    parser.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="exact non-local host permitted for this run",
    )
    parser.add_argument("--location", required=True)
    parser.add_argument("--node", default="front")
    parser.add_argument("--target", action="append", choices=TARGETS, default=[])
    parser.add_argument("--window", choices=tuple(WINDOW_HOURS), default="7d")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--method", default="GET", help=argparse.SUPPRESS)
    parser.add_argument("--header", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--soak", action="store_true")
    parser.add_argument("--soak-viewers", type=int, default=8)
    parser.add_argument("--soak-seconds", type=float, default=600.0)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def validate_base_url(base_url: str, allow_hosts: list[str]) -> str:
    """Allow only explicit local/private origins or an exact caller-approved host."""
    parsed = urlsplit(base_url)
    hostname = parsed.hostname
    if parsed.scheme not in {"http", "https"} or hostname is None:
        raise GuardError("base URL must be an http(s) URL with a hostname")
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise GuardError("base URL must be an origin without credentials, path, query, or fragment")
    try:
        is_private = ipaddress.ip_address(hostname).is_private
    except ValueError:
        is_private = hostname == "localhost"
    if not (is_private or hostname in allow_hosts):
        raise GuardError(
            "base URL host must be localhost, private LAN, or exactly named by --allow-host"
        )
    return hostname


def validate_options(arguments: argparse.Namespace) -> BenchmarkConfig:
    """Parse all untrusted CLI values before opening a connection."""
    if arguments.method.upper() != "GET":
        raise GuardError("only GET requests are permitted")
    if arguments.samples < 1 or arguments.warmup < 0 or arguments.timeout <= 0:
        raise GuardError("samples must be >=1, warmup must be >=0, and timeout must be >0")
    if arguments.soak and (arguments.soak_viewers < 1 or arguments.soak_seconds <= 0):
        raise GuardError("soak viewers must be >=1 and soak seconds must be >0")
    for header in arguments.header:
        if SECRET_HEADER.search(header):
            raise GuardError("credential-bearing headers are refused")
        raise GuardError("custom headers are not supported")
    validate_base_url(arguments.base_url, arguments.allow_host)
    return BenchmarkConfig(
        base_url=arguments.base_url.rstrip("/"),
        location=arguments.location,
        node=arguments.node,
        targets=tuple(arguments.target) or TARGETS,
        window=arguments.window,
        samples=arguments.samples,
        warmup=arguments.warmup,
        timeout_seconds=arguments.timeout,
    )


async def execute(
    config: BenchmarkConfig, arguments: argparse.Namespace, host: str
) -> dict[str, object]:
    """Produce the stable benchmark document for one sampling or soak invocation."""
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "base_url_host": host,
        "targets": list(config.targets),
        "samples_per_target": config.samples,
        "warmup": config.warmup,
        "results": [],
    }
    if arguments.soak:
        report["soak"] = await soak(config, arguments.soak_viewers, arguments.soak_seconds)
    else:
        report["results"] = await sample(config)
    return report


def human_summary(report: dict[str, object]) -> str:
    """Render a concise human summary while preserving JSON as the source of truth."""
    soak_report = report.get("soak")
    if isinstance(soak_report, dict):
        aggregate = soak_report["aggregate"]
        assert isinstance(aggregate, dict)
        return (
            f"soak viewers={soak_report['viewers']} seconds={soak_report['seconds']} "
            f"max_in_flight={aggregate['max_concurrent_in_flight']} "
            f"status={aggregate['status_counts']} errors={aggregate['error_counts']}"
        )
    results = report["results"]
    assert isinstance(results, list)
    lines = [f"samples={report['samples_per_target']} warmup={report['warmup']}"]
    for result in results:
        assert isinstance(result, dict)
        latency = result["latency_ms"]
        assert isinstance(latency, dict)
        lines.append(
            f"{result['endpoint']}: p95={latency['p95']}ms status={result['status_counts']} errors={result['error_counts']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Validate before I/O, run one safe benchmark mode, and save its report."""
    arguments = build_parser().parse_args(argv)
    try:
        config = validate_options(arguments)
        host = validate_base_url(config.base_url, arguments.allow_host)
    except GuardError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    report = asyncio.run(execute(config, arguments, host))
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(human_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
